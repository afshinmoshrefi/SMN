"""Recompute optional historical context without making it a news eligibility gate.

The research adapter chooses relevant instruments and a calendar window BEFORE
seeing returns. Missing/failed research is explicitly unavailable, never a claim
that no pattern exists. The writer can include or omit eligible context with a
recorded reason. Code owns the statistics and the expandable evidence table.
"""
from copy import deepcopy
import html
import json
import re

from cohort_policy import build_selection_evidence, validate_selection_evidence
from market_policy import evaluate_market
from news_selection import utc_time
from private_history import derive_history_panel
from reader_promise import fingerprint


def assess_news_seasonality(research, event, *, as_of):
    clock = utc_time(as_of)
    request = research.get('seasonal_research')
    result = {'status': 'unavailable', 'records': [], 'checks': [], 'as_of': clock.isoformat(),
              'reason': 'No historical research inputs supplied; this is not evidence of no pattern.',
              'publishable': False}
    if not isinstance(request, dict):
        return result
    try:
        if (request.get('event_id') != event.get('event_id') or not request.get('reviewed_by')
                or not request.get('scope_reason')
                or not 0 <= (clock - utc_time(request['checked_at'])).total_seconds() <= 7 * 86400):
            raise ValueError('HISTORICAL_RESEARCH_SCOPE_UNVERIFIED')
        specs = request.get('instruments')
        if not isinstance(specs, list) or not 1 <= len(specs) <= 4:
            raise ValueError('ONE_TO_FOUR_RELEVANT_INSTRUMENTS_REQUIRED')
        ids = [s.get('id') for s in specs if isinstance(s, dict)]
        if len(ids) != len(specs) or len(ids) != len(set(ids)):
            raise ValueError('DUPLICATE_OR_MALFORMED_HISTORY_REQUEST')
    except (ValueError, KeyError, TypeError) as exc:
        result['reason'] = str(exc) if isinstance(exc, ValueError) else 'MALFORMED_HISTORICAL_RESEARCH'
        return result
    result['scope_reason'] = request['scope_reason']
    for spec in specs:
        check = {'id': spec.get('id'), 'status': 'unavailable', 'issues': []}
        result['checks'].append(check)
        try:
            sid = spec['id']
            if (not isinstance(sid, str) or not re.fullmatch('[A-Za-z0-9-]{1,40}', sid)
                    or not spec.get('relevance_reason') or not spec.get('window_reason')):
                raise ValueError('HISTORY_RELEVANCE_OR_WINDOW_REASON_MISSING')
            instrument, inputs, raw = spec['instrument'], spec['seasonality'], spec['history_input']
            derived = derive_history_panel(raw['ohlc'], raw['session_manifest'], instrument=instrument,
                anchor_date=inputs['anchor_date'], days=inputs['days'], as_of=clock,
                dataset_sha256=raw['dataset_sha256'], source_ref=raw['source_ref'],
                requested_years=raw.get('requested_years', 40))
            if derived['status'] != 'passed':
                check['issues'] = derived['issues']
                continue
            market = evaluate_market(instrument, derived['history'], as_of=clock)
            if not market['seasonal_eligible']:
                check['issues'] = market['reasons']
                continue
            ev = build_selection_evidence(observations=derived['observations'], coverage=derived['coverage'],
                instrument=instrument, anchor_date=inputs['anchor_date'], days=inputs['days'], as_of=clock)
            validated = validate_selection_evidence(ev)
            if not validated['ok'] or not ev.get('pilot_eligibility', {}).get('annual_lead'):
                check['status'] = 'insufficient_history'
                check['issues'] = validated['issues'] or ['FIXED_ANNUAL_BASELINE_UNAVAILABLE']
                continue
            summaries = {'annual': ev['baseline']['summary']}
            for key, part in ev.get('cycle', {}).items():
                if isinstance(part, dict) and part.get('summary'):
                    summaries['cycle_' + key] = part['summary']
            for key, part in ev.get('recent', {}).items():
                for period in ('recent', 'preceding'):
                    if isinstance(part, dict) and isinstance(part.get(period), dict):
                        summaries[key + '_' + period] = part[period].get('summary', part[period])
            record = {'id': sid, 'symbol': instrument['symbol'], 'instrument': instrument,
                'relevance_reason': spec['relevance_reason'], 'window_reason': spec['window_reason'],
                'window': ev['window'], 'classification': ev['classification'],
                'baseline': ev['baseline']['summary'], 'comparisons': summaries,
                'requested_years': ev['baseline']['requested_years'],
                'missing_years': ev['baseline']['missing_years'],
                'missing_year_reasons': ev['baseline']['missing_year_reasons'],
                'required_qualifications': ev['writer_brief']['required_qualifications'],
                'provenance': derived['provenance'], 'claim_id': 'seasonal:' + sid,
                'method': 'Fixed prior 20-year calendar span with actual available observation count disclosed; inclusive calendar window; first/last sessions inside each window; provider-adjusted closes; calendar history, not returns following this news event.',
                'interpretation_limit': 'These observations do not predict a policy decision, establish news causation, or estimate the return still available in an underway window.'}
            record['evidence_sha256'] = fingerprint(record)
            result['records'].append(record)
            check.update(status='available', evidence_sha256=record['evidence_sha256'])
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            check['issues'] = [str(exc) if isinstance(exc, ValueError) else 'MALFORMED_HISTORY_INPUT']
    if result['records']:
        result.update(status='available', reason='Computed history is available for an editorial include/omit decision.')
    elif result['checks'] and all(c['status'] == 'insufficient_history' for c in result['checks']):
        result.update(status='insufficient_history', reason='The requested fixed annual sample is unavailable; news coverage remains eligible.')
    return result


