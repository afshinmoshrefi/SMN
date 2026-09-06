import copy
from datetime import date, timedelta
import unittest
from unittest.mock import Mock

from stock_activity import analyze_stock_volume, scan_stock_volume, attach_activity_event
from private_selection import generate_private_article, prepare_candidate

NOW = '2026-09-06T20:00:00+00:00'


def volume_packet():
    dates = []
    day = date(2026, 9, 4)
    while len(dates) < 61:
        if day.weekday() < 5:
            dates.append(str(day))
        day -= timedelta(days=1)
    dates.sort()
    return {'instrument': {'symbol': 'TEST', 'resource_id': '2', 'exchange': 'US',
        'provider': 'fixture', 'series_id': 'synthetic-daily', 'asset_class': 'equity'},
        'interval': 'completed_daily',
        'audit': {'status': 'reviewed', 'symbol': 'TEST', 'reviewed_by': 'synthetic fixture',
            'reviewed_at': NOW, 'source_ref': 'synthetic data, not market evidence',
            'source_url': 'https://example.test/volume/TEST',
            'volume_basis': 'provider_split_adjusted_shares', 'price_basis': 'provider_split_dividend_adjusted_close'},
        'calendar': {'checked_as_of': '2026-09-06', 'calendar_id': 'SYNTHETIC',
            'source_ref': 'synthetic weekdays', 'expected_latest_date': '2026-09-04',
            'latest_session_close': '2026-09-04T20:00:00Z', 'completed_sessions': dates},
        'rows': [{'date': d, 'close': 100, 'volume': 1000000 if d != dates[-1] else 3000000} for d in dates]}


class StockActivityTests(unittest.TestCase):
    def test_current_session_never_enters_baseline(self):
        packet = volume_packet()
        packet['rows'][-1]['close'] = 105
        original = copy.deepcopy(packet)
        result = analyze_stock_volume(packet, as_of=NOW)
        self.assertTrue(result['research_candidate'])
        self.assertEqual(result['measurements']['volume_multiple'], 3)
        self.assertEqual(result['measurements']['volume_percentile'], 100)
        self.assertEqual(result['measurements']['baseline_median_shares'], 1000000)
        self.assertEqual(result['measurements']['adjusted_close_change_pct'], 5)
        self.assertEqual(packet, original)
        self.assertFalse(result['publishable'])

    def test_tied_volumes_are_not_anomalies(self):
        packet = volume_packet()
        packet['rows'][-1]['volume'] = 1000000
        result = analyze_stock_volume(packet, as_of=NOW)
        self.assertEqual(result['status'], 'ordinary_activity')
        self.assertEqual(result['measurements']['volume_percentile'], 0)

    def test_incomplete_stale_missing_duplicate_and_nonfinite_hold(self):
        mutations = [lambda p: p.update(interval='intraday'),
            lambda p: p['calendar'].update(latest_session_close='2026-09-07T20:00:00Z'),
            lambda p: p['calendar'].update(expected_latest_date='2026-09-03'),
            lambda p: p['rows'].pop(4), lambda p: p['rows'].append(p['rows'][0]),
            lambda p: p['rows'][0].update(volume=float('nan')),
            lambda p: p['rows'][0].update(volume=0), lambda p: p['rows'][0].update(close=True),
            lambda p: p['audit'].update(symbol='OTHER'), lambda p: p['audit'].update(volume_basis='unknown')]
        for mutate in mutations:
            packet = volume_packet()
            mutate(packet)
            result = analyze_stock_volume(packet, as_of=NOW)
            self.assertEqual(result['status'], 'held', result)
            self.assertFalse(result['research_candidate'])

    def test_duplicate_candidates_cannot_fill_the_slate(self):
        result = scan_stock_volume([volume_packet(), volume_packet()], as_of=NOW)
        self.assertEqual(result['research_candidates'], [])

    def test_volume_without_editorial_materiality_does_not_self_publish(self):
        c = {'kind': 'activity', 'candidate_id': 'volume-test', 'volume_input': volume_packet()}
        item = prepare_candidate(c, as_of=NOW)
        self.assertFalse(item['news_gate']['eligible'])
        self.assertTrue(item['activity_gate']['research_candidate'])

    def test_forged_activity_cannot_bypass_recalculation_or_trigger_paid_calls(self):
        packet = volume_packet()
        packet['rows'][-1]['volume'] = 1000000
        send = Mock()
        result = generate_private_article({'as_of': NOW, 'candidate': {
            'candidate_id': 'ordinary', 'kind': 'activity', 'volume_input': packet,
            'activity_gate': {'research_candidate': True}, 'news_gate': {'eligible': True}}},
            send_plan=send, send_write=send)
        self.assertEqual(result['status'], 'hold')
        send.assert_not_called()

    def test_measurement_claim_is_recomputed_and_holiday_grace_is_scoped(self):
        c = {'kind': 'activity', 'candidate_id': 'volume-test', 'volume_input': volume_packet(),
            'activity_editorial': {'reviewed': True, 'significance': 3, 'audience_relevance': 3,
                'significance_reason': 'Large turnover deserves investigation.', 'relevance_reason': 'A liquid stock changes materially.'}}
        one = attach_activity_event(c, as_of=NOW)
        two = attach_activity_event(one, as_of=NOW)
        self.assertEqual(one['news_packet'], two['news_packet'])
        item = prepare_candidate(c, as_of=NOW)
        self.assertTrue(item['news_gate']['eligible'], item['news_gate'])
        self.assertIn('3.00 times', item['news_packet']['research']['claims'][0]['text'])


if __name__ == '__main__':
    unittest.main()
