"""angle_engine.py — Seasonal matrix, cell scoring, and angle selection (Phase 1).

Implements the angle engine from ANGLE_ENGINE_DESIGN.md:

  1. MATRIX   — for a symbol anchored at a date, fetch ChartData4 for every
                horizon x lookback cell (30/60/90 days x 10/15/20 consecutive
                years + the current-phase PE slice), plus any detected-pattern
                candidates passed in by the caller.
  2. SCORING  — per cell, derive direction-neutral metrics from per-year nets
                (n, up/down counts, median, best/worst, MFE/MAE medians) and
                score conviction (symmetric binomial tail) and tension
                (disagreement with news/price context). Deterministic; no LLM.
  3. ANGLE    — pick the story cell and one of six angles by deterministic
                predicates: COLLISION, TAILWIND, CLOCKWORK, FORK, REGIME,
                QUIET_EDGE. Emit the Angle Card consumed by the PLAN prompt
                (Phase 2) and logged to the audit trail.

Direction semantics are DERIVED from per-year net returns, never from the
API's Trade Dir field, so the engine is robust to whatever ChartData4 reports
for arbitrary (non-detected) windows. The verbatim stats block is carried on
each cell for publication surfaces; a mismatch between derived counts and the
stats block sets Cell.stats_mismatch for review instead of crashing.

Standalone by design: stdlib + requests + config only. No matplotlib, no
AI_tools, no redis. Offline use (tests, local dev) is supported via fixture
files; network functions require config + SERVICE_API_KEY (TW2 v5 auth).

CLI:
  python3 angle_engine.py AVGO --resource 2 --news-direction bullish \
      --news-headline "Broadcom pops 9% on AI deal" --news-date 2026-07-19
  python3 angle_engine.py AAPL --offline-fixture tests/fixtures/aapl.json
  python3 angle_engine.py AAPL --record-fixture /tmp/aapl_fixture.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests  # required for network mode only; fixtures work without it
except Exception:
    requests = None

sys.path.insert(0, '/home/flask')
try:
    import config  # exposes appserver_url; absent on offline/dev-local machines
except Exception:
    config = None

# ============================================================
# TUNABLES — one block, snapshotted into every Angle Card
# ============================================================
TUNABLES = {
    # matrix shape
    "horizons_days": (30, 60, 90),
    "cons_lookbacks": ("10", "15", "20"),
    "pe_lookback": "10",            # current-phase slice: pe{phase}-10
    # eligibility floors (mirror the ladder floors in select_news_articles)
    "min_n_cons": 8,
    "min_n_pe": 6,
    # conviction thresholds (binomial tail, p=0.5, dominant side)
    "tail_striking": 0.06,          # n=10, 8-of-10 -> 0.0547 qualifies
    "tail_extreme": 0.015,          # n=10, 9-of-10 -> 0.0107 qualifies
    "tail_magnitude": 0.12,         # softer tail allowed when |median| is large
    "median_magnitude_pct": 3.0,
    # angle predicates
    "clockwork_min_rate": 0.85,
    "clockwork_min_n": 10,
    "fork_min_horizon_gap_days": 30,
    "fork_min_lookback_gap_years": 5,
    "fork_max_surprise_ratio": 3.0,   # balance check on the log-surprise scale
    "regime_dominance": 1.25,         # pe surprise must be 1.25x best cons surprise
    "tension_boost": 0.75,
    "news_fresh_days": 7,
    "price_momentum_min_abs_pct": 3.0,
}

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "cache", "angle_matrix")

# Reserved integrity-gate stat labels. Quotables must never contain these so
# auxiliary-cell numbers cannot collide with the primary evidence bundle's
# labeled-stat scan in integrity_gate.py.
RESERVED_LABELS = ("Avg Profit", "Percent Profitable", "Median Profit",
                   "Num Winners", "Num Losers", "win rate", "Sharpe Ratio",
                   "Std Dev", "TradeWave Ratio")


# ============================================================
# APPSERVER AUTH (TW2 v5 — SERVICE_API_KEY via POST /login/api)
# ============================================================

def _appserver_url() -> str:
    if requests is None:
        raise RuntimeError("the 'requests' package is unavailable — network mode "
                           "needs it (use --offline-fixture elsewhere)")
    if config is None or not getattr(config, "appserver_url", ""):
        raise RuntimeError("config.appserver_url unavailable — network mode "
                           "requires /home/flask/config.py (use --offline-fixture "
                           "elsewhere)")
    return config.appserver_url.rstrip('/')


def login_appserver() -> str:
    """Authenticate with the TW2 appserver and return a JWT.

    Key resolution order matches the workflow: config.SERVICE_API_KEY (each
    box's config decides how it is populated) then the process environment
    (systemd-injected for services; `set -a; . /etc/tradewave/secrets.env`
    for ad-hoc shells)."""
    api_key = (getattr(config, 'SERVICE_API_KEY', '') if config else '') \
        or os.environ.get('SERVICE_API_KEY', '')
    if not api_key:
        raise RuntimeError('SERVICE_API_KEY not set (config.SERVICE_API_KEY or env). '
                           'SMN services authenticate via POST /login/api with '
                           'X-Service-Key.')
    url = f'{_appserver_url()}/login/api'
    headers = {'X-Service-Key': api_key}
    result = requests.post(url, headers=headers, timeout=15).json()
    if 'token' not in result:
        time.sleep(5)
        result = requests.post(url, headers=headers, timeout=15).json()
        if 'token' not in result:
            raise RuntimeError('appserver login failed (no token in response)')
    return result['token']


# ============================================================
# CELL FETCH (ChartData4, arbitrary windows) + disk cache
# ============================================================

def _cell_key(resource_id: Any, symbol: str, anchor: str, days: int, years: str) -> str:
    return f"{resource_id}_{symbol}_{anchor}_{days}_{years}"


def fetch_cell_raw(token: str, resource_id: Any, symbol: str, anchor: str,
                   days: int, years: str,
                   cache: bool = True) -> Optional[Dict[str, Any]]:
    """One matrix cell = one ChartData4 call.

    NOTE the -1 convention: the API's daysOut parameter is window_days - 1
    (same as get_opp_data in article_prompt.py).

    Returns the raw API dict, or None when the combo is unavailable.
    Disk-cached by (resource, symbol, anchor, days, years); the anchor date in
    the key gives natural daily invalidation.
    """
    key = _cell_key(resource_id, symbol, anchor, days, years)
    cache_path = os.path.join(CACHE_DIR, key + ".json")
    if cache and os.path.isfile(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass

    days_out = str(int(days) - 1)
    url = (f'{_appserver_url()}/ChartData4/{resource_id}/{anchor}/{symbol}/'
           f'{days_out}/{years}?token={token}')
    try:
        data = requests.get(url, timeout=30).json()
    except Exception as exc:
        print(f'[angle_engine] fetch failed {key}: {exc}')
        return None
    if not isinstance(data, dict) or 'ChartData4' not in data:
        return None

    if cache:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = cache_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(data, fh)
            os.replace(tmp, cache_path)
        except Exception:
            pass
    return data


# ============================================================
# CELL DERIVATION — direction-neutral metrics from per-year nets
# ============================================================

@dataclass
class Cell:
    resource_id: str
    symbol: str
    anchor_date: str
    days: int
    years: str                    # lookback code: '15' or 'pe2-10'
    mode: str                     # 'cons' | 'pe'
    horizon_tag: str              # '30d' | '60d' | '90d' | 'detected'
    n: int = 0
    up_years: int = 0
    down_years: int = 0
    flat_years: int = 0
    direction: str = "flat"       # 'bullish' | 'bearish' | 'flat' (majority sign)
    median_net: float = 0.0
    avg_net: float = 0.0
    best_year: int = 0
    best_net: float = 0.0
    worst_year: int = 0
    worst_net: float = 0.0
    median_mfe: float = 0.0
    median_mae: float = 0.0
    per_year: List[Dict[str, Any]] = field(default_factory=list)
    stats_raw: Dict[str, Any] = field(default_factory=dict)
    stats_mismatch: bool = False
    notes: List[str] = field(default_factory=list)
    # scoring (filled by score_cells)
    tail_p: float = 1.0
    conviction: float = 0.0
    tension: float = 0.0
    story_score: float = 0.0
    eligible: bool = False
    ineligible_reason: str = ""

    def key(self) -> str:
        return _cell_key(self.resource_id, self.symbol, self.anchor_date,
                         self.days, self.years)


def _mode_for_years(years: str) -> str:
    return 'pe' if str(years).lower().startswith('pe') else 'cons'


def _median(vals: List[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def derive_cell(raw: Dict[str, Any], *, resource_id: Any, symbol: str,
                anchor: str, days: int, years: str, horizon_tag: str,
                today: Optional[datetime.date] = None) -> Optional[Cell]:
    """Build a Cell from a raw ChartData4 response.

    Per-year rows are the authority for direction counts. Rows are excluded
    when all-zero (the API/client zeroes incomplete years) or when they are
    the current calendar year and the window has not completed yet — matching
    how the production pipeline treats the current year (zero_last_year=True).
    """
    if not isinstance(raw, dict):
        return None
    rows = raw.get('ChartData4') or []
    stats = raw.get('stats') or {}
    today = today or datetime.date.today()

    try:
        anchor_d = datetime.date.fromisoformat(anchor)
    except ValueError:
        return None
    window_complete_now = (anchor_d + datetime.timedelta(days=int(days))) <= today

    per_year: List[Dict[str, Any]] = []
    for r in rows:
        try:
            year = int(r.get('year'))
            net_s, mfe_s, mae_s = [s.strip() for s in str(r.get('pct', '0,0,0')).split(',')]
            net, mfe, mae = float(net_s), float(mfe_s), float(mae_s)
        except Exception:
            continue
        if net == 0.0 and mfe == 0.0 and mae == 0.0:
            continue                      # zeroed/empty year
        if year == today.year and not window_complete_now:
            continue                      # incomplete current-year window
        per_year.append({'year': year, 'net': net, 'mfe': mfe, 'mae': mae})

    per_year.sort(key=lambda x: x['year'])
    if not per_year:
        return None

    nets = [p['net'] for p in per_year]
    up = sum(1 for v in nets if v > 0)
    down = sum(1 for v in nets if v < 0)
    flat = len(nets) - up - down
    if up > down:
        direction = 'bullish'
    elif down > up:
        direction = 'bearish'
    else:
        med = _median(nets)
        direction = 'bullish' if med > 0 else ('bearish' if med < 0 else 'flat')

    best = max(per_year, key=lambda p: p['net'])
    worst = min(per_year, key=lambda p: p['net'])

    cell = Cell(
        resource_id=str(resource_id), symbol=symbol.upper(), anchor_date=anchor,
        days=int(days), years=str(years), mode=_mode_for_years(years),
        horizon_tag=horizon_tag,
        n=len(per_year), up_years=up, down_years=down, flat_years=flat,
        direction=direction,
        median_net=round(_median(nets), 2),
        avg_net=round(sum(nets) / len(nets), 2),
        best_year=best['year'], best_net=best['net'],
        worst_year=worst['year'], worst_net=worst['net'],
        median_mfe=round(_median([p['mfe'] for p in per_year]), 2),
        median_mae=round(_median([p['mae'] for p in per_year]), 2),
        per_year=per_year, stats_raw=stats,
    )

    # Cross-check derived counts against the API's own winner/loser stats.
    # Informative only: sets a flag for review, never blocks Phase 1 output.
    try:
        api_w = int(str(stats.get('Num Winners', '')).replace(',', ''))
        api_l = int(str(stats.get('Num Losers', '')).replace(',', ''))
        api_dir = str(stats.get('Trade Dir', '')).strip().lower()
        derived = (down, up) if api_dir == 'short' else (up, down)
        if (api_w, api_l) != derived:
            cell.stats_mismatch = True
            cell.notes.append(
                f"stats block reports {api_w}W/{api_l}L (Trade Dir={api_dir or '?'}) "
                f"vs derived {up} up / {down} down")
    except Exception:
        cell.notes.append('stats block missing winner/loser counts')

    return cell


# ============================================================
# SCORING — conviction (binomial tail) and tension (context)
# ============================================================

def binom_tail(n: int, k: int) -> float:
    """P(X >= k) for X ~ Binomial(n, 0.5). Exact; n is small (<= ~30)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.comb(n, i) for i in range(k, n + 1)) / (2 ** n)