def enrich_news_evidence(evidence, research):
    result = deepcopy(evidence)
    context = assess_news_seasonality(research, evidence['event'], as_of=evidence['as_of'])
    result['seasonal_context'] = context
    for record in context['records']:
        sid = 'seasonal-source:' + record['id']
        baseline, window = record['baseline'], record['window']
        claim = (f"TradeWave's {record['symbol']} calendar history for {window['start_date']} through "
            f"{window['end_date']} ({window['calendar_days']} calendar days, inclusive) uses "
            f"{baseline['n']} available annual observations, {baseline['first_year']} through "
            f"{baseline['last_year']}, within the fixed requested span {record['requested_years'][0]} through "
            f"{record['requested_years'][-1]}. Missing years: {record['missing_years']}. "
            f"Median return: {baseline['median_net_display']:+.2f}%; "
            f"positive years: {baseline['up_years']}; negative years: {baseline['down_years']}; "
            f"flat years: {baseline['flat_years']}. Worst window return: {baseline['worst_net']:+.2f}% "
            f"in {baseline['worst_year']}. Measurement: {record['instrument']['semantics']['measurement']}. "
            f"Historical classification: {record['classification']}. "
            + record['interpretation_limit'])
        result['sources'].append({'id': sid, 'url': '#seasonal-evidence-' + record['id'],
            'title': 'TradeWave historical calculation: ' + record['symbol'],
            'published_at': evidence['as_of'], 'source_type': 'derived', 'role': 'context',
            'excerpt': claim + '\nCOMPLETE COMPUTED CONTEXT: ' + json.dumps(record, ensure_ascii=False)})
        result['claims'].append({'id': record['claim_id'], 'text': claim, 'source_ids': [sid]})
    return result


def context_instructions(evidence):
    if 'seasonal_context' not in evidence:
        return ''
    return '''
SEASONAL RESEARCH STEP: This news story has a recorded seasonal-context check.
The event qualifies independently of a detected pattern. If history is unavailable
or inadequate, omit it without telling readers a pattern does not exist. If
computed context is available, decide whether one or at most two relevant records
help answer the event's reader question. A mixed history can provide useful
perspective; do not cherry-pick a favorable asset, period or sample. This is
an investor publication: context can explain why a calendar reputation deserves
caution, or why the present development matters more than a seasonal tendency.
History need not predict the event to earn a brief place in the story. Evaluate
that reader benefit before omitting it merely because it cannot forecast policy.
The output remains optional when that connection would be contrived. This is
calendar history, not an event study of rate cuts, earnings or volume spikes.
Never use bond-price returns as yield changes, or imply the calendar predicts
the Fed. Keep the news opening focused on the event. When useful, connect
history in ONE compact paragraph before what-to-watch, usually 50-100 words.
Explain the market exposure and the material qualification naturally. Do not
dump the comparison matrix into the article; code renders expandable evidence.
State the contrasts and caveats material to the paragraph's actual claims in
plain language; do not recite every internal cohort-policy qualification ID or
every subset's statistics. A small cycle sample can be qualified without making
an election-cycle thesis the subject of an ordinary news article.
The reader-facing paragraph must say what the history adds to this particular
news story. It cannot be an unrelated seasonal statistic or a sales pitch.
'''


