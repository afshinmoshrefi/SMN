"""Deterministic, private-only market eligibility and editorial lineup policy.

No application/config imports, I/O, ranking model, probability or publisher. A
reviewed manifest is supplied by the trusted evidence adapter, never by a writer.
Its hash detects mismatched metadata; this module does not authenticate a source
or certify an economic definition just because an input asserts it. See
MARKET_POLICY_CONTRACT.md for the upstream evidence boundary.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence


IDENTITY_FIELDS = ('resource_id', 'provider', 'exchange', 'symbol', 'series_id')
RESOURCE_IDS = frozenset(map(str, range(14))) | {'16'}
ASSET_CLASSES = frozenset({'equity', 'equity_index', 'etf', 'bond', 'rate',
                           'commodity', 'fx', 'crypto', 'volatility'})
MEASUREMENTS = frozenset({'adjusted_price', 'adjusted_price_return', 'price_index', 'total_return_index',
                         'fund_total_return', 'bond_price', 'yield_level',
                         'fx_spot', 'crypto_spot', 'futures_contract',
                         'continuous_futures', 'excess_return_index',
                         'volatility_index'})
SEMANTIC_FIELDS = ('measurement', 'units', 'return_basis', 'adjustment', 'currency',
                   'calendar_id', 'session_model', 'timezone')
PRIORITY_FIELDS = ('consequence', 'timeliness', 'relevance', 'completeness')
_HEX = re.compile(r'^[0-9a-f]{64}$')


def _text(value: Any) -> str:
    return '' if value is None else str(value).strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def _identity(instrument: Mapping[str, Any]) -> dict:
    return {name: _text(instrument.get(name)) for name in IDENTITY_FIELDS}


def instrument_key(instrument: Mapping[str, Any]) -> str:
    """Hash all five supplied identity fields; missing fields remain empty.

    Case is preserved. Computing a key does not certify an incomplete identity.
    """
    return hashlib.sha256(_canonical(_identity(instrument)).encode('utf-8')).hexdigest()


def metadata_digest(instrument: Mapping[str, Any]) -> str:
    """Bind a reviewed source manifest to this identity, class and semantics."""
    payload = {'identity': _identity(instrument), 'asset_class': instrument.get('asset_class'),
               'semantics': instrument.get('semantics')}
    return hashlib.sha256(_canonical(payload).encode('utf-8')).hexdigest()


def _day(value: Any) -> date | None:
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _instant(value: Any) -> datetime | None:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _as_of(value: Any) -> datetime:
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    result = _instant(value)
    if result is None:
        raise ValueError('as_of must be a timezone-aware datetime or date')
    return result


def _ids(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) or not v.strip() for v in value):
        return set()
    return set(value)


def _reason(code: str, detail: str, **extra: Any) -> dict:
    return {'code': code, 'detail': detail, **extra}


def _passed_gate(gate: Any) -> bool:
    return (isinstance(gate, dict) and gate.get('eligible') is True
            and gate.get('issues') == [] and bool(_ids(gate.get('evidence_ids'))))


def instrument_from_audit(identity: Mapping[str, Any], audit: Mapping[str, Any], *,
                          asset_class: str | None = None, semantics: Mapping | None = None,
                          provenance: Mapping | None = None) -> dict:
    """Attach observed CSV metadata without inferring or certifying semantics.

    Pass one series-audit.json entry. Bounds/counts/hashes are observations, not
    evidence that all sessions/windows are valid. No history gate is manufactured.
    """
    observed = {name: copy.deepcopy(audit[name]) for name in (
        'path', 'exists', 'columns', 'row_count', 'min_date', 'max_date',
        'duplicate_date_count', 'nonpositive_close_count', 'sha256') if name in audit}
    return {**_identity(identity), 'asset_class': asset_class,
            'semantics': copy.deepcopy(dict(semantics or {})),
            'provenance': copy.deepcopy(dict(provenance or {})),
            'audit_observations': observed}


def evaluate_market(instrument: Mapping[str, Any], history: Mapping[str, Any] | None = None,
                    *, as_of: Any) -> dict:
    """Assess seasonal measurement/history eligibility, independently of news.

    Sample/cohort interpretation is a separate required gate in evaluate_lineup.
    """
    now = _as_of(as_of)
    identity = _identity(instrument)
    key = instrument_key(instrument)
    reasons = []
    for field, value in identity.items():
        if not value:
            reasons.append(_reason('IDENTITY_MISSING', f'Missing {field}.', field=field))
    if identity['resource_id'] not in RESOURCE_IDS:
        reasons.append(_reason('RESOURCE_UNKNOWN', 'Resource ID is not a supported permanent TradeWave ID.'))
    asset = instrument.get('asset_class')
    if asset not in ASSET_CLASSES:
        reasons.append(_reason('ASSET_CLASS_UNKNOWN', 'A source-backed asset class is required.'))
    sem = instrument.get('semantics')
    sem = sem if isinstance(sem, dict) else {}
    measurement = sem.get('measurement')
    for field in SEMANTIC_FIELDS:
        if not isinstance(sem.get(field), str) or not sem[field].strip() or sem[field].lower() == 'unknown':
            reasons.append(_reason('SEMANTICS_MISSING', f'Missing defined {field}.', field=field))
    if measurement not in MEASUREMENTS:
        reasons.append(_reason('MEASUREMENT_UNKNOWN', 'Unknown measurement holds the seasonal claim.'))

    provenance = instrument.get('provenance')
    provenance = provenance if isinstance(provenance, dict) else {}
    reviewed = _instant(provenance.get('reviewed_at'))
    try:
        metadata_matches = provenance.get('metadata_sha256') == metadata_digest(instrument)
    except (ValueError, TypeError):
        metadata_matches = False
    if not (provenance.get('method') == 'reviewed_source_manifest'
            and _text(provenance.get('source_uri')) and _text(provenance.get('evidence_id'))
            and reviewed is not None and reviewed <= now and metadata_matches):
        reasons.append(_reason('SEMANTICS_UNVERIFIED', 'A dated reviewed source manifest must bind the exact metadata.'))

    expected_pairs = {
        'adjusted_price_return': ('percent_return', {'provider_adjusted_price'}),
        'adjusted_price': ('percent_return', {'price_return', 'dividend_adjusted_return'}),
        'price_index': ('percent_return', {'price_return'}),
        'total_return_index': ('percent_return', {'total_return'}),
        'fund_total_return': ('percent_return', {'total_return'}),
        'bond_price': ('percent_return', {'price_return'}),
        'fx_spot': ('percent_return', {'spot_change_excluding_carry'}),
        'crypto_spot': ('percent_return', {'spot_change'}),
        'futures_contract': ('percent_return', {'contract_price_change'}),
        'continuous_futures': ('percent_return', {'continuous_series_change'}),
    }
    if measurement in expected_pairs:
        units, bases = expected_pairs[measurement]
        if sem.get('units') != units or sem.get('return_basis') not in bases:
            reasons.append(_reason('RETURN_BASIS_MISMATCH', 'Units and return basis do not match the declared measurement.'))
    if measurement == 'adjusted_price_return' and sem.get('adjustment') != 'provider_adjusted_close_ratio':
        reasons.append(_reason('ADJUSTMENT_UNDEFINED', 'The provider-adjusted return path requires its reviewed adjusted-close ratio convention.'))
    if measurement in {'yield_level', 'volatility_index', 'excess_return_index'}:
        reasons.append(_reason('SPECIALIZED_ANALYSIS_REQUIRED',
                               'This measurement needs its own analysis path; generic seasonal return claims are held.'))
    allowed_for_asset = {
        'equity': {'adjusted_price', 'adjusted_price_return'},
        'equity_index': {'price_index', 'total_return_index'},
        'etf': {'adjusted_price', 'fund_total_return'},
        'bond': {'bond_price', 'total_return_index', 'futures_contract', 'continuous_futures'},
        'rate': {'yield_level', 'futures_contract', 'continuous_futures'},
        'commodity': {'futures_contract', 'continuous_futures', 'excess_return_index'},
        'fx': {'fx_spot', 'futures_contract', 'continuous_futures'},
        'crypto': {'crypto_spot', 'futures_contract', 'continuous_futures'},
        'volatility': {'volatility_index', 'futures_contract', 'continuous_futures'},
    }
    if asset in allowed_for_asset and measurement not in allowed_for_asset[asset]:
        reasons.append(_reason('ASSET_MEASUREMENT_MISMATCH', 'Asset class and measurement require a consistent reviewed definition.'))
    if sem.get('session_model') not in {'exchange', 'seven_day'}:
        reasons.append(_reason('CALENDAR_UNKNOWN', 'An exchange or seven-day session model is required.'))
    if measurement == 'crypto_spot' and (sem.get('session_model') != 'seven_day' or sem.get('timezone') != 'UTC'):
        reasons.append(_reason('CRYPTO_CALENDAR_UNSUPPORTED', 'The private spot-crypto policy requires daily UTC closes.'))
    if measurement != 'crypto_spot' and sem.get('session_model') == 'seven_day':
        reasons.append(_reason('CALENDAR_MEASUREMENT_MISMATCH', 'Seven-day treatment is currently implemented only for crypto spot.'))
    if measurement == 'fx_spot':
        if not all(_text(sem.get(f)) for f in ('base_currency', 'quote_currency', 'close_convention')):
            reasons.append(_reason('FX_QUOTE_UNDEFINED', 'Base/quote currencies and close convention must be explicit.'))
        elif sem['base_currency'] == sem['quote_currency']:
            reasons.append(_reason('FX_QUOTE_INVALID', 'Base and quote currencies must differ.'))
    if measurement in {'futures_contract', 'continuous_futures'}:
        fields = ['contract_definition', 'settlement', 'multiplier', 'denominator_method']
        if measurement == 'continuous_futures':
            fields += ['roll_rule', 'back_adjustment']
        missing = [field for field in fields if not _text(sem.get(field)) or _text(sem.get(field)) == 'unknown']
        if missing:
            reasons.append(_reason('FUTURES_DEFINITION_INCOMPLETE', 'Contract/roll/settlement measurement is incomplete.', fields=missing))
        if sem.get('denominator_method') != 'positive_entry_price':
            reasons.append(_reason('FUTURES_DENOMINATOR_UNSUPPORTED', 'Only positive-entry price-change evidence is implemented; this is not financed trade P&L.'))
    if asset == 'etf' and sem.get('fund_structure') != 'unlevered_plain':
        reasons.append(_reason('FUND_STRUCTURE_REVIEW_REQUIRED', 'Only explicitly reviewed straightforward unlevered funds qualify in this private pass.'))
    if asset == 'equity_index' and not _text(sem.get('index_methodology')):
        reasons.append(_reason('INDEX_METHODOLOGY_MISSING', 'The exact index methodology/weighting must be supplied.'))

    history = history if isinstance(history, dict) else {}
    if not history:
        reasons.append(_reason('HISTORY_MISSING', 'No reviewed historical data-quality record was supplied.'))
    else:
        if history.get('instrument_key') != key:
            reasons.append(_reason('HISTORY_IDENTITY_MISMATCH', 'History belongs to a different or incomplete series identity.'))
        if history.get('calendar_id') != sem.get('calendar_id'):
            reasons.append(_reason('HISTORY_CALENDAR_MISMATCH', 'Historical quality checks used a different calendar.'))
        if not _ids(history.get('evidence_ids')) or not _HEX.fullmatch(_text(history.get('dataset_sha256'))):
            reasons.append(_reason('HISTORY_PROVENANCE_MISSING', 'Historical quality checks require evidence IDs and the dataset hash.'))
        latest, expected = _day(history.get('last_observation_date')), _day(history.get('expected_latest_date'))
        calendar_as_of = _day(history.get('calendar_checked_as_of'))
        if latest is None or expected is None or calendar_as_of != now.date() or expected > now.date():
            reasons.append(_reason('FRESHNESS_UNKNOWN', 'A dated current calendar check and latest completed-session date are required.'))
        elif latest < expected:
            reasons.append(_reason('HISTORY_STALE', 'Stored history is behind its expected completed session.'))
        elif latest > now.date():
            reasons.append(_reason('HISTORY_FUTURE', 'Stored history includes a future date.'))
        elif latest > expected:
            reasons.append(_reason('HISTORY_UNEXPECTED_SESSION', 'Latest data is beyond the declared completed-session boundary.'))
        if measurement == 'crypto_spot' and expected != now.date() - timedelta(days=1):
            reasons.append(_reason('CRYPTO_DAILY_FRESHNESS', 'UTC crypto must include yesterday; equity weekend allowances do not apply.'))
        if history.get('unexplained_gaps') != []:
            reasons.append(_reason('HISTORY_GAPS', 'Missing or unexplained session coverage holds the historical claim.'))
        if history.get('invalid_values') != []:
            reasons.append(_reason('HISTORY_INVALID_VALUES', 'Invalid or unchecked historical values hold the claim.'))
        if history.get('nonpositive_values') is not False:
            reasons.append(_reason('NONPOSITIVE_VALUES', 'Generic percentage claims require a checked positive price basis.'))
        if history.get('window_checks_passed') is not True:
            reasons.append(_reason('WINDOW_QUALITY_UNVERIFIED', 'Completed-window coverage must pass upstream evidence checks.'))
        if measurement in {'futures_contract', 'continuous_futures'} and history.get('denominator_checks_passed') is not True:
            reasons.append(_reason('FUTURES_DENOMINATOR_UNCHECKED', 'A reviewed denominator check must exclude unsupported zero, negative or unstable entry bases.'))

    core = (not any(r['code'] in {'SEMANTICS_UNVERIFIED', 'IDENTITY_MISSING', 'ASSET_CLASS_UNKNOWN'} for r in reasons)
            and (asset == 'equity' or (asset == 'etf' and sem.get('fund_structure') == 'unlevered_plain')))
    return {'instrument_key': key, 'identity': identity, 'asset_class': asset,
            'measurement': measurement, 'seasonal_eligible': not reasons,
            'core_preference': bool(core), 'reasons': reasons,
            'selection_policy': {'private': True, 'activated': False}, 'publishable': False}


def _candidate_check(candidate: Mapping, now: datetime) -> tuple[dict, list[dict]]:
    instrument = candidate.get('instrument')
    instrument = instrument if isinstance(instrument, dict) else {}
    market = evaluate_market(instrument, candidate.get('history'), as_of=now)
    reasons = []
    if not _text(candidate.get('candidate_id')):
        reasons.append(_reason('CANDIDATE_ID_MISSING', 'A stable candidate ID is required.'))
    question = candidate.get('question') or {}
    if not isinstance(question, dict) or not all(_text(question.get(k)) for k in ('question_id', 'text')) or not _ids(question.get('answer_evidence_ids')):
        reasons.append(_reason('READER_QUESTION_UNSUPPORTED', 'A canonical reader question and evidence-backed answer are required.'))
    if not _ids(candidate.get('exposure_ids')):
        reasons.append(_reason('EXPOSURE_UNKNOWN', 'Explicit reviewed exposure IDs are required for lineup comparisons.'))
    priority = candidate.get('priority') or {}
    if (not isinstance(priority, dict) or not _text(priority.get('reason'))
            or not _ids(priority.get('evidence_ids'))
            or any(type(priority.get(k)) is not int or not 0 <= priority[k] <= 3 for k in PRIORITY_FIELDS)):
        reasons.append(_reason('PRIORITY_UNREVIEWED', 'Priority needs bounded editorial dimensions and traceable reasons.'))
    kind = candidate.get('kind')
    if kind == 'seasonal':
        if not market['seasonal_eligible']:
            reasons.append(_reason('SEASONAL_MARKET_HELD', 'Instrument/history gates did not pass.', issues=market['reasons']))
        cohort = candidate.get('cohort_gate')
        cohort = cohort if isinstance(cohort, dict) else {}
        if not _passed_gate(cohort):
            reasons.append(_reason('COHORT_HELD', 'The separate cohort/calculation gate must pass.', issues=copy.deepcopy(cohort.get('issues', []))))
        supported = _ids(cohort.get('evidence_ids'))
        if candidate.get('editorial_mode') == 'current_context':
            context = candidate.get('context_gate') or {}
            if not _passed_gate(context):
                reasons.append(_reason('CURRENT_CONTEXT_HELD', 'Verified current asset research is required.',
                                       issues=copy.deepcopy(context.get('issues', []))))
            else:
                supported |= _ids(context.get('evidence_ids'))
    elif kind == 'news':
        event = candidate.get('event') or {}
        gate = candidate.get('news_gate') or {}
        gate = gate if isinstance(gate, dict) else {}
        supported = _ids(gate.get('evidence_ids'))
        if not _passed_gate(gate):
            reasons.append(_reason('NEWS_EVIDENCE_HELD', 'The independent news evidence gate must pass.'))
        if not isinstance(event, dict) or not all(_text(event.get(k)) for k in ('event_id', 'development_id')):
            reasons.append(_reason('NEWS_IDENTITY_MISSING', 'Canonical event and development IDs are required.'))
        else:
            occurred = _instant(event.get('occurred_at'))
            expires = _instant(gate.get('valid_until'))
            evidence_ids = _ids(event.get('evidence_ids'))
            if occurred is None or occurred > now or expires is None or expires < now:
                reasons.append(_reason('NEWS_DATE_UNVERIFIED', 'A real dated event and unexpired evidence gate are required.'))
            if not evidence_ids or not evidence_ids <= _ids(gate.get('evidence_ids')) or not _ids(event.get('fact_ids')):
                reasons.append(_reason('NEWS_FACTS_UNVERIFIED', 'Event facts must be bound to the passed evidence gate.'))
            if any(gate.get(k) != event.get(k) for k in ('event_id', 'development_id', 'occurred_at', 'fact_ids')):
                reasons.append(_reason('NEWS_GATE_IDENTITY_MISMATCH', 'News evidence gate must bind this exact event, development, date and fact IDs.'))
        if instrument and any(r['code'] in {'IDENTITY_MISSING', 'RESOURCE_UNKNOWN'} for r in market['reasons']):
            reasons.append(_reason('NEWS_INSTRUMENT_IDENTITY', 'A supplied instrument must have complete identity; macro news may omit the instrument.'))
    else:
        supported = set()
        reasons.append(_reason('CANDIDATE_KIND_UNKNOWN', 'Candidate kind must be news or seasonal.'))
    if isinstance(question, dict) and not _ids(question.get('answer_evidence_ids')) <= supported:
        reasons.append(_reason('ANSWER_EVIDENCE_UNBOUND', 'Answer evidence must belong to the passed news/cohort gate.'))
    if isinstance(priority, dict) and not _ids(priority.get('evidence_ids')) <= supported:
        reasons.append(_reason('PRIORITY_EVIDENCE_UNBOUND', 'Editorial priority must cite the passed evidence.'))
    return market, reasons


def _coverage_record(candidate: Mapping, key: str) -> dict:
    event = candidate.get('event') or {}
    question = candidate['question']
    return {'candidate_id': candidate['candidate_id'], 'article_id': candidate.get('article_id'),
            'kind': candidate['kind'], 'instrument_key': key,
            'event_id': event.get('event_id'), 'development_id': event.get('development_id'),
            'occurred_at': event.get('occurred_at'), 'fact_ids': sorted(_ids(event.get('fact_ids'))),
            'evidence_ids': sorted(_ids(event.get('evidence_ids'))),
            'question_id': question['question_id'],
            'answer_evidence_ids': sorted(_ids(question.get('answer_evidence_ids'))),
            'exposure_ids': sorted(_ids(candidate.get('exposure_ids')))}


def evaluate_lineup(candidates: Sequence[Mapping], *, coverage: Sequence[Mapping] = (),
                    as_of: Any, max_articles: int = 6,
                    selection_policy: Mapping | None = None) -> dict:
    """Return an auditable private slate. Never publishes or mutates inputs.

    Trusted upstream gates own source entailment and semantic event/question IDs.
    Exposure overlap is explicit, not estimated from keywords or ticker returns.
    """
    now = _as_of(as_of)
    if type(max_articles) is not int or not 0 <= max_articles <= 6:
        raise ValueError('max_articles must be between 0 and the private ceiling of 6')
    if selection_policy is not None and (selection_policy.get('private') is not True
                                         or selection_policy.get('activated', False) is not False):
        raise ValueError('Only private, inactive selection policy is supported')
    if len(candidates) > 500:
        raise ValueError('Private candidate budget is 500')
    if len(coverage) > 2000:
        raise ValueError('Private coverage budget is 2000 current ledger records')
    decisions, eligible = [], []
    all_ids = [_text(c.get('candidate_id')) for c in candidates if isinstance(c, dict)]
    for position, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            decisions.append({'candidate_id': None, 'status': 'hold', 'reasons': [_reason('CANDIDATE_INVALID', 'Candidate must be an object.')]})
            continue
        market, reasons = _candidate_check(candidate, now)
        cid = _text(candidate.get('candidate_id'))
        if all_ids.count(cid) > 1:
            reasons.append(_reason('DUPLICATE_CANDIDATE_ID', 'Candidate IDs must be unique within the slate.'))
        decision = {'candidate_id': cid, 'status': 'hold' if reasons else 'eligible',
                    'reasons': reasons, 'market': market, 'publishable': False}
        decisions.append(decision)
        if not reasons:
            priority = candidate['priority']
            rank = (int(candidate.get('material_news') is True and candidate['kind'] == 'news'),
                    *(priority[k] for k in PRIORITY_FIELDS), int(market['core_preference']))
            eligible.append((rank, position, candidate, decision))
    eligible.sort(key=lambda row: (tuple(-v for v in row[0]), row[1]))
    selected, prior = [], [copy.deepcopy(dict(c)) for c in coverage]
    for rank, _, candidate, decision in eligible:
        record = _coverage_record(candidate, decision['market']['instrument_key'])
        event = candidate.get('event') or {}
        match, update = None, None
        for old in prior:
            same_event = bool(record['event_id']) and record['event_id'] == old.get('event_id')
            same_question = record['question_id'] == old.get('question_id')
            overlap = bool(set(record['exposure_ids']) & _ids(old.get('exposure_ids')))
            same_instrument = record['instrument_key'] == old.get('instrument_key')
            if same_event and same_question:
                old_time, new_time = _instant(old.get('occurred_at')), _instant(record['occurred_at'])
                new_facts = set(record['fact_ids']) - _ids(old.get('fact_ids'))
                new_evidence = set(record['evidence_ids']) - _ids(old.get('evidence_ids'))
                supersedes = old.get('development_id') in _ids(event.get('supersedes_development_ids'))
                if (old_time and new_time and new_time >= old_time and supersedes
                        and new_facts and new_evidence and _ids(old.get('fact_ids'))
                        and record['development_id'] != old.get('development_id')):
                    update = old
                    continue
                match = (old, 'EVENT_ALREADY_COVERED', 'The same event/question lacks a verified new development.'); break
            if same_event and not same_question and set(record['answer_evidence_ids']) <= _ids(old.get('answer_evidence_ids')):
                match = (old, 'ANSWER_ALREADY_COVERED', 'A different question label alone does not add answer evidence.'); break
            if same_question and (overlap or same_instrument):
                # Independent verified news events remain eligible despite shared exposure.
                independent_news = (candidate['kind'] == 'news' and record['event_id']
                                    and record['event_id'] != old.get('event_id')
                                    and bool(set(record['fact_ids']) - _ids(old.get('fact_ids')))
                                    and bool(set(record['evidence_ids']) - _ids(old.get('evidence_ids'))))
                if not independent_news:
                    match = (old, 'EXPOSURE_QUESTION_OVERLAP', 'The same exposure and reader question is already covered.'); break
        if match:
            old, code, detail = match
            decision.update(status='consolidate', target_id=old.get('article_id') or old.get('candidate_id'))
            decision['reasons'].append(_reason(code, detail))
            continue
        replacement = next((s for s in selected if update and s['candidate_id'] == update.get('candidate_id')), None)
        if len(selected) >= max_articles and replacement is None:
            decision.update(status='hold')
            decision['reasons'].append(_reason('DAILY_CEILING', 'Eligible but outside the private daily ceiling; no quota must be filled.'))
            continue
        if replacement is not None:
            selected.remove(replacement)
            prior.remove(update)
            replaced_decision = next(d for d in decisions if d['candidate_id'] == replacement['candidate_id'])
            replaced_decision.update(status='consolidate', target_id=candidate['candidate_id'])
            replaced_decision['reasons'].append(_reason('SUPERSEDED_IN_SLATE', 'A verified newer development replaces this treatment.'))
        action = 'update_draft' if update else 'new_draft'
        decision.update(status='select', action=action)
        if update:
            decision['target_id'] = update.get('article_id') or update.get('candidate_id')
            decision['reasons'].append(_reason('NEW_DEVELOPMENT', 'New fact/evidence IDs and explicit supersession support an update.'))
        else:
            decision['reasons'].append(_reason('DISTINCT_READER_ANSWER', 'The candidate supplies an eligible event or historical answer.'))
        if candidate['kind'] == 'news' and any(old.get('instrument_key') == record['instrument_key'] and old.get('event_id') != record['event_id'] for old in coverage):
            decision['reasons'].append(_reason('NEW_EVENT_OVERRIDES_TICKER_COOLDOWN', 'Recent ticker coverage does not block this verified new event.'))
        selected.append({'candidate_id': candidate['candidate_id'], 'action': action,
                         'target_id': decision.get('target_id'), 'rank': list(rank),
                         'coverage_record': record, 'publishable': False})
        prior.append(record)
    omitted = [{'candidate_id': d['candidate_id'], 'reasons': copy.deepcopy(d['reasons'])}
               for d in decisions if d['status'] == 'hold'
               and any(_text(c.get('candidate_id')) == d['candidate_id'] and c.get('material_news') is True
                       and c.get('kind') == 'news' for c in candidates if isinstance(c, dict))]
    return {'selection_policy': {'private': True, 'activated': False, 'max_articles': max_articles,
                                  'core_preference': 'provisional_tiebreak_only', 'market_quotas': False},
            'as_of': now.isoformat(), 'selected': selected, 'decisions': decisions,
            'omitted_material_events': omitted, 'publishable': False}