def surprise(tail_p: float) -> float:
    """Log-surprise: -log10(tail). The common conviction currency for all
    angle scores. A 1-tail cutoff of 0.06 is ~1.22; 0.015 is ~1.82; 10-of-10
    is ~3.0. Log scale keeps REGIME dominance and FORK balance ratios
    meaningful (a plain 1-tail scale saturates at 1.0 and ratios collapse)."""
    return -math.log10(max(float(tail_p), 1e-12))


def infer_context_direction(news_direction: Optional[str],
                            one_month_return: Optional[float],
                            t: Dict[str, Any] = TUNABLES) -> Tuple[int, str]:
    """Context direction the story cell can agree or collide with.

    News direction (when the caller provides it) dominates; otherwise price
    momentum (1M return beyond +/- price_momentum_min_abs_pct); otherwise 0.
    Returns (dir in {+1,-1,0}, source in {'news','price','none'}).
    """
    nd = (news_direction or '').strip().lower()
    if nd in ('bullish', 'up', 'positive'):
        return 1, 'news'
    if nd in ('bearish', 'down', 'negative'):
        return -1, 'news'
    if one_month_return is not None:
        try:
            r = float(one_month_return)
            if abs(r) >= t['price_momentum_min_abs_pct']:
                return (1 if r > 0 else -1), 'price'
        except (TypeError, ValueError):
            pass
    return 0, 'none'


