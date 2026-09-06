"""Explicit, frozen-input selection and drafting for SMN's private second pass.

This is a separate entry point, not a scheduler or publication switch. Inputs
include reviewed instrument/session manifests and current sourced context.
Commissioning chooses the question before article planning, without changing
the fixed history window. No network discovery or alternate-window fallback.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from cohort_policy import (build_selection_evidence, validate_selection_evidence,
                           baseline_cell, comparison_cells)
from market_policy import evaluate_lineup
from reader_promise import build_selected_reader_brief, review_reader_promise


def prepare_candidate(candidate: dict, *, as_of: str) -> dict:
    """Recalculate trusted gates; supplied eligible/score flags have no effect.

    ``seasonality`` must contain anchor_date, days and cells (or observations
    with declared coverage). A deterministic cohort:<candidate_id> evidence ID
    is bound to the recomputed record and its hash in this result. Instrument
    provenance/history quality are upstream reviewed assertions, not inferred
    from a symbol or authenticated merely by their hashes.
    """
    item = deepcopy(candidate)
    item.pop('cohort_gate', None)
    item.pop('news_gate', None)
    item.pop('context_gate', None)
    if item.get('kind') == 'news':
        from private_news import prepare_news_candidate
        return prepare_news_candidate(item, as_of=as_of)
    inputs = item.get('seasonality') or {}
    history_derivation = None
    if item.get('history_input') is not None:
        from private_history import derive_history_panel
        raw = item.pop('history_input')
        raw = raw if isinstance(raw, dict) else {}
        history_derivation = derive_history_panel(raw.get('ohlc'), raw.get('session_manifest'),
            instrument=item.get('instrument'), anchor_date=inputs.get('anchor_date'), days=inputs.get('days'),
            as_of=as_of, dataset_sha256=raw.get('dataset_sha256'), source_ref=raw.get('source_ref'),
            requested_years=raw.get('requested_years', 40))
        item['history_derivation'] = history_derivation
        item['history'] = history_derivation.get('history', {})
        # Raw price input is not retained in the prepared selection or writer
        # packet. It belongs in the trusted local source adapter's audit.
        inputs = {'anchor_date':inputs.get('anchor_date'), 'days':inputs.get('days'),
                  'observations':history_derivation.get('observations', []),
                  'coverage':history_derivation.get('coverage', {})}
        item['seasonality'] = inputs
    evidence = build_selection_evidence(
        cells=inputs.get('cells'), observations=inputs.get('observations'),
        coverage=inputs.get('coverage'), instrument=item.get('instrument'),
        anchor_date=inputs.get('anchor_date'), days=inputs.get('days'), as_of=as_of)
    mode = item.get('editorial_mode', 'current_context')
    item['editorial_mode'] = mode
    if mode != 'historical_review':
        from current_context import build_current_context
        item['context_gate'] = build_current_context(item, as_of=as_of)
        if mode != 'current_context':
            item['context_gate']['eligible'] = False
            item['context_gate']['issues'].append('UNKNOWN_EDITORIAL_MODE')
    check = validate_selection_evidence(evidence)
    evidence_id = 'cohort:' + str(item.get('candidate_id', ''))
    item['selection_evidence'] = evidence
    item['cohort_gate'] = {
        'eligible': check['ok'] and evidence.get('pilot_eligibility', {}).get('annual_lead') is True,
        'issues': [*check['issues'], *[i for i in evidence.get('issues', []) if i.get('severity') == 'hold']],
        'evidence_ids': [evidence_id],
        'evidence_sha256': hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    if mode not in {'current_context', 'historical_review'}:
        item['cohort_gate']['eligible'] = False
        item['cohort_gate']['issues'].append('UNKNOWN_EDITORIAL_MODE')
    if history_derivation is not None and history_derivation.get('status') != 'passed':
        item['cohort_gate']['eligible'] = False
        item['cohort_gate']['issues'].extend(history_derivation.get('issues') or ['HISTORY_DERIVATION_HELD'])
    cell = baseline_cell(evidence)
    item.pop('card', None)
    if cell is not None:
        card = {'schema_version': 2, 'symbol': cell['symbol'], 'resource_id': cell['resource_id'],
                'anchor_date': cell['anchor_date'], 'generated_at': as_of,
                'instrument': deepcopy(item.get('instrument') or {}),
                'angle': {'name': 'SEASONAL_CONTEXT', 'runner_up': [],
                          'reason': 'Answer the selected question with fixed-window comparisons.'},
                'story_cell': cell, 'auxiliary_cells': comparison_cells(evidence),
                'selection_evidence': evidence, 'policy_mode': 'private_v2', 'publishable': False}
        card['editorial_mode'] = mode
        if mode == 'current_context':
            # The adapter supplies facts, not a prewritten story question.
            # This provisional identity is replaced after commissioning, then
            # coverage is checked again against the actual selected question.
            item['question'] = {'question_id': 'pending:' + str(item.get('candidate_id', '')),
                'text': 'What do the current disclosures mean for ' + cell['symbol'] + ' shareholders?',
                'answer_evidence_ids': [evidence_id, *item['context_gate']['evidence_ids']]}
            card['angle'] = {'name': 'BUSINESS_TEST', 'runner_up': [],
                             'reason': 'Provisional until current-context commissioning.'}
            card['current_context'] = deepcopy(item['context_gate']['context'])
            assignment = item.get('editorial_assignment')
            card['editorial_assignment_pending'] = not bool(assignment)
            if assignment:
                from current_context import validate_assignment
                issues = validate_assignment(assignment, card['current_context'], evidence)
                if issues:
                    item['context_gate']['eligible'] = False
                    item['context_gate']['issues'].extend(issues)
                else:
                    card['editorial_assignment'] = deepcopy(assignment)
                    card['angle'] = {'name': assignment['angle'], 'runner_up': [],
                                     'reason': assignment['angle_reason']}
                    item['question'] = {'question_id': 'current:' + hashlib.sha256(
                        assignment['reader_question'].encode()).hexdigest()[:20],
                        'text': assignment['reader_question'],
                        'answer_evidence_ids': [evidence_id, *item['context_gate']['evidence_ids']]}
        if item.get('hero_asset'):
            card['hero_asset'] = deepcopy(item['hero_asset'])
        question = item.get('question') or {}
        card['selected_question'] = {k: question.get(k, '') for k in ('question_id', 'text')}
        if mode != 'historical_review' and not item['context_gate']['eligible']:
            card['reader_brief'] = {'hold_reasons': list(item['context_gate']['issues'])}
        else:
            card['reader_brief'] = build_selected_reader_brief(card, item.get('research'))
        item['card'] = card
        if card['reader_brief'].get('hold_reasons'):
            item['cohort_gate']['eligible'] = False
            item['cohort_gate']['issues'].extend(card['reader_brief']['hold_reasons'])
    return item


def select_private_candidates(candidates: list[dict], *, as_of: str,
                              coverage=(), max_articles: int = 6) -> dict:
    prepared = [prepare_candidate(c, as_of=as_of) for c in candidates]
    slate = evaluate_lineup(prepared, coverage=coverage, as_of=as_of, max_articles=max_articles)
    return {'publishable': False, 'policy_mode': 'private_v2', 'as_of': as_of,
            'candidates': prepared, 'lineup': slate}


def generate_private_article(packet: dict, *, expected_resource_id=None,
                             expected_symbol=None, expected_anchor=None,
                             send_plan=None, send_write=None, editorial_send=None) -> dict:
    """Generate one explicitly selected preview, with no horizon substitution.

    A new gate calculation runs immediately before drafting. Hero approval is a
    separate, hash-bound visual review; a text-ready draft is still private.
    """
    candidate, as_of = packet['candidate'], packet['as_of']
    instrument, inputs = candidate.get('instrument') or {}, candidate.get('seasonality') or {}
    for expected, actual, label in (
            (expected_resource_id, instrument.get('resource_id'), 'resource'),
            (expected_symbol, instrument.get('symbol'), 'symbol'),
            (expected_anchor, inputs.get('anchor_date'), 'anchor')):
        if expected is not None and str(expected) != str(actual):
            raise ValueError('Private packet does not match requested ' + label)
    selected = select_private_candidates([candidate], as_of=as_of, coverage=packet.get('coverage', ()))
    item = selected['candidates'][0]
    result = {'status': 'hold', 'publishable': False, 'policy_mode': 'private_v2',
              'selection': selected['lineup'], 'candidate_id': candidate.get('candidate_id')}
    if not selected['lineup']['selected']:
        return result
    if item['kind'] == 'news':
        from private_news import generate_private_news
        return {**result, **generate_private_news(item, as_of=as_of, send=send_write)}
    if item.get('editorial_mode') == 'current_context':
        from current_context import build_assignment_prompt, parse_assignment
        from article_llm import ArticleLLM, ArticleModelError
        # Always commission afresh here. Supplied assignment objects are useful
        # for pure inspection, not permission to bypass a live editorial choice.
        sender = send_plan if send_plan is not None else ArticleLLM(stage='commission', system='Return JSON only.')
        try:
            assignment = parse_assignment(sender(build_assignment_prompt(
                item['card']['current_context'], item['selection_evidence'])),
                item['card']['current_context'], item['selection_evidence'])
        except (ValueError, TypeError, KeyError, ArticleModelError) as exc:
            return {**result, 'hold_reason': 'current_context_assignment_failed',
                    'assignment_error_type': type(exc).__name__}
        if assignment.get('feasible') is not True:
            return {**result, 'hold_reason': 'current_context_editorial_veto', 'assignment': assignment}
        candidate = {**candidate, 'editorial_assignment': assignment}
        selected = select_private_candidates([candidate], as_of=as_of, coverage=packet.get('coverage', ()))
        item = selected['candidates'][0]
        result.update(selection=selected['lineup'], assignment=assignment)
        if not selected['lineup']['selected']:
            return result
    from angle_writer import generate_angle_article
    from article_llm import ArticleModelError
    presentation = item.get('presentation') or {}
    allowed = {k: presentation[k] for k in ('company', 'byline', 'cta_link', 'methodology_url', 'book_url') if k in presentation}
    hero = item.get('hero_asset') or {}
    try:
        generated = generate_angle_article(item['card'], research=item.get('research'),
            images=item.get('images', []), hero_url=hero.get('url', ''), run_editorial=True,
            send_plan=send_plan, send_write=send_write, editorial_send=editorial_send, **allowed)
    except ArticleModelError as exc:
        return {**result, 'hold_reason':str(exc), 'card':item['card']}
    result.update(generated, card=item['card'], publishable=False)
    if generated.get('status') == 'vetoed':
        result.update(status='hold', hold_reason='planner_veto_no_alternate_sample')
    if generated.get('html'):
        final_gate = generated.get('gate2') or generated.get('gate1') or {}
        review = (final_gate.get('editorial') or {}).get('reader_promise_review')
        result['reader_review'] = review_reader_promise(generated['html'], item['card']['reader_brief'],
            plan=generated.get('plan'), review=review, hero_asset=hero,
            hero_review=packet.get('hero_review'), trusted_reviewers=())
        # Preview packets never self-authorize a reviewer. A trusted caller may
        # run review_reader_promise separately after inspecting final pixels.
        if not result['reader_review']['text_ready']:
            result['status'] = 'hold'
        result['text_ready'] = result['status'] == 'ready'
        result['visual_ready'] = False
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', required=True, help='Frozen candidate list and explicit as_of')
    parser.add_argument('--out', required=True)
    parser.add_argument('--generate', action='store_true', help='Explicitly permit bounded model calls')
    args = parser.parse_args(argv)
    from news_pipeline import _preview_directory
    directory = _preview_directory(args.out)
    packet = json.loads(Path(args.packet).read_text(encoding='utf-8'))
    result = select_private_candidates(packet['candidates'], as_of=packet['as_of'],
        coverage=packet.get('coverage', ()), max_articles=packet.get('max_articles', 6))
    (directory/'selection.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    if args.generate:
        marker = directory/'generation-started.json'
        with marker.open('x', encoding='utf-8') as fh:
            json.dump({'as_of': packet['as_of'], 'purpose': 'No automatic paid rerun'}, fh)
        selected_ids = {s['candidate_id'] for s in result['lineup']['selected']}
        for index, candidate in enumerate(packet['candidates']):
            if candidate['candidate_id'] not in selected_ids:
                continue
            generated = generate_private_article({'candidate': candidate, 'as_of': packet['as_of'],
                                                   'coverage': packet.get('coverage', ())})
            case = directory/f'case-{index+1}'
            case.mkdir()
            (case/'result.json').write_text(json.dumps(generated, indent=2), encoding='utf-8')
            if generated.get('html') or generated.get('article_html'):
                (case/'article.html').write_text(generated.get('html') or generated['article_html'], encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
