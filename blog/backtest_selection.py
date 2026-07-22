#!/usr/bin/env python3
"""
backtest_selection.py — replay historical article-idea CSVs through the CURRENT
selection gate and a PROPOSED dynamic-count gate, then diff the outcomes.

STRICTLY READ-ONLY: reads article_queue_*.csv, imports daily_article_queue only
for its pure selection helpers and shared constants. Never queues, never writes
to the pipeline, never touches Redis.

Usage:
  backtest_selection.py --data-dir /home/flask/blog/_backtest_data [--since 2026-05-01] [--verbose]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import glob
import io
import os
import statistics
import sys
from collections import Counter

sys.path.insert(0, '/home/flask/blog')
sys.path.insert(0, '/home/flask')

import daily_article_queue as daq   # real logic + real constants

# ------------------------------------------------------------------
# PROPOSED gate tunables (the things we are here to calibrate)
# ------------------------------------------------------------------
NEW_MIN_SCORE      = 6.0    # raised floor (was 5.0; file calls 5-6 merely "decent")
CLIFF_DROP         = 2.0    # drop anything below best_score - CLIFF_DROP
CLIFF_CAP          = 7.0    # the cliff may never push the bar above this. A standout
                            # idea must not suppress objectively-decent ones.
MAX_DAYS_NEWSLESS  = 10     # pattern-only ideas must start within this many days
MAX_DAYS_ANY       = 30     # hard horizon for anything
MAX_PER_DAY        = 6      # cap (was the fixed target)
SOFT_MIN           = 2      # backfill to this many if quality gate yields fewer
LEGACY_FLOOR       = 5.0    # backfill may relax only to here
RVOL_PEG           = 1.5    # rvol at/above this counts as a news peg


def _quiet(fn, *a, **kw):
    """Run a chatty daq function without its stdout noise."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*a, **kw)


def days_to_start(row, pub_date):
    try:
        d = datetime.date.fromisoformat(str(row['pat_start_date'])[:10])
        return (d - pub_date).days
    except Exception:
        return None


def has_news_peg(row):
    if str(row.get('in_news', '0')).strip() == '1':
        return True
    if (row.get('earnings_type') or '').strip():
        return True
    try:
        if float(row.get('rvol') or 0) >= RVOL_PEG:
            return True
    except (TypeError, ValueError):
        pass
    return False


def has_required_fields(row):
    required = ['pat_resource_id', 'ticker', 'pat_start_date',
                'pat_days', 'pat_years', 'pat_direction']
    return all(str(row.get(f, '')).strip() for f in required)


# ------------------------------------------------------------------
# CURRENT behaviour — delegate to the real code for exact fidelity
# ------------------------------------------------------------------
def current_selection(rows, pub_date):
    orig = daq.TODAY
    try:
        daq.TODAY = pub_date                      # featured_score reads this
        eligible = _quiet(daq.filter_ideas, rows)
        lineup   = _quiet(daq.select_diverse_lineup, eligible, daq.ARTICLES_PER_DAY)
        return eligible, lineup
    finally:
        daq.TODAY = orig


# ------------------------------------------------------------------
# PROPOSED behaviour — quality+timeliness gates, floating count,
# diversity as a preference among the already-qualified.
# ------------------------------------------------------------------
class _Caps:
    """Replicates daq's diversity caps, reusing its constants."""

    def __init__(self):
        self.used = set()
        self.counts = {}

    def can_add(self, row):
        t = row['ticker'].upper()
        if t in self.used:
            return False, 'duplicate'
        if (row.get('asset_type', '').lower() == 'index'
                and self.counts.get('__index__', 0) >= daq.MAX_INDICES):
            return False, 'index_cap'
        sec = daq.SECTOR_MAP.get(t, 'other')
        if sec != 'other' and self.counts.get(sec, 0) >= daq.MAX_PER_SECTOR:
            return False, 'sector_cap'
        grp = daq.CORRELATED_GROUPS.get(t)
        if grp and self.counts.get(f'__corr_{grp}__', 0) >= 1:
            return False, 'correlated_cap'
        return True, None

    def add(self, row):
        t = row['ticker'].upper()
        self.used.add(t)
        if row.get('asset_type', '').lower() == 'index':
            self.counts['__index__'] = self.counts.get('__index__', 0) + 1
        sec = daq.SECTOR_MAP.get(t, 'other')
        self.counts[sec] = self.counts.get(sec, 0) + 1
        grp = daq.CORRELATED_GROUPS.get(t)
        if grp:
            self.counts[f'__corr_{grp}__'] = self.counts.get(f'__corr_{grp}__', 0) + 1