def score_cells(cells: List[Cell], ctx_dir: int,
                t: Dict[str, Any] = TUNABLES) -> None:
    """Fill tail_p / conviction / tension / story_score / eligibility in place.

    conviction is on the log-surprise scale (see surprise()), not 1-tail.
    """
    for c in cells:
        dominant = max(c.up_years, c.down_years)
        c.tail_p = round(binom_tail(c.n, dominant), 5)
        c.conviction = round(surprise(c.tail_p), 4)

        cell_dir = 1 if c.direction == 'bullish' else (-1 if c.direction == 'bearish' else 0)
        c.tension = 1.0 if (ctx_dir != 0 and cell_dir != 0 and cell_dir == -ctx_dir) else 0.0
        c.story_score = round(c.conviction * (1.0 + t['tension_boost'] * c.tension), 5)

        min_n = t['min_n_pe'] if c.mode == 'pe' else t['min_n_cons']
        if c.n < min_n:
            c.eligible, c.ineligible_reason = False, f'n={c.n} below {c.mode} floor {min_n}'
        elif c.direction == 'flat':
            c.eligible, c.ineligible_reason = False, 'no directional majority'
        elif c.tail_p <= t['tail_striking']:
            c.eligible = True
        elif c.tail_p <= t['tail_magnitude'] and abs(c.median_net) >= t['median_magnitude_pct']:
            c.eligible = True
        else:
            c.eligible, c.ineligible_reason = False, (
                f'coin-flip: tail {c.tail_p:.3f} > {t["tail_striking"]} and '
                f'|median| {abs(c.median_net):.1f}% < {t["median_magnitude_pct"]}%')


