"""Private integration tests use synthetic observations, never paid requests."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

BLOG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BLOG))
from private_selection import prepare_candidate, select_private_candidates, generate_private_article
from angle_writer import generate_angle_article
from test_first_pass_integration import actual_functions

NOW = '2026-08-21T12:00:00Z'


def candidate():
    c = json.loads((BLOG/'tests/fixtures/private_equity_candidate.json').read_text())
    c['candidate_id'] = 'synthetic-fixed20'
    c['question']['answer_evidence_ids'] = ['cohort:' + c['candidate_id']]
    c['priority']['evidence_ids'] = ['cohort:' + c['candidate_id']]
    c['seasonality'] = {'anchor_date': '2026-08-21', 'days': 30,
        'observations': [{'year': y, 'net': 1 + y % 5, 'mfe': 8, 'mae': -2} for y in range(1986, 2026)],
        'coverage': {'start_year': 1986, 'end_year': 2025, 'source_ref': 'synthetic-panel'}}
    c['research'] = {}
    return c


class PrivateSelectionTests(unittest.TestCase):
    def test_fixed_baseline_ignores_supplied_card_and_gate_and_is_nonmutating(self):
        c = candidate()
        c.update(card={'story_cell': {'years': '5'}}, cohort_gate={'eligible': False})
        original = copy.deepcopy(c)
        result = select_private_candidates([c], as_of=NOW)
        self.assertEqual(c, original)
        item = result['candidates'][0]
        self.assertEqual(item['card']['story_cell']['years'], '20')
        self.assertEqual(item['card']['story_cell']['n'], 20)
        self.assertEqual(result['lineup']['decisions'][0]['status'], 'select')
        self.assertFalse(result['publishable'])

    def test_incomplete_history_cannot_be_overridden_and_makes_no_calls(self):
        c = candidate()
        c['seasonality']['observations'] = c['seasonality']['observations'][-5:]
        c['cohort_gate'] = {'eligible': True, 'issues': [], 'evidence_ids': ['fake']}
        send = Mock(side_effect=AssertionError('Held inputs must not call models'))
        result = generate_private_article({'candidate': c, 'as_of': NOW}, send_plan=send, send_write=send, editorial_send=send)
        self.assertEqual(result['status'], 'hold')
        send.assert_not_called()

    def test_unknown_market_measurement_holds_before_writing(self):
        c = candidate()
        c['instrument']['semantics']['measurement'] = 'unknown'
        send = Mock()
        result = generate_private_article({'candidate': c, 'as_of': NOW}, send_plan=send)
        self.assertEqual(result['status'], 'hold')
        send.assert_not_called()

    def test_pipeline_binding_rejects_other_instrument_or_anchor(self):
        for kw in ({'expected_symbol': 'JPM'}, {'expected_resource_id': '16'}, {'expected_anchor': '2026-09-01'}):
            with self.assertRaises(ValueError):
                generate_private_article({'candidate': candidate(), 'as_of': NOW}, **kw)

    def test_veto_holds_without_alternate_horizon(self):
        send = Mock(return_value=json.dumps({'feasible': False, 'veto_reason': 'No useful reader answer'}))
        result = generate_private_article({'candidate': candidate(), 'as_of': NOW}, send_plan=send,
            send_write=Mock(side_effect=AssertionError('No write after veto')), editorial_send=Mock())
        self.assertEqual(result['status'], 'hold')
        self.assertEqual(result['hold_reason'], 'planner_veto_no_alternate_sample')
        self.assertEqual(send.call_count, 1)

    def test_provider_failure_holds_without_paid_retry(self):
        from article_llm import ArticleModelError
        send=Mock(side_effect=ArticleModelError('Article response was incomplete'))
        result=generate_private_article({'candidate':candidate(),'as_of':NOW},send_plan=send)
        self.assertEqual(result['status'],'hold')
        self.assertEqual(send.call_count,1)

    def test_card_swap_cannot_license_new_sample_or_dropped_comparison(self):
        for mutate in (lambda c: c['story_cell'].update(years='5'),
                       lambda c: c.update(auxiliary_cells=[]),
                       lambda c: c.update(symbol='JPM')):
            card = prepare_candidate(candidate(), as_of=NOW)['card']
            mutate(card)
            send = Mock()
            result = generate_angle_article(card, run_editorial=True, send_plan=send, send_write=send, editorial_send=send)
            self.assertEqual(result['status'], 'hold')
            send.assert_not_called()

    def test_reader_brief_cannot_disable_or_omit_private_obligations(self):
        for mutate in (lambda c: c.update(reader_brief={'policy':'legacy'}),
                       lambda c: c['reader_brief'].update(required_qualifications=[]),
                       lambda c: c['selected_question'].update(text='A different question')):
            card = prepare_candidate(candidate(), as_of=NOW)['card']
            mutate(card)
            send = Mock()
            result = generate_angle_article(card, run_editorial=True, send_plan=send, send_write=send, editorial_send=send)
            self.assertEqual(result['status'], 'hold')
            send.assert_not_called()

    def test_invalid_raw_history_replaces_supplied_pass_flags_and_drops_prices(self):
        c=candidate()
        c['history_input']={'ohlc':[{'close':12345}]}
        prepared=prepare_candidate(c,as_of=NOW)
        self.assertFalse(prepared['cohort_gate']['eligible'])
        self.assertNotIn('history_input',prepared)
        self.assertNotIn('12345',json.dumps(prepared))

    def test_private_chrome_has_one_summary_and_useful_canonical_stats(self):
        import angle_chrome
        card=prepare_candidate(candidate(),as_of=NOW)['card']
        chrome=angle_chrome.build_chrome(card)
        prose='<h1>Test</h1><p class="dek">Test finding</p><section id="key-takeaways"><h2>Summary</h2><p class="direct-answer">The answer.</p><div class="key-takeaways-box"><h2>Key Takeaways</h2><ul><li>One useful risk.</li></ul></div></section>{{META_STRIP}}{{KEY_STATS}}{{METHODOLOGY}}'
        html=angle_chrome.assemble_article(prose,chrome)['html']
        self.assertEqual(html.count('<h2>Key Takeaways</h2>'),1)
        self.assertIn('The answer.',html)
        self.assertIn('One useful risk.',html)
        self.assertIn('Median ending return',html)
        self.assertIn('Higher closes',html)
        self.assertNotIn('Cumulative Return is',html)

    def test_annual_contrasts_are_prominent_and_not_fabricated_for_consistent_views(self):
        from reader_promise import build_reader_brief
        from cohort_policy import build_selection_evidence, baseline_cell
        fixtures=json.loads((BLOG/'tests/fixtures/cohort_policy_cases.json').read_text())
        data={k:v for k,v in fixtures['JNJ'].items() if k!='snapshot_sha256'}
        evidence=build_selection_evidence(**data)
        brief=build_reader_brief({'story_cell':baseline_cell(evidence),'selection_evidence':evidence})
        byid={q['id']:q for q in brief['required_qualifications']}
        self.assertEqual(byid['cohort.annual_recency_5']['placement'],'preview_or_opening')
        self.assertNotIn('cohort.era_sensitive',byid)

    def test_pipeline_private_branch_precedes_legacy_services_and_blocks_publish(self):
        fn = actual_functions('angle_pipeline.py', ['generate_angle_news_article'])['generate_angle_news_article']
        with patch('private_selection.generate_private_article', return_value={'status': 'hold'}) as private:
            for kw in ({'publish': True}, {'angle_index': 1}):
                with self.assertRaises(ValueError):
                    fn('2', 'WMT', private_preview={'candidate': {}}, **kw)
            private.assert_not_called()
            result = fn('2', 'WMT', anchor='2026-08-21', private_preview={'candidate': {}})
            self.assertEqual(result['status'], 'hold')
            private.assert_called_once()


if __name__ == '__main__':
    unittest.main()
