"""Deterministic, private cohort policy over an explicitly supplied annual panel.

No fetching, config, selection scores or publication imports. A fixed twenty-year
reference is a proposed editorial default, not a validated optimum. All returned
observations are percentages; source prices and archived derived claims are omitted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import re
from statistics import median
from typing import Any

from article_evidence import build_cell_evidence, finite_number, inclusive_window

IDENTITY_FIELDS = ('resource_id', 'provider', 'exchange', 'symbol', 'series_id')
VERSION = 'fixed20-cycle10-private-v1'
MISSING_WINDOW_REASONS = frozenset({'before_series_start', 'source_data_unavailable',
    'corporate_action_unresolved', 'contract_unavailable', 'calendar_unavailable'})


@dataclass(frozen=True)
class CohortPolicy:
    annual_min_n: int = 8
    cycle_min_n: int = 6


def instrument_key(instrument: dict) -> str:
    fields = {name: '' if instrument.get(name) is None else str(instrument.get(name, '')).strip()
              for name in IDENTITY_FIELDS}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode('utf-8')).hexdigest()


def _json_safe(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and finite_number(value) is None:
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _day(value) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError('as-of timestamps require a timezone')
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return date.fromisoformat(value)
    if isinstance(value, str):
        return _day(datetime.fromisoformat(value.replace('Z', '+00:00')))
    raise ValueError('a calendar date is required')


def _year(value):
    number = finite_number(value)
    return int(number) if number is not None and number.is_integer() and 1 <= number <= 9999 else None


def _rounded(value, digits=2):
    return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def _summary(rows: list[dict]) -> dict:
    years = [r['year'] for r in rows]
    nets = [r['net'] for r in rows]
    n, up, down = len(rows), sum(v > 0 for v in nets), sum(v < 0 for v in nets)
    decimals = [Decimal(str(v)) for v in nets]
    med = float(median(decimals)) if nets else None
    best = max(rows, key=lambda r: r['net']) if rows else {}
    worst = min(rows, key=lambda r: r['net']) if rows else {}
    return {'n': n, 'years': years, 'first_year': years[0] if years else None,
            'last_year': years[-1] if years else None, 'up_years': up, 'down_years': down,
            'flat_years': n-up-down, 'up_rate_pct': _rounded(100*up/n) if n else None,
            'down_rate_pct': _rounded(100*down/n) if n else None,
            'median_net': med, 'median_net_display': _rounded(med) if n else None,
            'avg_net': float(sum(decimals)/n) if n else None,
            'median_direction': 'up' if med is not None and med > 0 else 'down' if med is not None and med < 0 else 'flat' if n else 'unavailable',
            'strict_majority': 'up' if up > n/2 else 'down' if down > n/2 else 'none',
            'best_year': best.get('year'), 'best_net': best.get('net'),
            'worst_year': worst.get('year'), 'worst_net': worst.get('net')}


def _relation(left, right):
    a, b = left['median_direction'], right['median_direction']
    return ('unavailable' if 'unavailable' in (a, b) else 'flat_involved' if 'flat' in (a, b)
            else 'consistent' if a == b else 'contrast')


def _source(value, index):
    """Normalize public cells or raw ChartData4 responses without carrying prices."""
    value = asdict(value) if is_dataclass(value) else value
    if not isinstance(value, dict) or value.get('malformed'):
        return {'source_id': str(value.get('source_id') or f'cell-{index}') if isinstance(value, dict) else f'cell-{index}', 'malformed': True}
    request = value.get('request') or {}
    raw = 'ChartData4' in value
    years = value.get('years')
    if raw:
        count = request.get('years')
        phase = request.get('pe_cycle')
        years = f'{phase}-{count}' if phase and str(phase).startswith('pe') else str(count)
    rows = value.get('per_year', []) if not raw else value.get('ChartData4', [])
    normalized = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                normalized.append({'malformed': True}); continue
            if raw:
                parts = str(row.get('pct', '')).split(',')
                normalized.append({'year': row.get('year'),
                    **({k: v.strip() for k, v in zip(('net', 'mfe', 'mae'), parts)} if len(parts) == 3 else {'malformed': True})})
            else:
                normalized.append({k: row[k] for k in ('year', 'net', 'mfe', 'mae', 'malformed') if k in row})
    else:
        normalized = [{'malformed': True}]
    identity = value.get('instrument') if isinstance(value.get('instrument'), dict) else value
    return _json_safe({'source_id': str(value.get('source_id') or f'cell-{index}'),
        'resource_id': value.get('resource_id', request.get('market')),
        'symbol': value.get('symbol', request.get('symbol')),
        **{k: identity.get(k) for k in ('provider', 'exchange', 'series_id')},
        'anchor_date': value.get('anchor_date', request.get('entry_date')),
        'days': value.get('days', request.get('days_out')), 'years': years,
        'source_kind': value.get('source_kind', 'cell'), 'per_year': normalized})


def build_selection_evidence(card=None, *, cells=None, observations=None, instrument=None,
                             anchor_date=None, days=None, as_of=None, coverage=None,
                             policy=None) -> dict:
    """Build a fixed-window cohort brief. See COHORT_POLICY_CONTRACT.md.

    ``cells`` accepts public Cell dictionaries/dataclasses or raw ChartData4
    responses. ``observations`` accepts year/net/mfe/mae rows with explicit root
    identity and coverage. Different horizons in a matrix are ignored; they can
    never change the requested horizon. All other identity conflicts hold.
    """
    policy = CohortPolicy(**policy) if isinstance(policy, dict) else policy or CohortPolicy()
    if not isinstance(policy, CohortPolicy) or any(isinstance(v, bool) or not isinstance(v, int) or v < 1
                                                 for v in asdict(policy).values()):
        raise ValueError('pilot sample floors must be positive integers')
    card = card if isinstance(card, dict) else {}
    story = card.get('story_cell') or {}
    instrument = dict(instrument or {k: story.get(k, card.get(k)) for k in IDENTITY_FIELDS})
    identity = {k: '' if instrument.get(k) is None else str(instrument.get(k, '')).strip() for k in IDENTITY_FIELDS}
    anchor_date = anchor_date if anchor_date is not None else story.get('anchor_date', card.get('anchor_date'))
    days = days if days is not None else story.get('days')
    as_of = as_of if as_of is not None else card.get('generated_at') or anchor_date
    coverage = _json_safe(coverage or {})
    issues = []

    def issue(code, severity='hold', **details):
        issues.append({'code': code, 'severity': severity, **details})

    try:
        anchor, checked_at = _day(anchor_date), _day(as_of)
        window = inclusive_window(anchor.isoformat(), days)
        if (anchor.month, anchor.day) == (2, 29):
            raise ValueError('February 29 anniversaries require an explicit data-calendar policy')
        cutoff = min(anchor, checked_at)  # never introduce outcomes learned after entry
        last = cutoff.year
        while date.fromisoformat(inclusive_window(date(last, anchor.month, anchor.day).isoformat(), days)['end_date']) >= cutoff:
            last -= 1
        if last < 40:
            raise ValueError('insufficient supported calendar range')
        if not identity['resource_id'] or not identity['symbol']:
            raise ValueError('resource_id and symbol are required')
    except (TypeError, ValueError, OverflowError):
        return {'schema_version': 1, 'policy_version': VERSION, 'mode': 'private_preview', 'publishable': False,
                'classification': 'incomparable', 'issues': [{'code': 'INVALID_IDENTITY_OR_CALENDAR', 'severity': 'hold'}],
                'pilot_eligibility': {'eligible': False, 'annual_lead': False, 'cycle_lead': False},
                'writer_brief': {'classification': 'incomparable', 'required_qualifications': [],
                                 'hold_reasons': ['INVALID_IDENTITY_OR_CALENDAR']}}
    baseline_years = list(range(last-19, last+1))
    phase = anchor.year % 4
    cycle_last = last - (last-phase) % 4
    cycle_years = list(range(cycle_last-36, cycle_last+1, 4))
    cycle_code = f'pe{phase}-10'
    supplied = list(cells or [])
    if story:
        supplied += [story] + list(card.get('auxiliary_cells') or [])
    sources = [_source(c, i) for i, c in enumerate(supplied)]
    if observations is not None:
        sources.append(_source({**identity, 'anchor_date': anchor.isoformat(), 'days': window['calendar_days'],
            'years': 'panel', 'source_kind': 'annual_panel', 'source_id': 'annual-panel', 'per_year': observations}, len(sources)))
    replay = {'cells': sources, 'instrument': identity, 'anchor_date': anchor.isoformat(),
              'days': window['calendar_days'], 'as_of': checked_at.isoformat(), 'coverage': coverage, 'policy': asdict(policy)}
    panel, origins, conflicts, exclusions, spans = {}, {}, set(), [], []
    checked_cycle = False
    explicit_start, explicit_end = _year(coverage.get('start_year')), _year(coverage.get('end_year'))
    if explicit_start and explicit_end and explicit_start <= explicit_end and str(coverage.get('source_ref') or '').strip():
        spans.append((explicit_start, explicit_end))
    elif coverage and any(k in coverage for k in ('start_year', 'end_year', 'source_ref')):
        issue('INVALID_COVERAGE_DECLARATION')
    for source in sources:
        sid = source['source_id']
        if source.get('malformed'):
            issue('MALFORMED_SOURCE', source_id=sid); continue
        source_identity = {k: '' if source.get(k) is None else str(source.get(k, '')).strip() for k in IDENTITY_FIELDS}
        if any(source_identity[k] != identity[k] for k in ('resource_id', 'symbol')) or any(
                source_identity[k] and source_identity[k] != identity[k] for k in ('provider', 'exchange', 'series_id')):
            issue('SOURCE_IDENTITY_MISMATCH', source_id=sid); continue
        if source.get('anchor_date') != anchor.isoformat():
            issue('SOURCE_ANCHOR_MISMATCH', source_id=sid); continue
        try:
            source_window = inclusive_window(source['anchor_date'], source['days'])
        except (KeyError, TypeError, ValueError, OverflowError):
            issue('INVALID_SOURCE_WINDOW', source_id=sid); continue
        if source_window != window:
            exclusions.append({'source_id': sid, 'reason': 'outside_fixed_horizon'}); continue
        code = source.get('years')
        if not isinstance(code, str):
            issue('YEARS_CODE_MUST_BE_STRING', source_id=sid); continue
        match = re.fullmatch(r'pe([0-3])-([1-9]\d*)', code)
        annual = re.fullmatch(r'[1-9]\d*', code)
        if match:
            source_phase, requested = int(match[1]), int(match[2])
            if requested > 250:
                issue('UNSUPPORTED_SOURCE_SPAN', source_id=sid); continue
            if source_phase != phase:
                exclusions.append({'source_id': sid, 'reason': 'outside_fixed_cycle'}); continue
            end = last - (last-source_phase) % 4
            expected = set(range(end-4*(requested-1), end+1, 4))
            checked_cycle |= requested >= 10
        elif annual:
            requested = int(code)
            if requested > 250:
                issue('UNSUPPORTED_SOURCE_SPAN', source_id=sid); continue
            expected = set(range(last-requested+1, last+1)); spans.append((last-requested+1, last))
        elif code == 'panel' and source.get('source_kind') == 'annual_panel':
            expected = None
        else:
            issue('INVALID_YEARS_CODE', source_id=sid); continue
        rows = source.get('per_year') or []
        counts = {}
        for row in rows:
            y = _year(row.get('year')) if isinstance(row, dict) else None
            if y is not None: counts[y] = counts.get(y, 0)+1
        for row in rows:
            y = _year(row.get('year')) if isinstance(row, dict) else None
            if y is None:
                issue('INVALID_OBSERVATION_YEAR', source_id=sid); continue
            try:
                completed = date.fromisoformat(inclusive_window(date(y, anchor.month, anchor.day).isoformat(), days)['end_date']) < cutoff
            except (ValueError, OverflowError):
                completed = False
            if not completed:
                exclusions.append({'source_id': sid, 'year': y, 'reason': 'window_not_completed_at_decision'}); continue
            if expected is not None and y not in expected:
                issue('ROW_OUTSIDE_DECLARED_COHORT', source_id=sid, year=y); continue
            if counts[y] > 1:
                conflicts.add(y); issue('DUPLICATE_YEAR_WITHIN_SOURCE', source_id=sid, year=y); continue
            net = finite_number(row.get('net'))
            if net is None or row.get('malformed'):
                issue('INVALID_NET_OBSERVATION', source_id=sid, year=y); continue
            parsed = {'year': y, 'net': net, 'mfe': finite_number(row.get('mfe')), 'mae': finite_number(row.get('mae'))}
            if y in panel:
                disagreements = [k for k in ('net', 'mfe', 'mae') if panel[y][k] is not None and parsed[k] is not None and panel[y][k] != parsed[k]]
                if disagreements:
                    conflicts.add(y); issue('CONFLICTING_OBSERVATIONS', year=y, fields=disagreements); continue
                parsed = {k: panel[y][k] if panel[y][k] is not None else parsed[k] for k in parsed}
            panel[y] = parsed; origins.setdefault(y, []).append(sid)
    for y in conflicts:
        panel.pop(y, None); origins.pop(y, None)
    rows = [panel[y] for y in sorted(panel)]

    def covered(start, end):
        return all(any(a <= y <= b for a, b in spans) for y in range(start, end+1))

    annual_checked = covered(baseline_years[0], baseline_years[-1])
    checked_cycle |= covered(cycle_years[0], last)
    missing_reasons = coverage.get('missing_year_reasons') if isinstance(coverage.get('missing_year_reasons'), dict) else {}
    series_start = _year(coverage.get('series_start_year'))
    series_ref = str(coverage.get('series_start_evidence_ref') or '').strip()
    if series_start and series_ref and any(y < series_start for y in panel):
        issue('SERIES_START_CONFLICTS_WITH_OBSERVATIONS')

    def missing_reason(y):
        reason = missing_reasons.get(str(y), missing_reasons.get(y))
        if isinstance(reason, dict) and reason.get('reason') in MISSING_WINDOW_REASONS and str(reason.get('evidence_ref') or '').strip():
            return {'reason': str(reason['reason']), 'evidence_ref': str(reason['evidence_ref'])}
        if series_start and y < series_start and series_ref:
            return {'reason': 'before_confirmed_series_start', 'evidence_ref': series_ref}
        return None

    missing = [y for y in baseline_years if y not in panel]
    unexplained = [y for y in missing if missing_reason(y) is None]
    if not annual_checked: issue('BASELINE_COVERAGE_NOT_CHECKED')
    if unexplained: issue('BASELINE_UNEXPLAINED_MISSING_YEARS', years=unexplained)
    baseline_rows = [panel[y] for y in baseline_years if y in panel]
    baseline = {'years_code': '20', 'requested_years': baseline_years, 'summary': _summary(baseline_rows),
                'per_year': baseline_rows, 'coverage_status': 'checked' if annual_checked else 'not_checked',
                'missing_years': missing, 'missing_year_reasons': {str(y): missing_reason(y) for y in missing}}
    if len(baseline_rows) < policy.annual_min_n: issue('BASELINE_BELOW_PILOT_FLOOR', n=len(baseline_rows), floor=policy.annual_min_n)
    full_rows = [panel[y] for y in cycle_years if y in panel]
    full = {'years_code': cycle_code, 'requested_years': cycle_years, 'summary': _summary(full_rows),
            'coverage_status': 'checked' if checked_cycle else 'not_checked',
            'missing_years': [y for y in cycle_years if y not in panel]}
    if not checked_cycle: issue('CYCLE_CONTEXT_NOT_CHECKED')
    elif len(full_rows) < policy.cycle_min_n:
        issue('CYCLE_INSUFFICIENT', severity='context', n=len(full_rows), floor=policy.cycle_min_n)
    if full_rows:
        internal_cycle_gaps = [y for y in cycle_years if full_rows[0]['year'] < y < full_rows[-1]['year']
                               and y not in panel and missing_reason(y) is None]
        if internal_cycle_gaps: issue('CYCLE_UNEXPLAINED_INTERNAL_GAPS', years=internal_cycle_gaps)
    recent_cycle_rows = [r for r in full_rows if r['year'] in baseline_years]
    noncycle_rows = [r for r in baseline_rows if r['year'] % 4 != phase]
    older_cycle_rows = [r for r in full_rows if r['year'] < baseline_years[0]]
    recent_cycle, noncycle, earlier = map(_summary, (recent_cycle_rows, noncycle_rows, older_cycle_rows))
    full_start = full_rows[0]['year'] if full_rows else cycle_years[0]
    comparator_missing = [y for y in range(full_start, last+1) if y not in panel and missing_reason(y) is None]
    comparator_ready = bool(full_rows) and covered(full_start, last) and not comparator_missing
    comparator_noncycle = [r for r in rows if full_start <= r['year'] <= last and r['year'] % 4 != phase]
    comparator = {'status': 'available' if comparator_ready else 'unavailable', 'start_year': full_start, 'end_year': last,
                  'missing_annual_years': comparator_missing, 'annual_coverage_confirmed': covered(full_start, last),
                  'cycle_summary': full['summary'] if comparator_ready else None,
                  'noncycle_summary': _summary(comparator_noncycle) if comparator_ready else None,
                  'observed_noncycle_n': len(comparator_noncycle), 'overlap_n': 0,
                  'reason': None if comparator_ready else 'No complete, confirmed annual comparator over the full cycle-history calendar span.'}
    recent = {}
    for n in (5, 10):
        boundary = last-n+1
        a, b = [r for r in baseline_rows if r['year'] >= boundary], [r for r in baseline_rows if r['year'] < boundary]
        recent[str(n)] = {'recent': _summary(a), 'preceding': _summary(b), 'overlap_n': 0,
                          'comparison': _relation(_summary(a), _summary(b)),
                          'requested_recent_years': list(range(boundary, last+1))}
    relations = {'full_cycle_vs_baseline': _relation(full['summary'], baseline['summary']),
                 'same_span_cycle_vs_noncycle': _relation(recent_cycle, noncycle),
                 'recent_vs_earlier_cycle': _relation(recent_cycle, earlier)}
    fatal = any(i['code'] in ('SOURCE_IDENTITY_MISMATCH', 'SOURCE_ANCHOR_MISMATCH', 'CONFLICTING_OBSERVATIONS',
                              'DUPLICATE_YEAR_WITHIN_SOURCE', 'ROW_OUTSIDE_DECLARED_COHORT') for i in issues)
    holds = [i['code'] for i in issues if i['severity'] == 'hold']
    if fatal: classification = 'incomparable'
    elif holds: classification = 'insufficient'
    elif len(full_rows) < policy.cycle_min_n: classification = 'insufficient_context'
    elif relations['full_cycle_vs_baseline'] == 'contrast' and _relation(recent_cycle, baseline['summary']) == 'consistent':
        classification = 'era_sensitive'
    elif relations['same_span_cycle_vs_noncycle'] == 'contrast': classification = 'genuine_contrast'
    elif relations['recent_vs_earlier_cycle'] == 'contrast' or any(r['comparison'] == 'contrast' for r in recent.values()):
        classification = 'era_sensitive'
    elif 'flat_involved' in relations.values(): classification = 'mixed'
    else: classification = 'consistent'
    annual_lead = not holds
    cycle_lead = annual_lead and checked_cycle and len(full_rows) >= policy.cycle_min_n
    requirements = []

    def qualify(key, text, *refs):
        requirements.append({'id': key, 'text': text, 'evidence_refs': list(refs)})

    if classification == 'era_sensitive':
        qualify('cohort.era_sensitive', 'The result changes with the historical period. Explain the dated recent and earlier observations; do not silently select the stronger sample.',
                '/baseline/summary', '/cycle/full/summary', '/cycle/within_baseline/summary', '/cycle/earlier/summary', '/recent')
    elif classification == 'genuine_contrast':
        qualify('cohort.genuine_contrast', 'The same-calendar-span cycle and complementary noncycle observations have contrasting median signs. Describe mixed history and sample sizes; this is a descriptive contrast, not a causal or predictive finding.',
                '/cycle/within_baseline/summary', '/cycle/noncycle_within_baseline/summary')
    elif classification == 'mixed':
        qualify('cohort.mixed', 'At least one compared group has a flat median rather than a clear positive or negative median. Describe the actual dated results; do not turn a flat result into directional agreement.',
                '/baseline/summary', '/cycle/full/summary', '/cycle/within_baseline/summary', '/cycle/noncycle_within_baseline/summary', '/cycle/earlier/summary')
    if classification != 'era_sensitive' and (relations['recent_vs_earlier_cycle'] == 'contrast' or any(r['comparison'] == 'contrast' for r in recent.values())):
        qualify('cohort.era_sensitive', 'The recent and earlier observations differ in median direction. Retain this period sensitivity even when another cohort contrast is the main classification.',
                '/cycle/within_baseline/summary', '/cycle/earlier/summary', '/recent')
    if recent_cycle_rows:
        qualify('cohort.overlap', 'The cycle observations inside the main sample are reused observations, not independent confirmation. Compare them with complementary noncycle years.', '/overlap')
        if len(recent_cycle_rows) < policy.cycle_min_n:
            qualify('cohort.small_matched_sample', f'The matched-span cycle subset contains only {len(recent_cycle_rows)} observations and remains descriptive.', '/cycle/within_baseline/summary')
    if not comparator_ready:
        qualify('cohort.incomplete_comparator', 'A full-span annual comparator is unavailable. Do not claim the complete cycle-history comparison controls for the historical era.', '/cycle/full_span_comparator')
    if classification == 'insufficient_context':
        qualify('cohort.insufficient_context', 'The requested cycle history has too few available observations for a cycle-led premise; the annual record may still be described.', '/cycle/full/summary')
    if checked_cycle and len(full_rows) < 10:
        qualify('cohort.shortfall', 'The ten-observation cycle request returned a shorter available sample. State actual n and historical years, not the requested ten.', '/cycle/full')
    if baseline['summary']['strict_majority'] == 'none':
        qualify('baseline.no_strict_majority', 'The main sample has no strict majority of positive or negative outcomes. A median sign must not be described as a strong winning record.', '/baseline/summary')
    if missing:
        qualify('baseline.shortfall', 'The declared twenty-year span has missing observations. State actual n, dates and documented exclusions; do not replace missing years with older favorable results.', '/baseline')
    result = {'schema_version': 1, 'policy_version': VERSION, 'mode': 'private_preview', 'publishable': False,
        'identity': {**identity, 'instrument_key': instrument_key(identity)}, 'window': window,
        'as_of': checked_at.isoformat(), 'decision_cutoff': cutoff.isoformat(), 'last_completed_year': last,
        'annual_panel': {'rows': rows, 'source_ids_by_year': {str(y): sorted(set(origins[y])) for y in sorted(origins)},
                         'confirmed_annual_spans': [list(s) for s in sorted(set(spans))],
                         'excluded': exclusions, 'scope': 'Union of compatible supplied observations; missing years are not fabricated.'},
        'baseline': baseline, 'recent': recent,
        'cycle': {'phase': phase, 'full': full, 'within_baseline': {'summary': recent_cycle},
                  'noncycle_within_baseline': {'summary': noncycle}, 'earlier': {'summary': earlier},
                  'full_span_comparator': comparator},
        'overlap': {'baseline_cycle_shared_years': [r['year'] for r in recent_cycle_rows],
                    'baseline_cycle_shared_n': len(recent_cycle_rows),
                    'distinct_years_in_baseline_cycle_union': len(set(baseline['summary']['years']) | set(full['summary']['years'])),
                    'same_span_cycle_noncycle_overlap_n': 0},
        'comparisons': relations, 'classification': classification,
        'pilot_eligibility': {'eligible': annual_lead, 'annual_lead': annual_lead, 'cycle_lead': cycle_lead,
                              'annual_min_n': policy.annual_min_n, 'cycle_min_n': policy.cycle_min_n,
                              'provisional': True, 'optimal_thresholds_validated': False},
        'issues': issues,
        'writer_brief': {'classification': classification,
            'proposed_reader_question': 'What does the fixed annual history show, and does period-aligned cycle context change that interpretation?',
            'required_qualifications': requirements,
            'permitted_summary_refs': ['/baseline/summary', '/recent/5', '/recent/10', '/cycle/full/summary',
                                       '/cycle/within_baseline/summary', '/cycle/noncycle_within_baseline/summary', '/cycle/earlier/summary'],
            'forbidden_inferences': ['Independent confirmation from overlapping samples', 'Calibrated forecast probability',
                                     'Causal cycle effect', 'Validated prediction performance', 'Extrema timing or peak-to-trough path from MFE/MAE'],
            'hold_reasons': sorted(set(holds))}, 'replay_input': replay}
    return result


def validate_selection_evidence(evidence: dict) -> dict:
    """Recompute the policy from its percent-only input record; no external truth claim."""
    try:
        expected = build_selection_evidence(**evidence['replay_input'])
        valid = expected == evidence
    except (KeyError, TypeError, ValueError, OverflowError):
        valid = False
    return {'ok': valid, 'issues': [] if valid else ['COHORT_EVIDENCE_RECOMPUTATION_MISMATCH']}


def baseline_cell(evidence: dict) -> dict | None:
    """Normalized fixed-20 cell for parent integration; never fallback to another view."""
    if not validate_selection_evidence(evidence)['ok'] or not evidence['pilot_eligibility']['annual_lead']:
        return None
    return _normalized_cell(evidence, evidence['baseline']['per_year'], '20', 'primary', '/baseline/summary')


def _normalized_cell(evidence, rows, years, role, evidence_ref):
    identity, window = evidence['identity'], evidence['window']
    s = _summary(rows)
    direction = 'bullish' if s['median_direction'] == 'up' else 'bearish' if s['median_direction'] == 'down' else 'flat'
    cell = {'resource_id': identity['resource_id'], 'symbol': identity['symbol'],
            'anchor_date': window['start_date'], 'end_date': window['end_date'], 'days': window['calendar_days'],
            'years': years, 'mode': 'pe' if years.startswith('pe') else 'cons', 'horizon_tag': str(window['calendar_days'])+'d',
            **{k: s[k] for k in ('n', 'up_years', 'down_years', 'flat_years', 'best_year', 'best_net', 'worst_year', 'worst_net')},
            'direction': direction, 'median_net': round(s['median_net'], 2), 'avg_net': round(s['avg_net'], 2),
            'per_year': [dict(r) for r in rows], 'stats_raw': {}, 'stats_mismatch': False,
            'notes': ['Fixed20 private policy; sample floors are provisional, not validated confidence thresholds.'],
            'eligible': role == 'primary', 'ineligible_reason': '' if role == 'primary' else 'comparison context only',
            'selection_policy': VERSION, 'role': role, 'selection_evidence_ref': evidence_ref}
    cell['evidence'] = build_cell_evidence(cell)
    # Use the same decimal half-up display facts as the deterministic article gates.
    cell['median_net'] = cell['evidence']['returns']['median_net']
    cell['avg_net'] = cell['evidence']['returns']['avg_net']
    cell['median_mfe'] = cell['evidence']['risk']['median_favorable_from_entry']['value_pct']
    cell['median_mae'] = cell['evidence']['risk']['median_adverse_from_entry']['value_pct']
    cell['lookback_label'] = cell['evidence']['cohort']['label']
    cell['requested_lookback_label'] = ('20 completed annual windows in the fixed calendar span' if role == 'primary'
                                        else 'Explicit comparison subset; actual years shown')
    return cell


def comparison_cells(evidence: dict) -> list[dict]:
    """Recomputable context cells licensing the brief's summaries, never new leads."""
    if not validate_selection_evidence(evidence)['ok']:
        return []
    by_year = {r['year']: r for r in evidence['annual_panel']['rows']}
    phase = evidence['cycle']['phase']
    choices = [('/cycle/full/summary', evidence['cycle']['full']['summary'], f'pe{phase}-10'),
               ('/cycle/within_baseline/summary', evidence['cycle']['within_baseline']['summary'], f'pe{phase}-5'),
               ('/cycle/noncycle_within_baseline/summary', evidence['cycle']['noncycle_within_baseline']['summary'], f'nonpe{phase}-20'),
               ('/cycle/earlier/summary', evidence['cycle']['earlier']['summary'], f'pe{phase}-earlier')]
    for n in ('5', '10'):
        choices += [(f'/recent/{n}/recent', evidence['recent'][n]['recent'], n),
                    (f'/recent/{n}/preceding', evidence['recent'][n]['preceding'], f'preceding-{20-int(n)}')]
    result = []
    for ref, summary, code in choices:
        if summary['n']:
            result.append(_normalized_cell(evidence, [by_year[y] for y in summary['years']], code, 'comparison', ref))
    return result