# ============================================================
# ANGLE SELECTION — six deterministic predicates
# ============================================================

@dataclass
class AngleDecision:
    angle: str
    score: float
    story_cell_key: str
    counter_cell_key: str = ""     # FORK only
    rationale: str = ""
    flavors: List[str] = field(default_factory=list)


def _is_extreme(c: Cell, t: Dict[str, Any]) -> bool:
    dominant = max(c.up_years, c.down_years)
    rate = dominant / c.n if c.n else 0.0
    if c.tail_p <= t['tail_extreme']:
        return True
    if rate >= t['clockwork_min_rate'] and c.n >= t['clockwork_min_n']:
        return True
    return dominant == c.n and c.n >= 8       # n-of-n streak


def _fork_pair(cells: List[Cell], t: Dict[str, Any]) -> Optional[Tuple[Cell, Cell]]:
    """Best genuinely-balanced opposite-sign pair on a clean comparison axis.

    PE cells are excluded: a PE-vs-consecutive sign flip is REGIME material,
    not a fork the reader can act on. Axis = horizons >= 30 days apart, or
    same horizon with lookbacks >= 5 years apart.
    """
    elig = [c for c in cells if c.eligible and c.mode == 'cons']
    best: Optional[Tuple[Cell, Cell]] = None
    best_score = 0.0
    for a in elig:
        for b in elig:
            if a.direction != 'bullish' or b.direction != 'bearish':
                continue
            horizon_gap = abs(a.days - b.days)
            try:
                lookback_gap = abs(int(a.years) - int(b.years))
            except ValueError:
                lookback_gap = 0
            if horizon_gap < t['fork_min_horizon_gap_days'] and \
               lookback_gap < t['fork_min_lookback_gap_years']:
                continue
            ratio = max(a.conviction, b.conviction) / max(min(a.conviction, b.conviction), 1e-9)
            if ratio > t['fork_max_surprise_ratio']:
                continue                       # one side dominates: not a fork
            pair_score = min(a.conviction, b.conviction)
            if pair_score > best_score:
                best_score, best = pair_score, (a, b)
    return best


def _detect_flavors(story: Cell) -> List[str]:
    flavors: List[str] = []
    if story.n >= 8 and story.per_year:
        dominant_sign = 1 if story.up_years >= story.down_years else -1
        rate = max(story.up_years, story.down_years) / story.n
        last_net = story.per_year[-1]['net']
        last_sign = 1 if last_net > 0 else (-1 if last_net < 0 else 0)
        if rate >= 0.8 and last_sign == -dominant_sign:
            flavors.append('streak_on_the_line')
    return flavors