def timeliness_ok(row, pub_date):
    dts = days_to_start(row, pub_date)
    if dts is None:
        return False, 'bad_date'
    if dts > MAX_DAYS_ANY:
        return False, 'beyond_horizon'
    if not has_news_peg(row) and dts > MAX_DAYS_NEWSLESS:
        return False, 'far_future_no_peg'
    return True, None


def proposed_selection(rows, pub_date):
    reasons = Counter()

    # Stage A: hard eligibility (fields + timeliness + absolute floor)
    qualified = []
    for r in rows:
        if not has_required_fields(r):
            reasons['missing_fields'] += 1
            continue
        ok, why = timeliness_ok(r, pub_date)
        if not ok:
            reasons[why] += 1
            continue
        if r['score'] < LEGACY_FLOOR:
            reasons['below_legacy_floor'] += 1
            continue
        qualified.append(r)

    if not qualified:
        return [], reasons, False

    qualified.sort(key=lambda r: r['score'], reverse=True)
    best = qualified[0]['score']
    threshold = min(CLIFF_CAP, max(NEW_MIN_SCORE, best - CLIFF_DROP))

    # Stage B: quality band + diversity caps, count floats
    caps = _Caps()
    lineup = []
    deferred = []
    for r in qualified:
        if len(lineup) >= MAX_PER_DAY:
            reasons['over_cap'] += 1
            continue
        if r['score'] < threshold:
            reasons['below_cliff_or_floor'] += 1
            deferred.append(r)
            continue
        ok, why = caps.can_add(r)
        if not ok:
            reasons[why] += 1
            continue
        caps.add(r)
        lineup.append(r)

    # Stage C: soft-min backfill (logged as a degraded day)
    backfilled = False
    if len(lineup) < SOFT_MIN:
        for r in deferred:
            if len(lineup) >= SOFT_MIN:
                break
            ok, _ = caps.can_add(r)
            if ok:
                caps.add(r)
                lineup.append(r)
                backfilled = True

    return lineup, reasons, backfilled


