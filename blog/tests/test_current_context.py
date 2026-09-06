"""Offline failures that must hold before paid writing, plus commissioning flow."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from test_private_selection import candidate, NOW
from private_selection import prepare_candidate, generate_private_article
from current_context import (build_current_context, parse_assignment,
                             validate_assignment, build_assignment_prompt)
from angle_writer import generate_angle_article
from angle_prompts import build_plan_prompt, build_write_prompt


def current_candidate():
    c = candidate()
    c.pop('editorial_mode', None)
    c['research'] = {'symbol': 'WMT', 'context_audit': {
        'checked_at': NOW, 'subject_symbol': 'WMT', 'status': 'reviewed',
        'reviewed_by': 'synthetic-test-adapter', 'search_scope': 'Latest issuer operating release'},
        'sources': [{'id': 1, 'url': 'https://example.test/wmt-results',
            'symbol': 'WMT', 'subject': 'Walmart operations', 'date': '2026-08-01',
            'fresh': False, 'excerpt': 'Sales grew 4%. Management retained the outlook.'}],
        'context_facts': [
            {'id': 'sales', 'role': 'current_baseline', 'event_date': '2026-08-01',
             'latest_verified': True, 'text': 'Sales grew 4%.', 'source_ids': [1]},
            {'id': 'outlook', 'role': 'development', 'event_date': '2026-08-01',
             'text': 'Management retained the outlook.', 'source_ids': [1]}]}
    return c


def assignment(angle='GROWTH_CHECK'):
    return {'feasible': True, 'hold_reason': '', 'angle': angle,
        'reader_question': 'What does sales growth tell Walmart shareholders about the outlook?',
        'reader_value': 'Whether sales growth changes the operating outlook.',
        'why_now': 'The latest quarterly sales update.', 'fact_ids': ['sales', 'outlook'],
        'story_connection': {'trigger': 'seasonal_window', 'trigger_fact_ids': [],
            'context_fact_ids': ['sales', 'outlook'], 'checkpoint_fact_ids': [],
            'relationship': 'contextual', 'connection': 'The current outlook gives the approaching seasonal period business context.',
            'reader_payoff': 'Understand what to watch as the seasonal period unfolds.'},
        'angle_reason': 'An operating update needs interpretation, with history as context.'}


class CurrentContextTests(unittest.TestCase):
    def test_default_missing_research_holds_without_any_call(self):
        c = candidate()
        c.pop('editorial_mode')
        send = Mock(side_effect=AssertionError('No paid fallback on empty research'))
        result = generate_private_article({'candidate': c, 'as_of': NOW}, send_plan=send)
        self.assertEqual(result['status'], 'hold')
        send.assert_not_called()

    def test_invalid_context_holds_before_commissioning(self):
        mutations = [
            lambda r: r['context_audit'].update(checked_at='2026-07-01'),
            lambda r: r['context_audit'].update(checked_at='2026-09-01'),
            lambda r: r['sources'][0].update(symbol='JPM'),
            lambda r: r['sources'][0].update(date='2026-09-01'),
            lambda r: r['sources'][0].update(excerpt=''),
            lambda r: r['context_facts'][0].update(source_ids=[99]),
            lambda r: r['context_facts'][0].update(event_date='2025-08-01'),
            lambda r: r['context_facts'][0].update(latest_verified=False),
            lambda r: r['context_facts'][1].update(role='next_checkpoint', event_date='2026-08-01'),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                c = current_candidate()
                mutate(c['research'])
                send = Mock()
                result = generate_private_article({'candidate': c, 'as_of': NOW}, send_plan=send)
                self.assertEqual(result['status'], 'hold')
                send.assert_not_called()

    def test_current_baseline_is_not_required_to_be_breaking_news(self):
        c = current_candidate()
        original = copy.deepcopy(c)
        item = prepare_candidate(c, as_of=NOW)
        self.assertTrue(item['context_gate']['eligible'])
        self.assertEqual(c, original)
        self.assertIn('publication:/window', item['card']['reader_brief']['why_now_evidence_refs'])
        self.assertTrue(any(r.startswith('current:') for r in item['card']['reader_brief']['why_now_evidence_refs']))

    def test_research_adapter_does_not_need_to_prewrite_the_question(self):
        c = current_candidate()
        c.pop('question')
        send = Mock(return_value=json.dumps({'feasible': False, 'hold_reason': 'No worthwhile story'}))
        result = generate_private_article({'candidate': c, 'as_of': NOW}, send_plan=send)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result['hold_reason'], 'current_context_editorial_veto')

    def test_malformed_context_returns_a_research_hold(self):
        for research in (None, [], {'sources': [None], 'context_facts': ['bad']},
                         {'context_audit': [], 'sources': 'bad', 'context_facts': {}}):
            c = current_candidate()
            c['research'] = research
            self.assertFalse(build_current_context(c, as_of=NOW)['eligible'])
            send = Mock()
            self.assertEqual(generate_private_article({'candidate': c, 'as_of': NOW}, send_plan=send)['status'], 'hold')
            send.assert_not_called()

    def test_unknown_editorial_mode_cannot_bypass_context(self):
        c = candidate()
        c['editorial_mode'] = 'typo'
        send = Mock()
        result = generate_private_article({'candidate': c, 'as_of': NOW}, send_plan=send)
        self.assertEqual(result['status'], 'hold')
        send.assert_not_called()

    def test_assignment_changes_question_and_angle_but_never_history(self):
        c = current_candidate()
        prepared = prepare_candidate(c, as_of=NOW)
        historical = copy.deepcopy(prepared['selection_evidence'])
        c['editorial_assignment'] = parse_assignment(json.dumps(assignment()), prepared['card']['current_context'], historical)
        chosen = prepare_candidate(c, as_of=NOW)
        self.assertEqual(chosen['selection_evidence'], historical)
        self.assertEqual(chosen['card']['story_cell']['years'], '20')
        self.assertEqual(chosen['card']['angle']['name'], 'GROWTH_CHECK')
        self.assertEqual(chosen['card']['reader_brief']['selected_question'], assignment()['reader_question'])

    def test_wrong_fact_and_changed_context_or_history_cannot_reuse_assignment(self):
        prepared = prepare_candidate(current_candidate(), as_of=NOW)
        context, evidence = prepared['card']['current_context'], prepared['selection_evidence']
        wrong = assignment()
        wrong['fact_ids'] = ['unknown']
        with self.assertRaises(ValueError):
            parse_assignment(json.dumps(wrong), context, evidence)
        chosen = parse_assignment(json.dumps(assignment()), context, evidence)
        changed = copy.deepcopy(context)
        changed['facts'][0]['text'] = 'A different result'
        self.assertTrue(validate_assignment(chosen, changed, evidence))
        self.assertTrue(validate_assignment(chosen, context, {**evidence, 'new': 'sample'}))

    def test_one_commissioning_call_precedes_plan_and_uses_current_evidence(self):
        c = current_candidate()
        send = Mock(side_effect=[json.dumps(assignment()), json.dumps({'feasible': False, 'veto_reason': 'Evidence does not answer the question'})])
        result = generate_private_article({'candidate': c, 'as_of': NOW}, send_plan=send,
                                         send_write=Mock(), editorial_send=Mock())
        self.assertEqual(send.call_count, 2)
        first, second = [call.args[0] for call in send.call_args_list]
        self.assertIn('CURRENT CONTEXT', first)
        self.assertIn('Sales grew 4%', first)
        self.assertIn(assignment()['reader_question'], second)
        self.assertEqual(result['status'], 'hold')

    def test_commissioning_veto_or_invalid_output_never_gets_paid_retry(self):
        for raw in ('invalid JSON', json.dumps({'feasible': False, 'hold_reason': 'No worthwhile supported story'})):
            send, write = Mock(return_value=raw), Mock()
            result = generate_private_article({'candidate': current_candidate(), 'as_of': NOW},
                                             send_plan=send, send_write=write)
            self.assertEqual(result['status'], 'hold')
            self.assertEqual(send.call_count, 1)
            write.assert_not_called()

    def test_direct_writer_requires_completed_commissioning(self):
        c = current_candidate()
        card = prepare_candidate(c, as_of=NOW)['card']
        send = Mock()
        result = generate_angle_article(card, research=c['research'], run_editorial=True,
                                        send_plan=send, send_write=send, editorial_send=send)
        self.assertEqual(result['status'], 'hold')
        send.assert_not_called()

    def test_first_history_qualification_and_company_opening_are_separate(self):
        c = current_candidate()
        # A genuine recent-history conflict, without altering the fixed baseline.
        for row in c['seasonality']['observations']:
            if row['year'] >= 2021:
                row['net'] = -1
        prepared = prepare_candidate(c, as_of=NOW)
        quals = prepared['card']['reader_brief']['required_qualifications']
        self.assertTrue(any(q['placement'] == 'first_seasonal_claim' for q in quals))
        prompt = build_write_prompt(prepared['card'], {'charts': [], 'word_budget': 600}, c['research'])
        self.assertIn('actual opening paragraph', prompt)
        self.assertNotIn('Then <section id="key-takeaways">', prompt)

    def test_visible_contradiction_cannot_move_into_collapsed_details(self):
        from reader_promise import review_reader_promise
        brief = {'editorial_mode': 'current_context', 'required_qualifications': []}
        opening = '<h1>Company</h1><p class="dek">Context</p><p class="direct-answer">Business fact.</p>'
        history = '<p class="seasonal-context">Earlier and recent history disagree.</p>'
        for fragment, expected in ((history, False), ('<details><summary>Data</summary>'+history+'</details>', True)):
            issues = review_reader_promise(opening+fragment, brief)['issues']
            self.assertEqual(any(i['code'] == 'PROMISE_SEASONAL_CONTEXT' for i in issues), expected)

    def test_opening_cannot_be_displaced_by_a_methodology_preamble(self):
        from reader_promise import review_reader_promise
        brief = {'editorial_mode': 'current_context', 'required_qualifications': []}
        article = '<p class="dek">Context</p><p>Twenty historical observations.</p><p class="direct-answer">Business fact.</p><p class="seasonal-context">Mixed history.</p>'
        self.assertIn('PROMISE_OPENING_ORDER', [i['code'] for i in review_reader_promise(article, brief)['issues']])

    def test_old_background_cannot_pass_as_recent_news(self):
        prepared = prepare_candidate(current_candidate(), as_of=NOW)
        value = assignment()
        value['story_connection'].update(trigger='recent_development', trigger_fact_ids=['sales'])
        with self.assertRaisesRegex(ValueError, 'Old background'):
            parse_assignment(json.dumps(value), prepared['card']['current_context'], prepared['selection_evidence'])

    def test_missing_connection_and_invented_checkpoint_hold(self):
        prepared = prepare_candidate(current_candidate(), as_of=NOW)
        for change in ('missing', 'checkpoint'):
            value = assignment()
            if change == 'missing':
                value.pop('story_connection')
            else:
                value['story_connection']['checkpoint_fact_ids'] = ['sales']
            with self.assertRaises(ValueError):
                parse_assignment(json.dumps(value), prepared['card']['current_context'], prepared['selection_evidence'])

    def test_late_publication_hook_cannot_pass_by_quoting_the_ending(self):
        from reader_promise import review_reader_promise, fingerprint
        prepared = prepare_candidate(current_candidate(), as_of=NOW)
        brief = prepared['card']['reader_brief']
        prose = ('<h1>Company</h1><p class="dek">Background</p><p class="direct-answer">Sales grew.</p>'
                 '<p>More fundamentals.</p><p>Even more fundamentals.</p>'
                 '<p class="seasonal-context">The seasonal window starts soon.</p>')
        review = {'article_sha256': fingerprint(prose), 'brief_sha256': fingerprint(brief),
            'checks': [{'check_id': 'publication_reason', 'judgment': 'supported',
                'quote': 'The seasonal window starts soon.', 'evidence_refs': ['publication:/window', 'current:sales'],
                'reason': 'This passage is too late, even though its facts are supported.'}]}
        issues = review_reader_promise(prose, brief, review=review)['issues']
        self.assertTrue(any(i['code'] == 'PROMISE_PASSAGE_MISSING' and 'publication_reason' in i['detail'] for i in issues))


if __name__ == '__main__':
    unittest.main()