def select_angle(cells: List[Cell], *, ctx_dir: int, ctx_source: str,
                 news_fresh: bool,
                 t: Dict[str, Any] = TUNABLES) -> Tuple[Optional[AngleDecision],
                                                        List[AngleDecision]]:
    """Score every eligible angle; return (winner, all candidates ranked).

    The runner-up list is the PLAN step's veto fallback: if the writer's plan
    declares the assigned angle infeasible given research, the pipeline falls
    back to the next candidate — once, never more.

    Ties break by insertion order (Python's sort is stable), which encodes
    specificity: FORK > REGIME > CLOCKWORK > COLLISION > TAILWIND > QUIET_EDGE.
    """
    eligible = [c for c in cells if c.eligible]
    if not eligible:
        return None, []

    best = max(eligible, key=lambda c: (c.story_score, c.n, abs(c.median_net)))
    candidates: List[AngleDecision] = []

    pair = _fork_pair(cells, t)
    if pair:
        bull, bear = pair
        primary, counter = (bull, bear) if bull.days <= bear.days else (bear, bull)
        candidates.append(AngleDecision(
            'FORK', round(min(bull.conviction, bear.conviction) * 1.1, 5),
            primary.key(), counter.key(),
            f'{bull.horizon_tag}/{bull.years}y bullish vs '
            f'{bear.horizon_tag}/{bear.years}y bearish; primary = shorter horizon'))

    cons_elig = [c for c in eligible if c.mode == 'cons']
    pe_elig = [c for c in eligible if c.mode == 'pe']
    if pe_elig:
        best_pe = max(pe_elig, key=lambda c: c.conviction)
        best_cons_conv = max((c.conviction for c in cons_elig), default=0.0)
        if best_pe.conviction >= t['regime_dominance'] * best_cons_conv:
            candidates.append(AngleDecision(
                'REGIME', round(best_pe.conviction, 5), best_pe.key(),
                rationale=f'PE slice surprise {best_pe.conviction:.2f} dominates '
                          f'best consecutive {best_cons_conv:.2f}'))

    # CLOCKWORK is consecutive-only: an extreme PE streak is a REGIME piece
    # (the cycle grouping is the frame; the plan can still open stat-first).
    extremes = [c for c in eligible if c.mode == 'cons' and _is_extreme(c, t)]
    if extremes:
        ex = max(extremes, key=lambda c: (c.conviction, c.n))
        candidates.append(AngleDecision(
            'CLOCKWORK', round(ex.conviction * 1.05, 5), ex.key(),
            rationale=f'{max(ex.up_years, ex.down_years)} of {ex.n} one-sided '
                      f'(tail {ex.tail_p:.4f})'))

    if news_fresh and ctx_source == 'news' and ctx_dir != 0:
        opposing = [c for c in eligible if c.tension > 0]
        aligned = [c for c in eligible
                   if c.tension == 0 and c.direction != 'flat']
        if opposing:
            oc = max(opposing, key=lambda c: c.story_score)
            candidates.append(AngleDecision(
                'COLLISION', oc.story_score, oc.key(),
                rationale=f'news {"bullish" if ctx_dir > 0 else "bearish"} vs '
                          f'{oc.direction} history ({oc.horizon_tag}/{oc.years}y)'))
        if aligned:
            ac = max(aligned, key=lambda c: c.conviction)
            candidates.append(AngleDecision(
                'TAILWIND', round(ac.conviction, 5), ac.key(),
                rationale=f'news and {ac.horizon_tag}/{ac.years}y history agree '
                          f'({ac.direction})'))
    elif not news_fresh:
        candidates.append(AngleDecision(
            'QUIET_EDGE', round(best.conviction, 5), best.key(),
            rationale='no fresh news peg; matrix strength alone'))

    if not candidates:
        # Fresh peg but price-only/no context and nothing else fired:
        # the honest fallback is the quiet piece on the best cell.
        candidates.append(AngleDecision(
            'QUIET_EDGE', round(best.conviction, 5), best.key(),
            rationale='no qualifying peg relationship; matrix strength alone'))

    candidates.sort(key=lambda d: d.score, reverse=True)
    winner = candidates[0]
    story = next(c for c in cells if c.key() == winner.story_cell_key)
    winner.flavors = _detect_flavors(story)
    return winner, candidates


# ============================================================
# QUOTABLES — exact strings prose may quote verbatim
# ============================================================

def _fmt_date(d: datetime.date) -> str:
    return f"{d.strftime('%b')} {d.day}, {d.year}"       # 'Sep 27, 2026'


def build_quotables(cell: Cell) -> Dict[str, str]:
    """Server-computed strings with the sample size embedded, so n can never
    be dropped in prose. MUST NOT contain reserved integrity-gate labels
    (see RESERVED_LABELS) — auxiliary-cell quotables would otherwise collide
    with the primary evidence bundle's labeled-stat scan.
    """
    word = 'higher' if cell.direction == 'bullish' else 'lower'
    k = cell.up_years if cell.direction == 'bullish' else cell.down_years
    med = cell.median_net
    med_word = 'gain' if med > 0 else 'loss'
    start = datetime.date.fromisoformat(cell.anchor_date)

    q = {
        "record": f"has closed {word} in {k} of the last {cell.n} years",
        "median": f"a median {med_word} of {abs(med):.1f}% across {cell.n} years",
        "window": f"the {cell.days}-day window beginning {_fmt_date(start)}",
        # Sign-aware: an all-winning cell has no "worst loss", and an
        # all-losing cell has no "best gain". Never let abs() invent one.
        "best_worst": (
            (f"the best year gained {cell.best_net:.1f}% ({cell.best_year})"
             if cell.best_net > 0 else
             f"the best year still lost {abs(cell.best_net):.1f}% ({cell.best_year})")
            + "; " +
            (f"the worst lost {abs(cell.worst_net):.1f}% ({cell.worst_year})"
             if cell.worst_net < 0 else
             f"even the weakest year gained {cell.worst_net:.1f}% ({cell.worst_year})")
        ),
    }
    if k == cell.n and cell.n >= 8:
        q["streak"] = f"{cell.n} for {cell.n} in this window"
    for label in RESERVED_LABELS:
        for text in q.values():
            assert label.lower() not in text.lower(), \
                f"quotable contains reserved label {label!r}: {text}"
    return q


# ============================================================
# MATRIX BUILD + ANGLE CARD
# ============================================================

