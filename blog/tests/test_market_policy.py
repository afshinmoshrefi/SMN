import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_policy import (evaluate_lineup, evaluate_market, instrument_from_audit,
                           instrument_key, metadata_digest)


NOW = '2026-08-21T12:00:00Z'
FIXTURE = Path(__file__).parent / 'fixtures' / 'private_equity_candidate.json'


def equity():
    return json.loads(FIXTURE.read_text())


def attest(candidate):
    instrument = candidate['instrument']
    instrument['provenance']['metadata_sha256'] = metadata_digest(instrument)
    candidate['history']['instrument_key'] = instrument_key(instrument)
    candidate['history']['calendar_id'] = instrument['semantics']['calendar_id']
    return candidate


def news(cid='oil-decision', event_id='oil-meeting', evidence='passage-oil-1', fact='oil-decision-1'):
    c = equity()
    c.update(candidate_id=cid, kind='news', material_news=True,
             exposure_ids=['oil-supply'], question={'question_id': 'oil-supply-change',
                'text': 'What changes in oil supply and why does it matter?',
                'answer_evidence_ids': [evidence]})
    c['instrument'].update(resource_id='7', exchange='COMM', symbol='CL',
                           series_id='unknown-continuous', asset_class='commodity')
    c['instrument']['semantics'] = {}
    c['instrument']['provenance'] = {}
    c['history'] = None
    c['event'] = {'event_id': event_id, 'development_id': fact,
                  'occurred_at': '2026-08-21T09:00:00Z', 'fact_ids': [fact],
                  'evidence_ids': [evidence]}
    c['news_gate'] = {**copy.deepcopy(c['event']), 'eligible': True, 'issues': [],
                      'valid_until': '2026-08-22T09:00:00Z'}
    c['priority'].update(consequence=3, timeliness=3, evidence_ids=[evidence])
    return c


def rec(candidate):
    return evaluate_lineup([candidate], as_of=NOW)['selected'][0]['coverage_record']


def codes(result):
    return {reason['code'] for reason in result['reasons']}


