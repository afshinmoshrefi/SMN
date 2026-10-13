"""Offline freshness/custody regressions. No providers or network calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from volume_evidence import (read_verified_volume_csv, bind_verified_volume,
                             bind_news_evidence, volume_lookup, merge_volume_candidates)
from test_select_news_context import sna

NOW = datetime(2026, 10, 13, 6, 30, tzinfo=timezone.utc)
CSV = b'rank,ticker,avg_volume_30d,today_volume,rvol\n1,HPQ,5000001,14000001,2.79\n'


class VolumeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / 'volume.csv'
        self.csv.write_bytes(CSV)
        self.proof = {'schema': 'tradewave-volume-v1', 'source': 'tradewave',
                      'csv_sha256': hashlib.sha256(CSV).hexdigest(),
                      'observation_date': '2026-10-13', 'observed_at': '2026-10-13T06:00:00Z'}
        self.warnings = []

    def save_proof(self):
        Path(str(self.csv) + '.provenance.json').write_text(json.dumps(self.proof), encoding='utf-8')

    def read(self):
        return read_verified_volume_csv(self.csv, now=NOW, warn=self.warnings.append)

    def test_undated_may_feed_omitted_even_with_fresh_mtime_and_bytes_preserved(self):
        self.assertEqual(self.read(), [])
        self.assertEqual(self.csv.read_bytes(), CSV)
        self.assertEqual(len(self.warnings), 1)

    def test_verified_values_preserved_without_recalculation(self):
        self.save_proof()
        row = self.read()[0]
        self.assertEqual({k: row[k] for k in ('ticker', 'avg_volume_30d', 'today_volume', 'rvol')},
                         {'ticker': 'HPQ', 'avg_volume_30d': 5000001, 'today_volume': 14000001, 'rvol': 2.79})
        self.assertEqual(row['volume_provenance']['scope'], 'current_session')
        self.assertEqual(self.warnings, [])

    def test_previous_session_and_future_dates_omitted(self):
        for day in ['2026-05-26', '2026-10-12', '2026-10-14']:
            with self.subTest(day=day):
                self.proof['observation_date'] = day
                self.save_proof()
                self.assertEqual(self.read(), [])

    def test_missing_malformed_or_wrong_source_proof_omitted(self):
        for change in [{'source': 'model'}, {'schema': 'other'}, {'csv_sha256': '0' * 64}, {'observed_at': None}]:
            with self.subTest(change=change):
                original = dict(self.proof)
                self.proof.update(change)
                self.save_proof()
                self.assertEqual(self.read(), [])
                self.proof = original
        Path(str(self.csv) + '.provenance.json').write_text('{', encoding='utf-8')
        self.assertEqual(self.read(), [])

    def test_future_naive_or_previous_session_observation_time_omitted(self):
        for observed in ['2026-10-13T07:00:00Z', '2026-10-13T06:00:00', '2026-10-13T03:00:00Z']:
            with self.subTest(observed=observed):
                self.proof['observed_at'] = observed
                self.save_proof()
                self.assertEqual(self.read(), [])

    def test_csv_changed_after_proof_omitted(self):
        self.save_proof()
        self.csv.write_bytes(CSV.replace(b'2.79', b'9.99'))
        self.assertEqual(self.read(), [])

    def test_missing_feed_does_not_raise_or_create_files(self):
        self.csv.unlink()
        self.assertEqual(self.read(), [])
        self.assertFalse(self.csv.exists())

    def test_one_unavailable_feed_does_not_hold_verified_feed(self):
        self.save_proof()
        with patch.object(sna, 'VOLUME_LIST_PATH', str(self.csv)), patch.object(
                sna, 'VOLUME_SPIKES_PATH', str(self.csv.parent / 'missing.csv')):
            volume, spikes = sna.load_volume_data(now=NOW)
        self.assertEqual(len(volume), 1)
        self.assertEqual(spikes, [])

    def test_all_unavailable_volume_continues_with_news_and_earnings(self):
        items = [{'ticker': 'HPQ', 'rvol': 9.99, 'news_reason': 'Unusual volume spike: 9.99x normal'},
                 {'ticker': 'JPM', 'rvol': 8.0, 'news_reason': 'Reported quarterly earnings',
                  'earnings_date': '2026-10-13', 'news_headline': 'JPM reports earnings'}]
        self.assertIs(bind_verified_volume(items, {}), items)
        self.assertTrue(all(x['rvol'] is None for x in items))
        # Binding optional volume never rewrites independent news. Untrusted
        # extractor reasons are handled by source binding, not lexical deletion.
        self.assertEqual(items[0]['news_reason'], 'Unusual volume spike: 9.99x normal')
        self.assertEqual(items[1]['news_reason'], 'Reported quarterly earnings')
        self.assertEqual(items[1]['earnings_date'], '2026-10-13')
        self.assertEqual(items[1]['news_headline'], 'JPM reports earnings')

    def test_model_value_replaced_with_exact_verified_engine_value(self):
        item = {'ticker': 'hpq', 'rvol': 9.99, 'news_reason': 'Volume spike: 9.99x normal'}
        self.save_proof()
        bind_verified_volume([item], volume_lookup(self.read()))
        self.assertEqual(item['rvol'], 2.79)
        self.assertEqual(item['news_reason'], 'Volume spike: 9.99x normal')
        self.assertEqual(item['volume_context']['rvol'], 2.79)

    def test_hash_case_does_not_change_byte_verification(self):
        self.proof['csv_sha256'] = self.proof['csv_sha256'].upper()
        self.save_proof()
        self.assertEqual(len(self.read()), 1)

    def test_us_date_boundary_and_weekend_do_not_relabel_prior_session(self):
        # 02:00 UTC Oct 13 is still Oct 12 in New York.
        self.proof.update(observation_date='2026-10-12', observed_at='2026-10-13T01:00:00Z')
        self.save_proof()
        self.assertEqual(len(read_verified_volume_csv(
            self.csv, now=datetime(2026, 10, 13, 2, tzinfo=timezone.utc))), 1)
        # Friday's observation cannot become Monday's "today" signal.
        self.proof.update(observation_date='2026-10-09', observed_at='2026-10-09T20:00:00Z')
        self.save_proof()
        self.assertEqual(read_verified_volume_csv(
            self.csv, now=datetime(2026, 10, 12, 12, tzinfo=timezone.utc), warn=self.warnings.append), [])

    def test_stale_feed_not_in_extraction_prompt_and_news_still_extracted(self):
        # Model response is a fixture, never a real dispatch.
        with patch.object(sna, 'VOLUME_LIST_PATH', str(self.csv)), patch.object(
                sna, 'VOLUME_SPIKES_PATH', str(self.csv)):
            volume, spikes = sna.load_volume_data(now=NOW)
        response = json.dumps([{'ticker': 'JPM', 'rvol': 9.99,
                                'news_reason': 'Reported earnings', 'news_headline': 'JPM reports earnings'}])
        with patch.object(sna, 'send_openai_prompt', return_value=response) as model:
            items = sna.extract_tickers_with_grok(
                [{'title': 'JPM reports earnings', 'content': 'Primary announcement'}], [], spikes, volume)
        prompt = model.call_args.args[0]
        self.assertNotIn('UNUSUAL VOLUME SPIKES TODAY (MUST INCLUDE ALL) ===', prompt)
        self.assertNotIn('HIGH ABSOLUTE VOLUME TODAY (include if rvol notable) ===', prompt)
        self.assertNotIn('HPQ', prompt)
        self.assertIn('JPM reports earnings', prompt)
        bind_verified_volume(items, {})
        self.assertEqual(items[0]['rvol'], None)
        self.assertEqual(sna.pre_score(items[0], []), 3.0)

    def test_oversized_optional_csv_field_is_omitted_and_other_feed_continues(self):
        import csv
        raw = CSV + b'2,' + b'X' * (csv.field_size_limit() + 1) + b',1,1,2\n'
        self.csv.write_bytes(raw)
        self.proof['csv_sha256'] = hashlib.sha256(raw).hexdigest()
        self.save_proof()
        self.assertEqual(self.read(), [])
        self.assertTrue(self.warnings)
        good = self.csv.parent / 'good.csv'
        good.write_bytes(CSV)
        proof = {**self.proof, 'csv_sha256': hashlib.sha256(CSV).hexdigest()}
        Path(str(good) + '.provenance.json').write_text(json.dumps(proof), encoding='utf-8')
        with patch.object(sna, 'VOLUME_LIST_PATH', str(self.csv)), patch.object(sna, 'VOLUME_SPIKES_PATH', str(good)):
            volume, spikes = sna.load_volume_data(now=NOW)
        self.assertEqual(volume, [])
        self.assertEqual(len(spikes), 1)

    def test_mixed_earnings_guidance_reason_kept_without_volume_censorship(self):
        headline = 'HPQ beats earnings and raises guidance despite sales-volume decline'
        items = [{'ticker': 'HPQ', 'news_headline': headline, 'rvol': 9.0,
                  'news_reason': 'Beat earnings and raised guidance; volume spike',
                  'earnings_type': 'recent', 'earnings_date': '2026-10-13'}]
        bind_news_evidence(items, [{'title': headline, 'url': 'https://issuer.example/release'}], [])
        bind_verified_volume(items, {})
        self.assertEqual(items[0]['news_reason'], headline)
        self.assertEqual(items[0]['earnings_type'], 'recent')
        self.assertIn('volume spike', items[0]['unverified_extraction']['news_reason'])
        self.assertIsNone(items[0]['rvol'])

    def test_business_sales_volume_news_is_preserved(self):
        headline = 'Sales volume spike lifts revenue outlook'
        item = {'ticker': 'HPQ', 'news_headline': headline, 'news_reason': headline}
        bind_news_evidence([item], [{'title': headline}], [])
        bind_verified_volume([item], {})
        self.assertEqual(item['news_reason'], headline)

    def test_turnover_paraphrase_without_bound_news_source_never_enters_ranking_news(self):
        reason = 'Shares traded at 9x average turnover after earnings'
        item = {'ticker': 'HPQ', 'rvol': 9.0, 'news_reason': reason,
                'news_headline': 'HPQ reports earnings and raises guidance'}
        bind_news_evidence([item], [{'title': item['news_headline']}], [])
        bind_verified_volume([item], {})
        self.assertEqual(item['news_reason'], 'HPQ reports earnings and raises guidance')
        self.assertNotIn('9x', item['news_reason'])
        self.assertEqual(item['unverified_extraction']['news_reason'], reason)
        self.assertIsNone(item['rvol'])
        unmatched = {'ticker': 'HPQ', 'news_reason': reason, 'news_headline': 'Invented headline'}
        bind_news_evidence([unmatched], [], [])
        self.assertEqual(unmatched['news_reason'], '')

    def test_valid_prior_completed_session_is_dated_context_not_current_rvol(self):
        self.proof.update(observation_date='2026-10-12', observed_at='2026-10-12T20:00:00Z',
                          observation_kind='completed_session')
        self.save_proof()
        rows = self.read()
        self.assertEqual(rows[0]['volume_provenance']['scope'], 'prior_session')
        item = {'ticker': 'HPQ', 'news_reason': 'Raises annual guidance'}
        bind_verified_volume([item], volume_lookup(rows))
        self.assertIsNone(item['rvol'])
        self.assertEqual(item['volume_context']['observation_date'], '2026-10-12')
        self.assertEqual(item['volume_context']['rvol'], 2.79)
        self.assertEqual(item['news_reason'], 'Raises annual guidance')

    def test_weekend_prior_session_retained_with_date_and_expired_session_omitted(self):
        self.proof.update(observation_date='2026-10-09', observed_at='2026-10-09T20:00:00Z',
                          observation_kind='completed_session')
        self.save_proof()
        rows = read_verified_volume_csv(self.csv, now=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
        self.assertEqual(rows[0]['volume_provenance']['observation_date'], '2026-10-09')
        self.assertEqual(read_verified_volume_csv(
            self.csv, now=datetime(2026, 10, 14, 12, tzinfo=timezone.utc), warn=self.warnings.append), [])

    def test_verified_volume_is_never_exposed_to_news_extraction_model(self):
        self.save_proof()
        rows = self.read()
        headline = 'JPM reports earnings'
        response = json.dumps([{'ticker': 'JPM', 'news_reason': headline, 'news_headline': headline}])
        with patch.object(sna, 'send_openai_prompt', return_value=response) as model:
            items = sna.extract_tickers_with_grok([{'title': headline}], [], rows, [])
        self.assertNotIn('HPQ', model.call_args.args[0])
        self.assertNotIn('2.79', model.call_args.args[0])
        volume_only = next(item for item in items if item['ticker'] == 'HPQ')
        self.assertEqual(volume_only['news_reason'], '')
        self.assertEqual(volume_only['volume_context']['rvol'], 2.79)

    def test_undated_direct_volume_rows_cannot_be_merged(self):
        self.assertEqual(volume_lookup([{'ticker': 'HPQ', 'rvol': 9.99}]), {})
        self.assertEqual(merge_volume_candidates([], {}), [])

    def test_untrusted_reason_excluded_from_actual_ranking_prompt(self):
        headline = 'HPQ reports earnings and raises guidance'
        item = {'ticker': 'HPQ', 'company': 'HPQ', 'asset_type': 'stock', 'in_news': True,
                'news_headline': headline, 'news_reason': 'Shares traded at 9x average turnover after earnings',
                'rvol': 9.0, 'patterns': []}
        bind_news_evidence([item], [{'title': headline}], [])
        bind_verified_volume([item], {})
        with patch.object(sna, 'RANKING_LLM', 'gpt5'), patch.object(
                sna, 'send_openai_prompt', return_value='[]') as model:
            sna.rank_ideas_with_llm([item])
        prompt = model.call_args.args[0]
        self.assertIn(headline, prompt)
        self.assertNotIn('9x average turnover', prompt)
        self.assertNotIn('unverified_extraction', prompt)
        self.assertIn('"rvol":null', prompt)

    def test_mechanical_volume_candidates_keep_original_25_plus_15_bound(self):
        self.save_proof()
        row = self.read()[0]
        spikes = [{**row, 'ticker': f'S{i}'} for i in range(30)]
        leaders = [{**row, 'ticker': f'L{i}'} for i in range(20)]
        headline = 'JPM reports earnings'
        response = json.dumps([{'ticker': 'JPM', 'news_headline': headline, 'news_reason': headline}])
        with patch.object(sna, 'send_openai_prompt', return_value=response):
            items = sna.extract_tickers_with_grok([{'title': headline}], [], spikes, leaders)
        self.assertEqual(len(items), 41)
        self.assertFalse({'S25', 'L15'} & {item['ticker'] for item in items})


if __name__ == '__main__':
    unittest.main()