def _pe_years_code(anchor: str, t: Dict[str, Any] = TUNABLES) -> str:
    phase = int(anchor[:4]) % 4
    return f"pe{phase}-{t['pe_lookback']}"


def matrix_specs(anchor: str, t: Dict[str, Any] = TUNABLES) -> List[Tuple[int, str, str]]:
    """(days, years_code, horizon_tag) for every standard matrix cell."""
    specs = []
    for days in t['horizons_days']:
        for yrs in t['cons_lookbacks']:
            specs.append((days, yrs, f'{days}d'))
        specs.append((days, _pe_years_code(anchor, t), f'{days}d'))
    return specs


def build_matrix(resource_id: Any, symbol: str, anchor: str,
                 detected: Optional[List[Dict[str, Any]]] = None,
                 token: Optional[str] = None,
                 fixture: Optional[Dict[str, Any]] = None,
                 record: Optional[Dict[str, Any]] = None,
                 t: Dict[str, Any] = TUNABLES) -> List[Cell]:
    """Fetch + derive every cell. `fixture` maps cell keys to raw responses
    for offline runs; `record` (a dict) collects raw responses for fixture
    creation. `detected` rows need start_date/days/years keys (mode optional).
    """
    def get_raw(anchor_: str, days: int, years: str):
        key = _cell_key(resource_id, symbol, anchor_, days, years)
        if fixture is not None:
            return fixture.get(key)
        raw = fetch_cell_raw(token, resource_id, symbol, anchor_, days, years)
        if record is not None and raw is not None:
            record[key] = raw
        return raw

    cells: List[Cell] = []
    for days, years, tag in matrix_specs(anchor, t):
        raw = get_raw(anchor, days, years)
        if raw is None:
            continue
        cell = derive_cell(raw, resource_id=resource_id, symbol=symbol,
                           anchor=anchor, days=days, years=years, horizon_tag=tag)
        if cell:
            cells.append(cell)

    for pat in detected or []:
        p_anchor = str(pat.get('start_date', anchor))
        p_days = int(pat.get('days', 0) or 0)
        p_years = str(pat.get('years', ''))
        if not p_days or not p_years:
            continue
        raw = get_raw(p_anchor, p_days, p_years)
        if raw is None:
            continue
        cell = derive_cell(raw, resource_id=resource_id, symbol=symbol,
                           anchor=p_anchor, days=p_days, years=p_years,
                           horizon_tag='detected')
        if cell:
            cells.append(cell)

    # Dedup (a detected pattern can coincide with a grid cell): keep first.
    seen, out = set(), []
    for c in cells:
        if c.key() in seen:
            continue
        seen.add(c.key())
        out.append(c)
    return out


PE_PHASE_NAMES = {"0": "presidential election years", "1": "post-election years",
                  "2": "midterm election years",       "3": "pre-election years"}


def lookback_label(years_code: Any) -> str:
    """Human-readable lookback, e.g. "pe2-10" -> "the last 10 midterm election
    years"; "20" -> "the last 20 years". This is how a reader consumes the
    slice, so it belongs on the CARD (the reviewer's authoritative facts), not
    only in the chrome. Mirrors angle_chrome._pe_label."""
    code = str(years_code).lower().strip()
    m = re.match(r"pe([0-3])-(\d+)$", code)
    if m:
        return f"the last {m.group(2)} {PE_PHASE_NAMES[m.group(1)]}"
    if code.isdigit():
        return f"the last {code} years"
    return f"{years_code} years"


def _cell_public(cell: Cell, with_quotables: bool = True) -> Dict[str, Any]:
    d = asdict(cell)
    # Authoritative human label for the lookback so the editorial reviewer can
    # VERIFY phrases like "midterm election years" instead of flagging them.
    d['lookback_label'] = lookback_label(cell.years)
    if with_quotables:
        d['quotables'] = build_quotables(cell)
    return d