def fmt(lineup):
    return ','.join(f"{r['ticker']}({r['score']:.1f})" for r in lineup) or '-'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default='/home/flask/blog/article_ideas')
    ap.add_argument('--since', default=None, help='YYYY-MM-DD')
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--quiet', action='store_true', help='summary only, no per-day table')
    # calibration overrides
    ap.add_argument('--floor', type=float)
    ap.add_argument('--cliff', type=float)
    ap.add_argument('--cliff-cap', type=float)
    ap.add_argument('--max', type=int)
    ap.add_argument('--newsless-days', type=int)
    args = ap.parse_args()

    global NEW_MIN_SCORE, CLIFF_DROP, CLIFF_CAP, MAX_PER_DAY, MAX_DAYS_NEWSLESS
    if args.floor is not None:         NEW_MIN_SCORE     = args.floor
    if args.cliff is not None:         CLIFF_DROP        = args.cliff
    if args.cliff_cap is not None:     CLIFF_CAP         = args.cliff_cap
    if args.max is not None:           MAX_PER_DAY       = args.max
    if args.newsless_days is not None: MAX_DAYS_NEWSLESS = args.newsless_days

    files = sorted(glob.glob(os.path.join(args.data_dir, 'article_queue_*.csv')))
    if args.since:
        files = [f for f in files
                 if os.path.basename(f)[14:24] >= args.since]
    if not files:
        print('No CSVs found.'); sys.exit(1)

    print('=' * 108)
    print('SELECTION BACKTEST — current (fill-to-%d) vs proposed (floating count)' % daq.ARTICLES_PER_DAY)
    print(f'  proposed: floor={NEW_MIN_SCORE} cliff=-{CLIFF_DROP} cliff_cap={CLIFF_CAP} '
          f'newsless<={MAX_DAYS_NEWSLESS}d any<={MAX_DAYS_ANY}d '
          f'cap={MAX_PER_DAY} soft_min={SOFT_MIN}')
    print(f'  files: {len(files)}   ({os.path.basename(files[0])[14:24]} → {os.path.basename(files[-1])[14:24]})')
    print('=' * 108)
    if not args.quiet:
        print(f'{"date":<12} {"cand":>4} {"elig":>4} {"cur":>3} {"new":>3} {"Δ":>3}  {"bf":<3} current → proposed')
        print('-' * 108)

    cur_counts, new_counts = Counter(), Counter()
    cur_scores, new_scores = [], []
    cur_far, new_far = 0, 0
    all_reasons = Counter()
    backfill_days = 0
    rows_out = []

    for path in files:
        date_s = os.path.basename(path)[14:24]
        try:
            pub_date = datetime.date.fromisoformat(date_s)
        except ValueError:
            continue
        rows = _quiet(daq.read_ideas, path)
        if not rows:
            continue

        eligible, cur = current_selection(rows, pub_date)
        new, reasons, backfilled = proposed_selection(rows, pub_date)
        all_reasons.update(reasons)
        if backfilled:
            backfill_days += 1

        cur_counts[len(cur)] += 1
        new_counts[len(new)] += 1
        cur_scores += [r['score'] for r in cur]
        new_scores += [r['score'] for r in new]

        for r in cur:
            dts = days_to_start(r, pub_date)
            if dts is not None and dts > MAX_DAYS_NEWSLESS and not has_news_peg(r):
                cur_far += 1
        for r in new:
            dts = days_to_start(r, pub_date)
            if dts is not None and dts > MAX_DAYS_NEWSLESS and not has_news_peg(r):
                new_far += 1

        delta = len(new) - len(cur)
        if not args.quiet:
            print(f'{date_s:<12} {len(rows):>4} {len(eligible):>4} {len(cur):>3} {len(new):>3} '
                  f'{delta:>+3}  {"BF" if backfilled else "":<3} {fmt(cur)}  →  {fmt(new)}')
        rows_out.append((date_s, len(cur), len(new)))

    def dist(c):
        return '  '.join(f'{k}:{c[k]}' for k in sorted(c))

    print('-' * 108)
    print('\nDAILY COUNT DISTRIBUTION (articles published that day : number of days)')
    print(f'  current : {dist(cur_counts)}')
    print(f'  proposed: {dist(new_counts)}')
    print(f'\nTOTAL ARTICLES   current={sum(cur_counts[k]*k for k in cur_counts)}  '
          f'proposed={sum(new_counts[k]*k for k in new_counts)}')
    if cur_scores and new_scores:
        print(f'MEAN PUBLISHED SCORE   current={statistics.mean(cur_scores):.2f}  '
              f'proposed={statistics.mean(new_scores):.2f}')
        print(f'MIN PUBLISHED SCORE    current={min(cur_scores):.1f}  '
              f'proposed={min(new_scores):.1f}')
    print(f'FAR-FUTURE, NO NEWS PEG (>{MAX_DAYS_NEWSLESS}d)   current={cur_far}  proposed={new_far}')
    print(f'DAYS NEEDING BACKFILL  {backfill_days} / {len(rows_out)}')
    print('\nPROPOSED-GATE EXCLUSION REASONS (all days)')
    for k, v in all_reasons.most_common():
        print(f'  {v:>5}  {k}')


if __name__ == '__main__':
    main()
