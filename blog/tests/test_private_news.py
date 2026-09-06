"""Offline evidence-bound news adapter and reader review regression tests."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))
from market_policy import evaluate_lineup
from news_pipeline import render_news_html, run_news_article
from news_prompts import evidence_packet
from news_selection import NewsPolicy, validate_event
from private_news import generate_private_news, prepare_news_candidate
from reader_promise import build_reader_brief, fingerprint
from test_news_pipeline import NOW, article, packet, plan


def candidate():
    return {'candidate_id': 'fictional-news', 'kind': 'news', 'instrument': {},
            'news_packet': packet(), 'exposure_ids': ['fictional-industrial-demand'],
            'question': {'question_id': 'outlook-change', 'text': plan()['reader_question'],
                         'answer_claim_ids': ['c1', 'c2', 'c3']},
            'priority': {'consequence': 2, 'timeliness': 3, 'relevance': 2, 'completeness': 2,
                         'reason': 'The release revises the outlook and identifies an unresolved duration.',
                         'claim_ids': ['c1', 'c2', 'c3']}, 'material_news': False}


def brief(selected=True):
    data = packet()
    checked = validate_event(**data, now=NOW)
    result = build_reader_brief(research=evidence_packet(data['event'], checked), article_type='news')
    if selected:
        result.update(selected_question=plan()['reader_question'], selected_question_id='outlook-change',
                      proposed_reader_question=plan()['reader_question'])
    return result


def private_plan(selected=True):
    result = plan()
    result['headlines'] = [article()['title']]
    result['reader_promise'] = {
        'answer_support': ['news_claim:c1', 'news_claim:c3'],
        'why_now': {'text': 'The company has revised its current outlook.', 'evidence_refs': ['news:/event']},
        'risk': {'text': 'The duration of softer orders remains unquantified.', 'evidence_refs': ['news_claim:c3']},
        'qualifications': [],
        'headline_support': [{'headline': article()['title'], 'evidence_refs': ['news_claim:c1', 'news_claim:c2']}],
        'hero_brief': {'role': 'none', 'concept': 'Deliberately omit the draft image pending review.',
                       'evidence_refs': [], 'factual_implications': []}}
    if selected:
        result['reader_promise']['selected_question_id'] = 'outlook-change'
    return result


def review_for(value, selected=True):
    data = packet()
    evidence = evidence_packet(data['event'], validate_event(**data, now=NOW))
    rendered = render_news_html(value, evidence)
    quotes = {'title': value['title'], 'dek': value['dek'], 'answer': value['dek'],
              'why_now': value['sections'][0]['paragraphs'][0]['text'],
              'risk': value['sections'][1]['paragraphs'][0]['text'],
              'reader_value': value['takeaways'][0]['text']}
    refs = {'title': ['news_claim:c1', 'news_claim:c2'], 'dek': ['news_claim:c1', 'news_claim:c3'],
            'answer': ['news_claim:c1', 'news_claim:c3'], 'why_now': ['news:/event'],
            'risk': ['news_claim:c3'], 'reader_value': ['news_claim:c1']}
    report = {'article_sha256': fingerprint(rendered), 'brief_sha256': fingerprint(brief(selected)),
              'expected_question': plan()['reader_question'], 'delivered_answer': value['dek'],
              'checks': [{'check_id': key, 'judgment': 'supported', 'quote': quote,
                          'evidence_refs': refs[key],
                          'reason': 'Fixture judgment: the quoted passage answers this requirement using the named release fact.'}
                         for key, quote in quotes.items()]}
    if selected:
        report['expected_question_id'] = 'outlook-change'
    return {'passed': True, 'issues': [], 'reader_promise_review': report}


class PrivateNewsAdapterTests(unittest.TestCase):
    def test_recalculates_gate_and_never_inherits_supersession(self):
        data = candidate()
        data['news_gate'] = {'eligible': True, 'evidence_ids': ['fake']}
        data['event'] = {'event_id': 'fake', 'supersedes_development_ids': ['previous']}
        data['news_packet']['event']['supersedes_development_ids'] = ['previous']
        prepared = prepare_news_candidate(data, as_of=NOW)
        self.assertTrue(prepared['news_gate']['eligible'])
        self.assertNotIn('fake', prepared['news_gate']['evidence_ids'])
        self.assertNotIn('supersedes_development_ids', prepared['event'])
        self.assertEqual(prepared['event']['event_id'], packet()['event']['event_id'])
        self.assertEqual(len(evaluate_lineup([prepared], as_of=NOW)['selected']), 1)
        self.assertFalse(prepared['publishable'])

    def test_result_is_an_isolated_snapshot(self):
        data = candidate()
        original = copy.deepcopy(data)
        prepared = prepare_news_candidate(data, as_of=NOW)
        prepared['news_packet']['research']['sources'][0]['excerpt'] = 'changed'
        prepared['news_gate']['fact_ids'].append('changed')
        self.assertEqual(data, original)
        self.assertNotIn('changed', prepared['event']['fact_ids'])

    def test_forged_gate_cannot_pass_unverified_research(self):
        data = candidate()
        data['news_gate'] = {'eligible': True, 'issues': []}
        data['news_packet']['research']['sources'][0]['verified'] = False
        result = generate_private_news(data, as_of=NOW,
                                       send=lambda _: self.fail('No model call for unverified evidence'))
        self.assertEqual(result['status'], 'hold')
        self.assertEqual(result['provider_calls'], 0)

    def test_missing_packet_holds_without_provider(self):
        data = candidate()
        del data['news_packet']
        result = generate_private_news(data, as_of=NOW, send=lambda _: self.fail('No provider call'))
        self.assertEqual(result['provider_calls'], 0)
        self.assertFalse(result['news_gate']['eligible'])

    def test_question_cannot_claim_unavailable_evidence(self):
        data = candidate()
        data['question']['answer_claim_ids'] = ['made-up']
        prepared = prepare_news_candidate(data, as_of=NOW)
        slate = evaluate_lineup([prepared], as_of=NOW)
        self.assertFalse(slate['selected'])
        self.assertIn('ANSWER_EVIDENCE_UNBOUND', [r['code'] for r in slate['decisions'][0]['reasons']])

    def test_gate_expiry_uses_event_clock_and_revalidates_later(self):
        data = candidate()
        prepared = prepare_news_candidate(data, as_of=NOW)
        self.assertEqual(prepared['news_gate']['valid_until'], '2026-09-07T14:00:00+00:00')
        expired = prepare_news_candidate(prepared, as_of='2026-09-07T14:00:01Z')
        self.assertFalse(expired['news_gate']['eligible'])
        self.assertIn('stale_event', expired['news_gate']['issues'])

    def test_context_age_does_not_change_event_age(self):
        data = candidate()
        source = copy.deepcopy(data['news_packet']['research']['sources'][0])
        source.update(id='background', url='https://example.test/old-background', role='context',
                      published_at='2020-01-01T00:00:00Z')
        data['news_packet']['research']['sources'].append(source)
        prepared = prepare_news_candidate(data, as_of=NOW)
        self.assertTrue(prepared['news_gate']['eligible'])
        self.assertEqual(prepared['news_gate']['valid_until'], '2026-09-07T14:00:00+00:00')

    def test_renumbered_sources_preserve_evidence_and_fact_ids(self):
        data = candidate()
        before = prepare_news_candidate(data, as_of=NOW)
        data['news_packet']['event']['source_ids'] = ['renumbered']
        data['news_packet']['research']['sources'][0]['id'] = 'renumbered'
        for claim in data['news_packet']['research']['claims']:
            claim['source_ids'] = ['renumbered']
        after = prepare_news_candidate(data, as_of=NOW)
        self.assertEqual(before['event'], after['event'])
        self.assertEqual(before['news_gate']['evidence_ids'], after['news_gate']['evidence_ids'])

    def test_shorter_quote_same_retrieved_body_is_not_a_new_article(self):
        data = candidate()
        source = data['news_packet']['research']['sources'][0]
        source['provenance'] = {'body_sha256': hashlib.sha256(source['excerpt'].encode()).hexdigest()}
        old = prepare_news_candidate(data, as_of=NOW)
        slate = evaluate_lineup([old], as_of=NOW)
        covered = slate['selected'][0]['coverage_record']
        source['excerpt'] = source['excerpt'].split('. Incoming')[0] + '.'
        data['news_packet']['research']['claims'] = [data['news_packet']['research']['claims'][0]]
        data['news_packet']['research']['claims'][0]['text'] = 'Atlas now forecasts 2% to 4% sales growth.'
        data['news_packet']['event'].update(claim_ids=['c1'], development_id='changed-wording',
                                           supersedes_development_ids=['release-1'])
        data['question']['answer_claim_ids'] = ['c1']
        data['priority']['claim_ids'] = ['c1']
        new = prepare_news_candidate(data, as_of=NOW)
        self.assertEqual(old['event']['evidence_ids'], new['event']['evidence_ids'])
        result = evaluate_lineup([new], as_of=NOW, coverage=[covered])
        self.assertFalse(result['selected'])
        self.assertEqual(result['decisions'][0]['status'], 'consolidate')

    def test_future_event_margin_cannot_get_a_private_pass(self):
        data = candidate()
        data['news_packet']['event']['event_time'] = '2026-09-05T16:03:00Z'
        self.assertFalse(prepare_news_candidate(data, as_of=NOW)['news_gate']['eligible'])

    def test_active_policy_rejected(self):
        with self.assertRaises(ValueError):
            prepare_news_candidate(candidate(), as_of=NOW, policy=NewsPolicy(enabled=True))


class PrivateNewsReaderTests(unittest.TestCase):
    def run_with(self, replies, **kwargs):
        prompts, responses = [], iter(replies)
        def send(prompt):
            prompts.append(prompt)
            return json.dumps(next(responses))
        result = run_news_article(**packet(), send=send, now=NOW, reader_policy='private_v2',
                                  reader_question=candidate()['question'], **kwargs)
        return result, prompts

    def test_three_calls_deliver_text_with_visual_review_pending(self):
        with tempfile.TemporaryDirectory() as out:
            result, prompts = self.run_with([private_plan(), article(), review_for(article())], output_dir=out)
            self.assertEqual(result['status'], 'draft_ready', result)
            self.assertEqual(result['provider_calls'], 3)
            self.assertTrue(result['text_ready'])
            self.assertFalse(result['visual_ready'])
            self.assertFalse(result['publishable'])
            self.assertIn(result['article_html'], prompts[2])
            self.assertIn(fingerprint(result['article_html']), prompts[2])
            self.assertIn('No seasonal baseline', prompts[0])
            self.assertTrue(Path(result['artifact_paths']['reader_brief']).exists())

    def test_adapter_generation_uses_same_validated_packet(self):
        replies = iter([private_plan(), article(), review_for(article())])
        result = generate_private_news(candidate(), as_of=NOW, send=lambda _: json.dumps(next(replies)))
        self.assertEqual(result['status'], 'draft_ready', result)
        self.assertEqual(result['provider_calls'], 3)
        self.assertTrue(result['news_gate']['eligible'])

    def test_missing_reader_plan_holds_before_writing(self):
        result, _ = self.run_with([plan()])
        self.assertEqual(result['status'], 'hold')
        self.assertEqual(result['provider_calls'], 1)
        self.assertIn('PROMISE_MISSING', [i['code'] for i in result['validation']['reader_plan']])

    def test_selected_question_cannot_be_swapped(self):
        changed = private_plan()
        changed['reader_question'] = 'Should a reader buy Atlas stock today?'
        result, _ = self.run_with([changed])
        self.assertEqual(result['provider_calls'], 1)
        self.assertIn('SELECTED_QUESTION_CHANGED', [i['code'] for i in result['validation']['reader_plan']])

    def test_passed_editorial_review_without_bound_report_is_held(self):
        result, _ = self.run_with([private_plan(), article(), {'passed': True, 'issues': []}], max_revisions=0)
        self.assertEqual(result['status'], 'hold')
        self.assertFalse(result['text_ready'])
        self.assertIn('article_html', result)

    def test_stale_review_after_revision_fails_in_five_call_budget(self):
        old_review = review_for(article())
        first_review = copy.deepcopy(old_review)
        first_review['reader_promise_review']['checks'][0]['judgment'] = 'needs_revision'
        revised = article()
        revised['title'] = 'Fictional fixture: Atlas lowers its growth forecast'
        result, prompts = self.run_with([private_plan(), article(), first_review, revised, old_review])
        self.assertEqual(result['status'], 'hold')
        self.assertEqual(result['provider_calls'], 5)
        self.assertEqual(result['revisions'], 1)
        self.assertIn('PROMISE_REVIEW_PENDING', [i['code'] for i in result['validation']['reader_promise']['issues']])
        self.assertIn(result['article_html'], prompts[-1])

    def test_revision_gets_new_bound_review_without_extra_call(self):
        first_review = review_for(article())
        first_review['reader_promise_review']['checks'][0]['judgment'] = 'needs_revision'
        revised = article()
        revised['title'] = 'Fictional fixture: Atlas lowers its growth forecast'
        result, _ = self.run_with([private_plan(), article(), first_review, revised, review_for(revised)])
        self.assertEqual(result['status'], 'draft_ready', result)
        self.assertEqual(result['provider_calls'], 5)

    def test_unknown_policy_rejected_without_call(self):
        with self.assertRaises(ValueError):
            run_news_article(**packet(), send=lambda _: self.fail('No provider call'), now=NOW,
                             reader_policy='public')


if __name__ == '__main__':
    unittest.main()