def build_angle_card(*, symbol: str, resource_id: Any, anchor: str,
                     cells: List[Cell], decision: Optional[AngleDecision],
                     candidates: List[AngleDecision],
                     news_headline: str = "", news_date: str = "",
                     news_direction: str = "", news_fresh: bool = False,
                     ctx_dir: int = 0, ctx_source: str = 'none',
                     one_month_return: Optional[float] = None,
                     t: Dict[str, Any] = TUNABLES) -> Dict[str, Any]:
    """The writer's brief: story cell + auxiliary cells + context + quotables.
    Logged to the audit trail; consumed by the PLAN prompt in Phase 2."""
    by_key = {c.key(): c for c in cells}
    card: Dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "symbol": symbol.upper(),
        "resource_id": str(resource_id),
        "anchor_date": anchor,
        "context": {
            "news_headline": news_headline, "news_date": news_date,
            "news_direction": news_direction, "news_fresh": news_fresh,
            "one_month_return": one_month_return,
            "context_direction": {1: 'bullish', -1: 'bearish', 0: 'none'}[ctx_dir],
            "context_source": ctx_source,
        },
        "thresholds": {k: (list(v) if isinstance(v, tuple) else v)
                       for k, v in t.items()},
        "matrix_summary": [
            {"key": c.key(), "horizon": c.horizon_tag, "days": c.days,
             "years": c.years, "mode": c.mode, "n": c.n,
             "record": f"{c.up_years}up/{c.down_years}dn",
             "direction": c.direction, "median_net": c.median_net,
             "tail_p": c.tail_p, "eligible": c.eligible,
             "reason": c.ineligible_reason, "stats_mismatch": c.stats_mismatch}
            for c in sorted(cells, key=lambda c: (c.days, c.years))
        ],
    }
    if decision is None:
        card["angle"] = None
        card["no_story"] = "no eligible cell — ticker should drop for today"
        return card

    story = by_key[decision.story_cell_key]
    card["angle"] = {
        "name": decision.angle, "score": decision.score,
        "rationale": decision.rationale, "flavors": decision.flavors,
        "runner_up": ([{"name": d.angle, "score": d.score, "cell": d.story_cell_key}
                       for d in candidates[1:3]]),
    }
    card["tension_descriptor"] = (
        "contradicts_" + ctx_source if story.tension > 0 else
        ("aligns_with_" + ctx_source if ctx_dir != 0 else "no_context"))
    card["story_cell"] = _cell_public(story)

    aux: List[Dict[str, Any]] = []
    if decision.angle == 'FORK' and decision.counter_cell_key:
        counter = by_key[decision.counter_cell_key]
        aux.append({**_cell_public(counter), "role": "conflicting"})
    else:
        same = [c for c in cells if c.eligible and c.key() != story.key()
                and c.direction == story.direction and c.mode == story.mode]
        if same:
            corr = max(same, key=lambda c: c.conviction)
            aux.append({**_cell_public(corr), "role": "corroborating"})
        opp = [c for c in cells if c.eligible and c.key() != story.key()
               and c.direction not in (story.direction, 'flat')]
        if opp:
            conf = max(opp, key=lambda c: c.conviction)
            aux.append({**_cell_public(conf), "role": "conflicting"})
    card["auxiliary_cells"] = aux[:2]
    return card


# ============================================================
# One-call convenience + CLI
# ============================================================

def analyze(resource_id: Any, symbol: str, anchor: str,
            *, news_headline: str = "", news_date: str = "",
            news_direction: str = "",
            detected: Optional[List[Dict[str, Any]]] = None,
            fixture: Optional[Dict[str, Any]] = None,
            record: Optional[Dict[str, Any]] = None,
            token: Optional[str] = None,
            t: Dict[str, Any] = TUNABLES) -> Dict[str, Any]:
    """Full analysis: {card, cells, candidates, ctx}. The pipeline keeps
    cells+candidates so a PLAN veto can fall back to the runner-up angle
    (fallback_card) without re-fetching the matrix."""
    if fixture is None and token is None:
        token = login_appserver()
    cells = build_matrix(resource_id, symbol, anchor, detected=detected,
                         token=token, fixture=fixture, record=record, t=t)

    one_month = None
    for c in cells:
        v = c.stats_raw.get('1M Return')
        if v not in (None, ''):
            try:
                one_month = float(str(v).replace('%', '').strip())
                break
            except ValueError:
                pass

    news_fresh = False
    if news_date:
        try:
            age = (datetime.date.fromisoformat(anchor)
                   - datetime.date.fromisoformat(news_date)).days
            news_fresh = 0 <= age <= t['news_fresh_days']
        except ValueError:
            pass
    elif news_direction:
        news_fresh = True          # caller asserts a live peg without a date

    ctx_dir, ctx_source = infer_context_direction(
        news_direction if news_fresh else None, one_month, t)

    score_cells(cells, ctx_dir, t)
    decision, candidates = select_angle(
        cells, ctx_dir=ctx_dir, ctx_source=ctx_source, news_fresh=news_fresh, t=t)
    card = build_angle_card(
        symbol=symbol, resource_id=resource_id, anchor=anchor, cells=cells,
        decision=decision, candidates=candidates,
        news_headline=news_headline, news_date=news_date,
        news_direction=news_direction, news_fresh=news_fresh,
        ctx_dir=ctx_dir, ctx_source=ctx_source,
        one_month_return=one_month, t=t)
    ctx = {"ctx_dir": ctx_dir, "ctx_source": ctx_source, "news_fresh": news_fresh,
           "one_month_return": one_month, "news_headline": news_headline,
           "news_date": news_date, "news_direction": news_direction}
    return {"card": card, "cells": cells, "candidates": candidates, "ctx": ctx}


