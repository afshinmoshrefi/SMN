import copy
import unittest

from market_policy import metadata_digest
from news_seasonality import (assess_news_seasonality, enrich_news_evidence,
    validate_context_decision, check_context_article, check_context_review, render_context_details)
import test_private_history as history_fixture


def history_request():
    source = history_fixture.PrivateHistoryTests().args()
    source['anchor_date'] = '2026-09-17'
    for row in source['ohlc']:
        row['date'] = row['date'].replace('-08-', '-09-')
    source['session_manifest']['sessions'] = sorted(d.replace('-08-', '-09-') for d in source['session_manifest']['sessions'])
    source['requested_years'] = 40
    source['session_manifest']['start_date'] = '1986-01-01'
    source['session_manifest']['sessions'] = sorted(source['session_manifest']['sessions'] +
        [f'{year}-09-{day}' for year in range(1986, 2006) for day in (17, 18, 20)])
    instrument = source['instrument']
    instrument.update(asset_class='equity', semantics={'measurement': 'adjusted_price_return',
        'units': 'percent_return', 'return_basis': 'provider_adjusted_price',
        'adjustment': 'provider_adjusted_close_ratio', 'currency': 'USD', 'calendar_id': 'SYNTHETIC',
        'session_model': 'exchange', 'timezone': 'America/New_York'})
    instrument['provenance'] = {'method': 'reviewed_source_manifest', 'source_uri': 'synthetic fixture',
        'reviewed_at': source['as_of'], 'evidence_id': 'test-identity', 'metadata_sha256': metadata_digest(instrument)}
    request = {'event_id': 'test-event', 'checked_at': source['as_of'], 'reviewed_by': 'synthetic test',
        'scope_reason': 'Exercise the news context contract using synthetic prices.',
        'instruments': [{'id': 'test-history', 'instrument': instrument,
            'relevance_reason': 'Synthetic company affected by the fictional event.',
            'window_reason': 'Window fixed before inspecting the synthetic returns.',
            'seasonality': {'anchor_date': source['anchor_date'], 'days': source['days']},
            'history_input': {k: source[k] for k in ('ohlc', 'session_manifest', 'dataset_sha256', 'source_ref', 'requested_years')}}]}
    return request, source['as_of']