class MarketEligibilityTests(unittest.TestCase):
    def test_reviewed_equity_fixture_passes_with_precise_non_total_return_basis(self):
        c = equity()
        result = evaluate_market(c['instrument'], c['history'], as_of=NOW)
        self.assertTrue(result['seasonal_eligible'], result['reasons'])
        self.assertTrue(result['core_preference'])
        self.assertEqual(result['measurement'], 'adjusted_price_return')

    def test_key_is_shared_exact_five_field_json_contract(self):
        identity = {'resource_id': 5, 'provider': ' TW-appserver ', 'exchange': 'INDX',
                    'symbol': 'DJI', 'series_id': 'price'}
        normalized = {'resource_id': '5', 'provider': 'TW-appserver', 'exchange': 'INDX',
                      'symbol': 'DJI', 'series_id': 'price'}
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(instrument_key(identity), digest)
        for field in normalized:
            changed = dict(normalized, **{field: normalized[field] + '-other'})
            self.assertNotEqual(instrument_key(changed), digest)

    def test_missing_identity_does_not_become_certified_by_symbol(self):
        c = equity()
        del c['instrument']['provider']
        attest(c)
        result = evaluate_market(c['instrument'], c['history'], as_of=NOW)
        self.assertIn('IDENTITY_MISSING', codes(result))
        self.assertFalse(result['seasonal_eligible'])

    def test_manifest_is_bound_to_identity_class_and_semantics(self):
        for field in ('symbol', 'asset_class', 'semantics'):
            with self.subTest(field=field):
                c = equity()
                if field == 'semantics':
                    c['instrument'][field]['return_basis'] = 'total_return'
                else:
                    c['instrument'][field] = 'other'
                result = evaluate_market(c['instrument'], c['history'], as_of=NOW)
                self.assertIn('SEMANTICS_UNVERIFIED', codes(result))

    def test_model_verified_flag_is_not_a_manifest(self):
        c = equity()
        c['instrument']['provenance'] = {'verified': True, 'method': 'llm',
                                         'metadata_sha256': metadata_digest(c['instrument'])}
        self.assertIn('SEMANTICS_UNVERIFIED', codes(evaluate_market(c['instrument'], c['history'], as_of=NOW)))

    def test_dow_indices_require_supplied_price_index_methodology(self):
        for symbol in ('DJI', 'DJT', 'DJU'):
            with self.subTest(symbol=symbol):
                c = equity()
                c['instrument'].update(resource_id='5', exchange='INDX', symbol=symbol,
                                       series_id='price-index', asset_class='equity_index')
                c['instrument']['semantics'].update(measurement='price_index', return_basis='price_return',
                                                    index_methodology='source-reviewed-price-weighted-average')
                attest(c)
                result = evaluate_market(c['instrument'], c['history'], as_of=NOW)
                self.assertTrue(result['seasonal_eligible'], result['reasons'])
                del c['instrument']['semantics']['index_methodology']
                attest(c)
                self.assertIn('INDEX_METHODOLOGY_MISSING', codes(evaluate_market(c['instrument'], c['history'], as_of=NOW)))

    def test_us10y_resources_stay_distinct_and_yield_needs_own_path(self):
        keys = []
        for rid, exchange in [('5', 'INDX'), ('10', 'GBOND')]:
            c = equity()
            c['instrument'].update(resource_id=rid, exchange=exchange, symbol='US10Y',
                                   series_id='yield', asset_class='rate')
            c['instrument']['semantics'].update(measurement='yield_level', units='basis_points', return_basis='yield_change')
            attest(c)
            result = evaluate_market(c['instrument'], c['history'], as_of=NOW)
            self.assertFalse(result['seasonal_eligible'])
            self.assertIn('SPECIALIZED_ANALYSIS_REQUIRED', codes(result))
            keys.append(result['instrument_key'])
        self.assertNotEqual(*keys)

    def test_bond_price_is_not_allowed_to_claim_yield_or_total_return(self):
        c = equity()
        c['instrument'].update(asset_class='bond')
        c['instrument']['semantics'].update(measurement='bond_price', return_basis='total_return')
        attest(c)
        self.assertIn('RETURN_BASIS_MISMATCH', codes(evaluate_market(c['instrument'], c['history'], as_of=NOW)))

    def test_futures_require_contract_and_roll_metadata(self):
        c = equity()
        c['instrument'].update(resource_id='7', exchange='COMM', symbol='CL', series_id='continuous', asset_class='commodity')
        c['instrument']['semantics'].update(measurement='continuous_futures', return_basis='continuous_series_change')
        attest(c)
        self.assertIn('FUTURES_DEFINITION_INCOMPLETE', codes(evaluate_market(c['instrument'], c['history'], as_of=NOW)))
        c['instrument']['semantics'].update(contract_definition='WTI chain', settlement='official settlement',
             multiplier='1000 barrels', denominator_method='positive_entry_price',
             roll_rule='reviewed provider schedule', back_adjustment='reviewed additive adjustment')
        attest(c)
        self.assertIn('FUTURES_DENOMINATOR_UNCHECKED', codes(evaluate_market(c['instrument'], c['history'], as_of=NOW)))
        c['history']['denominator_checks_passed'] = True
        self.assertTrue(evaluate_market(c['instrument'], c['history'], as_of=NOW)['seasonal_eligible'])
        c['history']['nonpositive_values'] = True
        self.assertIn('NONPOSITIVE_VALUES', codes(evaluate_market(c['instrument'], c['history'], as_of=NOW)))

    def test_fx_requires_quote_and_carry_distinction(self):
        c = equity()
        c['instrument'].update(resource_id='9', exchange='FOREX', symbol='EURUSD', series_id='spot', asset_class='fx')
        c['instrument']['semantics'].update(measurement='fx_spot', return_basis='spot_change_excluding_carry')
        attest(c)
        self.assertIn('FX_QUOTE_UNDEFINED', codes(evaluate_market(c['instrument'], c['history'], as_of=NOW)))
        c['instrument']['semantics'].update(base_currency='EUR', quote_currency='USD', close_convention='reviewed NY close')
        attest(c)
        self.assertTrue(evaluate_market(c['instrument'], c['history'], as_of=NOW)['seasonal_eligible'])
        c['instrument']['semantics']['return_basis'] = 'carry_return'
        attest(c)
        self.assertIn('RETURN_BASIS_MISMATCH', codes(evaluate_market(c['instrument'], c['history'], as_of=NOW)))

    def test_crypto_weekend_cannot_use_equity_freshness(self):
        c = equity()
        c['instrument'].update(resource_id='16', exchange='CC', symbol='BTC-USD', series_id='spot', asset_class='crypto')
        c['instrument']['semantics'].update(measurement='crypto_spot', return_basis='spot_change',
                                           calendar_id='UTC-7D', session_model='seven_day', timezone='UTC')
        attest(c)
        c['history'].update(calendar_checked_as_of='2026-09-06', last_observation_date='2026-09-04', expected_latest_date='2026-09-04')
        self.assertIn('CRYPTO_DAILY_FRESHNESS', codes(evaluate_market(c['instrument'], c['history'], as_of='2026-09-06T12:00:00Z')))
        c['history'].update(last_observation_date='2026-09-05', expected_latest_date='2026-09-05')
        self.assertTrue(evaluate_market(c['instrument'], c['history'], as_of='2026-09-06T12:00:00Z')['seasonal_eligible'])
        spot_key = instrument_key(c['instrument'])
        futures = dict(c['instrument'], resource_id='7', exchange='COMM', symbol='BTC', series_id='futures')
        self.assertNotEqual(spot_key, instrument_key(futures))

    def test_archived_replay_clock_is_explicit(self):
        c = equity()
        self.assertTrue(evaluate_market(c['instrument'], c['history'], as_of=NOW)['seasonal_eligible'])
        self.assertIn('FRESHNESS_UNKNOWN', codes(evaluate_market(c['instrument'], c['history'], as_of='2026-09-06T12:00:00Z')))

    def test_unknown_gaps_invalid_values_stale_and_partial_hold(self):
        cases = [('unexplained_gaps', None, 'HISTORY_GAPS'), ('unexplained_gaps', ['2025-08-22'], 'HISTORY_GAPS'),
                 ('invalid_values', ['bad close'], 'HISTORY_INVALID_VALUES'),
                 ('last_observation_date', '2026-08-19', 'HISTORY_STALE'),
                 ('last_observation_date', '2026-08-21', 'HISTORY_UNEXPECTED_SESSION'),
                 ('window_checks_passed', False, 'WINDOW_QUALITY_UNVERIFIED')]
        for field, value, code in cases:
            with self.subTest(field=field, value=value):
                c = equity()
                c['history'][field] = value
                self.assertIn(code, codes(evaluate_market(c['instrument'], c['history'], as_of=NOW)))

    def test_audit_adapter_does_not_certify_from_row_count_or_ticker(self):
        c = equity()
        observed = {'row_count': 19393, 'min_date': '1951-01-02', 'max_date': '2026-09-04',
                    'sha256': 'a' * 64, 'verified': True, 'close': 999999}
        item = instrument_from_audit(c['instrument'], observed, asset_class='equity')
        self.assertNotIn('close', item['audit_observations'])
        self.assertNotIn('verified', item['audit_observations'])
        result = evaluate_market(item, None, as_of=NOW)
        self.assertFalse(result['seasonal_eligible'])
        self.assertIn('MEASUREMENT_UNKNOWN', codes(result))

    def test_etf_structure_is_explicit(self):
        c = equity()
        c['instrument'].update(resource_id='11', exchange='ETF', symbol='SPY', asset_class='etf')
        c['instrument']['semantics'].update(measurement='fund_total_return', return_basis='total_return')
        attest(c)
        self.assertIn('FUND_STRUCTURE_REVIEW_REQUIRED', codes(evaluate_market(c['instrument'], c['history'], as_of=NOW)))
        c['instrument']['semantics']['fund_structure'] = 'unlevered_plain'
        attest(c)
        result = evaluate_market(c['instrument'], c['history'], as_of=NOW)
        self.assertTrue(result['seasonal_eligible'])
        self.assertTrue(result['core_preference'])


