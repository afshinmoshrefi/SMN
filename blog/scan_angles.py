#!/usr/bin/env python3
"""
scan_angles.py — how many ELIGIBLE story cells does each symbol have today?

Logs in ONCE and reuses the token (the /login/api endpoint is IP-rate-limited
to 10/min, so per-symbol CLI invocations trip 429 immediately).

Usage:
  scan_angles.py --anchor 2026-07-21 SYM:RID [SYM:RID ...]
"""
from __future__ import annotations

import argparse
import datetime
import sys
import time

sys.path.insert(0, '/home/flask/blog')
sys.path.insert(0, '/home/flask')

import angle_engine as ae


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pairs', nargs='+', help='SYMBOL:RESOURCE_ID')
    ap.add_argument('--anchor', default=datetime.date.today().isoformat())
    ap.add_argument('--sleep', type=float, default=0.4)
    args = ap.parse_args()

    token = ae.login_appserver()
    print(f'anchor={args.anchor}   (single shared token)\n')
    print(f'{"symbol":<8}{"rid":>4}  {"angle":<12}{"elig":>5}{"alt":>6}{"cells":>6}   story cell')
    print('-' * 78)

    results = []
    for pair in args.pairs:
        sym, _, rid = pair.partition(':')
        sym, rid = sym.strip().upper(), (rid.strip() or '2')
        try:
            a = ae.analyze(rid, sym, args.anchor, token=token)
        except Exception as e:
            print(f'{sym:<8}{rid:>4}  {"ERROR":<14}{"":>5}{"":>6}   {e}')
            continue

        card = a.get('card') or {}
        cells = a.get('cells') or []
        cands = a.get('candidates') or []
        raw_angle = card.get('angle')
        if isinstance(raw_angle, dict):
            angle = raw_angle.get('name') or 'no_story'
            runners = raw_angle.get('runner_up') or []
        else:
            angle, runners = (raw_angle or 'no_story'), []
        story = card.get('story_cell') or {}
        story_key = story.get('key') or (
            f"{story.get('horizon_tag','')}x{story.get('years','')}" if story else '')
        # A runner-up on a DIFFERENT cell = a genuinely distinct alternate story.
        distinct = sum(1 for r in runners
                       if str(r.get('cell', '')).split('_', 2)[-1] not in str(story_key))
        detail = (f"{story.get('horizon_tag','')}x{story.get('years','')} "
                  f"{story.get('record','')}") if story else ''
        print(f'{sym:<8}{rid:>4}  {angle:<12}{len(cands):>5}{distinct:>6}{len(cells):>6}   {detail[:30]}')
        results.append((sym, rid, angle, len(cands), distinct))
        time.sleep(args.sleep)

    print('-' * 78)
    from collections import Counter
    dist = Counter(r[2] for r in results)
    total = len(results)
    print(f'\nANGLE DISTRIBUTION  (n={total})')
    for name, n in dist.most_common():
        bar = '#' * int(40 * n / max(total, 1))
        print(f'  {name:<12}{n:>4}  {100*n/max(total,1):>5.1f}%  {bar}')
    multi = [r for r in results if r[4] >= 2]
    print(f'\nsymbols with >=2 DISTINCT alternate cells (3-angle demo): {len(multi)}')
    for sym, rid, angle, n, d in multi:
        print(f'   {sym} (rid {rid})  angle={angle}  distinct_alts={d}')


if __name__ == '__main__':
    main()