def fallback_card(analysis: Dict[str, Any], index: int = 1,
                  t: Dict[str, Any] = TUNABLES) -> Optional[Dict[str, Any]]:
    """Re-card on the ranked candidate at `index` after a PLAN veto.
    The design allows exactly one fallback (index 1); returns None when no
    further candidate exists."""
    candidates = analysis.get("candidates") or []
    if index >= len(candidates):
        return None
    decision = candidates[index]
    cells = analysis["cells"]
    story = next(c for c in cells if c.key() == decision.story_cell_key)
    decision.flavors = _detect_flavors(story)
    ctx = analysis["ctx"]
    prev = analysis["card"]
    return build_angle_card(
        symbol=prev["symbol"], resource_id=prev["resource_id"],
        anchor=prev["anchor_date"], cells=cells, decision=decision,
        candidates=candidates[index:],
        news_headline=ctx["news_headline"], news_date=ctx["news_date"],
        news_direction=ctx["news_direction"], news_fresh=ctx["news_fresh"],
        ctx_dir=ctx["ctx_dir"], ctx_source=ctx["ctx_source"],
        one_month_return=ctx["one_month_return"], t=t)


def run_angle_engine(resource_id: Any, symbol: str, anchor: str,
                     **kwargs) -> Dict[str, Any]:
    """Back-compat convenience: the Angle Card alone (see analyze())."""
    return analyze(resource_id, symbol, anchor, **kwargs)["card"]


def _print_card(card: Dict[str, Any]) -> None:
    print(f"\n=== Angle Card: {card['symbol']} anchored {card['anchor_date']} ===")
    print(f"{'cell':>22} {'n':>3} {'record':>9} {'dir':>8} {'median':>7} "
          f"{'tail_p':>7}  eligible")
    for row in card['matrix_summary']:
        flag = 'YES' if row['eligible'] else f"no ({row['reason'][:38]})"
        mm = ' [stats-mismatch]' if row.get('stats_mismatch') else ''
        print(f"{row['days']:>3}d x {row['years']:>7}y {row['n']:>5} "
              f"{row['record']:>9} {row['direction']:>8} {row['median_net']:>6.1f}% "
              f"{row['tail_p']:>7.4f}  {flag}{mm}")
    if not card.get('angle'):
        print(f"\nNO STORY — {card.get('no_story', '')}")
        return
    a = card['angle']
    sc = card['story_cell']
    print(f"\nANGLE: {a['name']} (score {a['score']:.3f}) — {a['rationale']}")
    if a['flavors']:
        print(f"flavors: {', '.join(a['flavors'])}")
    if a['runner_up']:
        ru = ', '.join(f"{r['name']} ({r['score']:.3f})" for r in a['runner_up'])
        print(f"fallbacks: {ru}")
    print(f"tension: {card['tension_descriptor']}")
    print(f"story cell: {sc['days']}d x {sc['years']}y — "
          f"{sc['up_years']}up/{sc['down_years']}dn, median {sc['median_net']}%")
    for name, text in sc['quotables'].items():
        print(f"  quotable[{name}]: “{text}”")
    for aux in card['auxiliary_cells']:
        print(f"aux ({aux['role']}): {aux['days']}d x {aux['years']}y — "
              f"{aux['up_years']}up/{aux['down_years']}dn, median {aux['median_net']}%")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='SMN angle engine (Phase 1).')
    p.add_argument('symbol')
    p.add_argument('--resource', default='2')
    p.add_argument('--anchor', default=datetime.date.today().isoformat())
    p.add_argument('--news-headline', default='')
    p.add_argument('--news-date', default='')
    p.add_argument('--news-direction', default='',
                   choices=['', 'bullish', 'bearish'])
    p.add_argument('--detected', action='append', default=[],
                   metavar='START:DAYS:YEARS',
                   help='detected-pattern candidate, e.g. 2026-08-02:47:14')
    p.add_argument('--offline-fixture', default='',
                   help='JSON file of {cell_key: raw ChartData4 response}')
    p.add_argument('--record-fixture', default='',
                   help='write fetched raw responses to this JSON file')
    p.add_argument('--json', default='', help='write the Angle Card here')
    args = p.parse_args(argv)

    fixture = None
    if args.offline_fixture:
        with open(args.offline_fixture, encoding='utf-8') as fh:
            fixture = json.load(fh)
    record: Optional[Dict[str, Any]] = {} if args.record_fixture else None

    detected = []
    for spec in args.detected:
        try:
            start, days, years = spec.split(':')
            detected.append({'start_date': start, 'days': int(days), 'years': years})
        except ValueError:
            print(f'[angle_engine] bad --detected spec {spec!r}, expected START:DAYS:YEARS')
            return 2

    card = run_angle_engine(args.resource, args.symbol, args.anchor,
                            news_headline=args.news_headline,
                            news_date=args.news_date,
                            news_direction=args.news_direction,
                            detected=detected, fixture=fixture, record=record)
    _print_card(card)

    if args.record_fixture and record is not None:
        with open(args.record_fixture, 'w', encoding='utf-8') as fh:
            json.dump(record, fh, indent=1)
        print(f'\n[angle_engine] fixture written: {args.record_fixture} '
              f'({len(record)} cells)')
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as fh:
            json.dump(card, fh, indent=2, default=str)
        print(f'[angle_engine] card written: {args.json}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