def validate_context_decision(plan, evidence):
    context = evidence.get('seasonal_context')
    if not context or context['status'] != 'available':
        return []
    decision = plan.get('seasonal_context_decision')
    if not isinstance(decision, dict) or not isinstance(decision.get('reason'), str) or not decision['reason'].strip():
        return ['SEASONAL_CONTEXT_DECISION_MISSING']
    ids = decision.get('record_ids')
    allowed = {r['id'] for r in context['records']}
    if not isinstance(ids, list) or any(not isinstance(r, str) or r not in allowed for r in ids) or len(ids) != len(set(ids)):
        return ['SEASONAL_CONTEXT_DECISION_UNBOUND']
    if decision.get('action') == 'include' and 1 <= len(ids) <= 2:
        needed = {'seasonal:' + sid for sid in ids}
        if not needed.issubset(plan.get('claim_ids') or []):
            return ['SEASONAL_CONTEXT_PLAN_CLAIMS_MISSING']
        return []
    if decision.get('action') == 'omit' and ids == []:
        return []
    return ['SEASONAL_CONTEXT_DECISION_INVALID']


def check_context_article(article, plan, evidence):
    issues = validate_context_decision(plan, evidence)
    decision = plan.get('seasonal_context_decision') or {}
    expected = {'seasonal:' + sid for sid in decision.get('record_ids', [])} if decision.get('action') == 'include' else set()
    used, locations = set(), []
    for section in article.get('sections') or []:
        if not isinstance(section, dict):
            continue
        for paragraph in section.get('paragraphs') or []:
            if not isinstance(paragraph, dict):
                continue
            supplied = paragraph.get('claim_ids')
            refs = {r for r in supplied if isinstance(r, str) and r.startswith('seasonal:')} if isinstance(supplied, list) else set()
            if refs:
                used |= refs
                locations.append(paragraph)
    if used != expected:
        issues.append('SEASONAL_CONTEXT_ARTICLE_DIFFERS_FROM_PLAN')
    if expected and len(locations) != 1:
        issues.append('SEASONAL_CONTEXT_NEEDS_ONE_CONNECTED_PARAGRAPH')
    return issues


def render_context_details(evidence, cited_claims):
    out = []
    for record in (evidence.get('seasonal_context') or {}).get('records', []):
        if record['claim_id'] not in cited_claims:
            continue
        out.append('<details class="historical-detail" id="seasonal-evidence-' + record['id'] + '"><summary>'
                   + html.escape(record['symbol']) + ' historical evidence</summary>')
        out.append('<p>' + html.escape(record['method']) + '</p><table><thead><tr>'
                   '<th>Sample</th><th>Years</th><th>Observations</th><th>Median return</th></tr></thead><tbody>')
        labels = {'annual': 'Consecutive annual baseline', 'cycle_full': 'All available matched election-cycle years',
                  'cycle_within_baseline': 'Matched cycle years within baseline', 'cycle_earlier': 'Earlier matched cycle years',
                  'cycle_noncycle_within_baseline': 'Other years within baseline'}
        for name, row in record['comparisons'].items():
            if not row.get('n') or row.get('median_net_display') is None:
                continue
            out.append('<tr><td>' + html.escape(labels.get(name, name.replace('_', ' '))) + '</td><td>'
                + str(row['first_year']) + '–' + str(row['last_year']) + '</td><td>' + str(row['n'])
                + '</td><td>' + f"{row['median_net_display']:+.2f}%" + '</td></tr>')
        out.append('</tbody></table><p>Cycle observations within the annual baseline reuse the same years. '
                   'Small subsets provide descriptive context. These are calendar returns, not returns conditioned '
                   'on this news event. Full-window results do not measure returns remaining after publication.</p></details>')
    return '\n'.join(out)


def check_context_review(review, article, plan, evidence):
    if (evidence.get('seasonal_context') or {}).get('status') != 'available':
        return []
    checked = review.get('seasonal_context_review')
    if (not isinstance(checked, dict) or checked.get('decision_appropriate') is not True
            or not isinstance(checked.get('reason'), str) or not checked['reason'].strip()):
        return ['SEASONAL_EDITORIAL_DECISION_UNREVIEWED']
    if (plan.get('seasonal_context_decision') or {}).get('action') != 'include':
        return []
    paragraphs = [p for s in (article.get('sections') or []) if isinstance(s, dict)
                  for p in (s.get('paragraphs') or []) if isinstance(p, dict)
                  and isinstance(p.get('claim_ids'), list)
                  and any(str(r).startswith('seasonal:') for r in p['claim_ids'])]
    if (checked.get('connection_quote') not in [p.get('text') for p in paragraphs]
            or checked.get('qualifications_complete') is not True
            or checked.get('calendar_not_event_conditioned') is not True
            or not isinstance(checked.get('reader_value'), str) or not checked['reader_value'].strip()):
        return ['SEASONAL_CONNECTION_OR_QUALIFICATION_UNREVIEWED']
    return []