class NewsSeasonalityTests(unittest.TestCase):
    def evidence(self):
        request, now = history_request()
        return enrich_news_evidence({'as_of': now, 'event': {'event_id': 'test-event'},
            'sources': [], 'claims': []}, {'seasonal_research': request})

    def test_missing_research_is_unavailable_not_no_pattern(self):
        result = assess_news_seasonality({}, {'event_id': 'test'}, as_of='2026-09-06T16:00:00Z')
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('not evidence of no pattern', result['reason'])

    def test_fixed_history_is_recomputed_and_current_event_is_unchanged(self):
        evidence = self.evidence()
        record = evidence['seasonal_context']['records'][0]
        self.assertEqual(record['baseline']['median_net_display'], 1)
        self.assertEqual(record['baseline']['n'], 20)
        self.assertEqual(record['window']['end_date'], '2026-09-20')
        self.assertEqual(evidence['event'], {'event_id': 'test-event'})
        self.assertEqual(evidence['claims'][0]['id'], 'seasonal:test-history')
        self.assertTrue(evidence['sources'][0]['url'].startswith('#seasonal-evidence-'))

    def test_shorter_available_series_is_not_mislabeled_twenty_observations(self):
        request, now = history_request()
        spec = request['instruments'][0]
        spec['history_input']['ohlc'] = [r for r in spec['history_input']['ohlc'] if r['date'] >= '2008-01-01']
        evidence = enrich_news_evidence({'as_of': now, 'event': {'event_id': 'test-event'},
            'sources': [], 'claims': []}, {'seasonal_research': request})
        record = evidence['seasonal_context']['records'][0]
        self.assertEqual(record['baseline']['n'], 18)
        self.assertEqual(record['missing_years'], [2006, 2007])
        self.assertIn('18 available annual observations', evidence['claims'][0]['text'])
        self.assertNotIn('20 annual observations', record['method'])

    def test_malformed_paragraph_claims_do_not_crash_context_check(self):
        evidence = self.evidence()
        plan = {'seasonal_context_decision': {'action': 'include', 'record_ids': ['test-history'],
            'reason': 'Needed context'}, 'claim_ids': ['seasonal:test-history']}
        for bad in (None, 3, 'seasonal:test-history'):
            article = {'sections': [{'paragraphs': [{'text': 'Malformed.', 'claim_ids': bad}]}]}
            self.assertIn('SEASONAL_CONTEXT_ARTICLE_DIFFERS_FROM_PLAN', check_context_article(article, plan, evidence))

    def test_bad_history_cannot_be_promoted_by_an_eligible_boolean(self):
        request, now = history_request()
        request['instruments'][0]['eligible'] = True
        request['instruments'][0]['history_input']['ohlc'].pop(4)
        original = copy.deepcopy(request)
        result = assess_news_seasonality({'seasonal_research': request}, {'event_id': 'test-event'}, as_of=now)
        self.assertEqual(result['records'], [])
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(request, original)

    def test_a_different_event_cannot_reuse_the_research_scope(self):
        request, now = history_request()
        result = assess_news_seasonality({'seasonal_research': request}, {'event_id': 'other'}, as_of=now)
        self.assertEqual(result['records'], [])

    def test_include_and_omit_are_both_possible_but_need_a_reason(self):
        evidence = self.evidence()
        self.assertTrue(validate_context_decision({}, evidence))
        omit = {'seasonal_context_decision': {'action': 'omit', 'record_ids': [], 'reason': 'Does not clarify this news question.'}}
        self.assertEqual(validate_context_decision(omit, evidence), [])
        include = {'seasonal_context_decision': {'action': 'include', 'record_ids': ['test-history'], 'reason': 'Clarifies timing.'},
                   'claim_ids': ['seasonal:test-history']}
        self.assertEqual(validate_context_decision(include, evidence), [])
        paragraph = {'text': 'A connected seasonal perspective.', 'claim_ids': ['seasonal:test-history']}
        article = {'sections': [{'paragraphs': [paragraph]}]}
        self.assertEqual(check_context_article(article, include, evidence), [])
        self.assertTrue(check_context_article(article, omit, evidence))
        article['sections'][0]['paragraphs'].append(paragraph)
        self.assertIn('SEASONAL_CONTEXT_NEEDS_ONE_CONNECTED_PARAGRAPH', check_context_article(article, include, evidence))

    def test_semantic_review_requires_actual_passage_and_event_study_distinction(self):
        evidence = self.evidence()
        plan = {'seasonal_context_decision': {'action': 'include', 'record_ids': ['test-history']}}
        article = {'sections': [{'paragraphs': [{'text': 'The exact connection.', 'claim_ids': ['seasonal:test-history']}]}]}
        review = {'seasonal_context_review': {'decision_appropriate': True, 'reason': 'Relevant context.',
            'connection_quote': 'The exact connection.', 'reader_value': 'Explains the timing.',
            'qualifications_complete': True, 'calendar_not_event_conditioned': True}}
        self.assertEqual(check_context_review(review, article, plan, evidence), [])
        review['seasonal_context_review']['calendar_not_event_conditioned'] = False
        self.assertTrue(check_context_review(review, article, plan, evidence))
        review['seasonal_context_review'].update(calendar_not_event_conditioned=True, connection_quote='Another draft.')
        self.assertTrue(check_context_review(review, article, plan, evidence))

    def test_only_cited_context_gets_an_expandable_table(self):
        evidence = self.evidence()
        self.assertEqual(render_context_details(evidence, []), '')
        rendered = render_context_details(evidence, ['seasonal:test-history'])
        self.assertIn('<details', rendered)
        self.assertIn('calendar returns, not returns conditioned', rendered)
        self.assertIn('+1.00%', rendered)


if __name__ == '__main__':
    unittest.main()