class LineupTests(unittest.TestCase):
    def test_seasonal_eligible_flag_cannot_bypass_either_gate(self):
        for change in ('cohort', 'market'):
            c = equity()
            c['seasonal_eligible'] = True
            if change == 'cohort':
                c['cohort_gate'] = {'eligible': False, 'issues': ['insufficient history'], 'evidence_ids': ['fixture-cohort-panel-1']}
            else:
                c['instrument']['semantics']['measurement'] = 'unknown'
            result = evaluate_lineup([c], as_of=NOW)
            self.assertFalse(result['selected'])

    def test_important_news_survives_unknown_futures_measurement(self):
        c = news()
        result = evaluate_lineup([c], as_of=NOW)
        self.assertEqual(len(result['selected']), 1)
        self.assertFalse(result['decisions'][0]['market']['seasonal_eligible'])
        self.assertFalse(result['publishable'])

    def test_macro_news_can_omit_instrument(self):
        c = news()
        c['instrument'] = {}
        self.assertEqual(len(evaluate_lineup([c], as_of=NOW)['selected']), 1)

    def test_fabricated_date_or_reused_gate_holds(self):
        for field, value in [('occurred_at', '2026-08-21T10:00:00Z'), ('event_id', 'renamed-event')]:
            c = news()
            c['event'][field] = value
            result = evaluate_lineup([c], as_of=NOW)
            self.assertFalse(result['selected'])
            self.assertIn('NEWS_GATE_IDENTITY_MISMATCH', codes(result['decisions'][0]))

    def test_news_gate_expiry_holds_with_omission_reason(self):
        c = news()
        c['news_gate']['valid_until'] = '2026-08-20T12:00:00Z'
        result = evaluate_lineup([c], as_of=NOW)
        self.assertFalse(result['selected'])
        self.assertEqual(result['omitted_material_events'][0]['candidate_id'], c['candidate_id'])

    def test_same_event_new_ticker_consolidates(self):
        old = news()
        new = news('oil-etf')
        new['instrument'].update(resource_id='11', exchange='ETF', symbol='USO', series_id='fund')
        result = evaluate_lineup([old, new], as_of=NOW)
        self.assertEqual(len(result['selected']), 1)
        self.assertEqual(result['decisions'][1]['status'], 'consolidate')

    def test_new_question_label_with_same_answer_evidence_is_not_novel(self):
        old = news()
        c = news('another-angle')
        c['question']['question_id'] = 'another-label'
        result = evaluate_lineup([c], coverage=[rec(old)], as_of=NOW)
        self.assertIn('ANSWER_ALREADY_COVERED', codes(result['decisions'][0]))

    def test_new_event_overrides_recent_ticker_coverage(self):
        old = news()
        c = news('inventory', 'inventory-release', 'passage-inventory', 'inventory-fact')
        result = evaluate_lineup([c], coverage=[rec(old)], as_of=NOW)
        self.assertEqual(len(result['selected']), 1)
        self.assertIn('NEW_EVENT_OVERRIDES_TICKER_COOLDOWN', codes(result['decisions'][0]))

    def test_development_rename_without_new_evidence_does_not_update(self):
        old = news()
        c = news('rename', fact='new-label')
        c['event']['supersedes_development_ids'] = [old['event']['development_id']]
        result = evaluate_lineup([c], coverage=[rec(old)], as_of=NOW)
        self.assertFalse(result['selected'])
        self.assertIn('EVENT_ALREADY_COVERED', codes(result['decisions'][0]))

    def test_grounded_new_development_updates_existing_private_draft(self):
        old = news()
        coverage = rec(old)
        coverage['article_id'] = 'draft-oil'
        c = news('decision-update', evidence='passage-oil-2', fact='decision-fact-2')
        c['event']['supersedes_development_ids'] = [old['event']['development_id']]
        result = evaluate_lineup([c], coverage=[coverage], as_of=NOW)
        self.assertEqual(result['selected'][0]['action'], 'update_draft')
        self.assertEqual(result['selected'][0]['target_id'], 'draft-oil')

    def test_legacy_same_event_without_fact_history_consolidates(self):
        old = rec(news())
        old.pop('fact_ids')
        c = news('decision-update', evidence='passage-oil-2', fact='decision-fact-2')
        c['event']['supersedes_development_ids'] = [old['development_id']]
        self.assertFalse(evaluate_lineup([c], coverage=[old], as_of=NOW)['selected'])

    def test_later_update_replaces_same_event_in_current_slate(self):
        old = news()
        new = news('decision-update', evidence='passage-oil-2', fact='decision-fact-2')
        new['event']['supersedes_development_ids'] = [old['event']['development_id']]
        result = evaluate_lineup([old, new], as_of=NOW, max_articles=1)
        self.assertEqual([s['candidate_id'] for s in result['selected']], ['decision-update'])
        self.assertEqual(result['decisions'][0]['status'], 'consolidate')

    def test_no_nonstock_quota_or_obligation_to_fill_ceiling(self):
        c = equity()
        result = evaluate_lineup([c], as_of=NOW)
        self.assertEqual(len(result['selected']), 1)
        self.assertFalse(result['selection_policy']['market_quotas'])

    def test_material_news_precedes_core_preference(self):
        result = evaluate_lineup([equity(), news()], as_of=NOW, max_articles=1)
        self.assertEqual(result['selected'][0]['candidate_id'], 'oil-decision')

    def test_every_material_ceiling_omission_is_auditable(self):
        result = evaluate_lineup([news()], as_of=NOW, max_articles=0)
        self.assertEqual(len(result['omitted_material_events']), 1)
        self.assertIn('DAILY_CEILING', codes(result['omitted_material_events'][0]))

    def test_duplicate_candidate_ids_hold_both(self):
        c = equity()
        result = evaluate_lineup([c, copy.deepcopy(c)], as_of=NOW)
        self.assertFalse(result['selected'])
        self.assertTrue(all('DUPLICATE_CANDIDATE_ID' in codes(d) for d in result['decisions']))

    def test_unknown_evidence_cannot_support_answer_or_priority(self):
        c = news()
        c['question']['answer_evidence_ids'] = ['invented']
        c['priority']['evidence_ids'] = ['invented']
        result = evaluate_lineup([c], as_of=NOW)
        self.assertIn('ANSWER_EVIDENCE_UNBOUND', codes(result['decisions'][0]))
        self.assertIn('PRIORITY_EVIDENCE_UNBOUND', codes(result['decisions'][0]))

    def test_private_policy_cannot_activate_or_publish(self):
        for policy in ({'private': False}, {'private': True, 'activated': True}):
            with self.assertRaises(ValueError):
                evaluate_lineup([equity()], as_of=NOW, selection_policy=policy)
        for maximum in (-1, 7, True):
            with self.assertRaises(ValueError):
                evaluate_lineup([equity()], as_of=NOW, max_articles=maximum)

    def test_inputs_are_not_mutated(self):
        c = news()
        before = copy.deepcopy(c)
        evaluate_lineup([c], coverage=[], as_of=NOW)
        self.assertEqual(c, before)

    def test_malformed_upstream_gate_holds_instead_of_raising(self):
        for c, field in [(equity(), 'cohort_gate'), (news(), 'news_gate')]:
            c[field] = ['invalid']
            result = evaluate_lineup([c], as_of=NOW)
            self.assertFalse(result['selected'])


if __name__ == '__main__':
    unittest.main()
