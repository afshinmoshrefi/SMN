"""Synthetic calendar fixtures test mechanics, never certify an actual exchange."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from private_history import derive_history_panel


class PrivateHistoryTests(unittest.TestCase):
    def args(self):
        sessions=[]; rows=[]
        for year in range(2006,2026):
            # An explicitly synthetic three-session annual window.
            for day,close,high,low in [(17,100,180,50),(18,102,105,97),(20,101,106,96)]:
                d=f'{year}-08-{day}'
                sessions.append(d);rows.append({'date':d,'open':close,'high':high,'low':low,'close':close})
        # A next session after inclusive end must not enter any outcome.
        rows.append({'date':'2025-08-21','open':150,'close':150,'high':160,'low':140});sessions.append('2025-08-21')
        rows.append({'date':'2026-09-04','open':150,'close':150,'high':150,'low':150});sessions.append('2026-09-04')
        return {'ohlc':rows,'session_manifest':{'calendar_id':'SYNTHETIC','start_date':'2006-01-01','end_date':'2026-09-06','sessions':sorted(sessions),'expected_latest_date':'2026-09-04','source_ref':'synthetic-calendar'},
            'instrument':{'resource_id':'2','symbol':'TEST','provider':'fixture','exchange':'fixture','series_id':'adjusted-fixture'},
            'anchor_date':'2026-08-17','days':4,'as_of':'2026-09-06T16:00:00Z','dataset_sha256':'a'*64,'source_ref':'synthetic-prices','requested_years':20}

    def test_inclusive_last_session_and_entry_close(self):
        r=derive_history_panel(**self.args())
        self.assertEqual(r['status'],'passed')
        self.assertEqual(r['observations'][-1],{'year':2025,'net':1,'mfe':6,'mae':-4})
        self.assertEqual(r['windows'][-1]['end_date'],'2025-08-20')
        self.assertEqual(r['windows'][-1]['exit_session'],'2025-08-20')
        self.assertEqual(r['decision_cutoff'],'2026-08-17')
        self.assertEqual(len(r['observations']),20)

    def test_holiday_or_weekend_end_stays_inside(self):
        a=self.args();a['days']=3
        r=derive_history_panel(**a)
        self.assertEqual(r['windows'][-1]['end_date'],'2025-08-19')
        self.assertEqual(r['windows'][-1]['exit_session'],'2025-08-18')
        self.assertEqual(r['observations'][-1],{'year':2025,'net':2,'mfe':5,'mae':-3})

    def test_one_session_close_has_no_intraday_excursions(self):
        a=self.args();a['days']=1;r=derive_history_panel(**a)
        self.assertEqual(r['status'],'passed')
        self.assertEqual(r['observations'][0],{'year':2006,'net':0,'mfe':0,'mae':0})

    def test_entry_zero_is_real_extrema_boundary(self):
        a=self.args()
        for row in a['ohlc']:
            if row['date'].endswith('-08-18') or row['date'].endswith('-08-20'):
                row.update(open=90,close=90,high=95,low=85)
        r=derive_history_panel(**a)
        self.assertEqual(r['observations'][0],{'year':2006,'net':-10,'mfe':0,'mae':-15})

    def test_missing_expected_session_holds(self):
        a=self.args();a['ohlc']=[r for r in a['ohlc'] if r['date']!='2017-08-18']
        r=derive_history_panel(**a)
        self.assertEqual(r['status'],'held')
        self.assertFalse(r['history']['window_checks_passed'])
        self.assertNotIn(2017,[r['year'] for r in r['observations']])

    def test_nonfinite_negative_zero_and_bad_ohlc_hold(self):
        for field,value in [('close',float('nan')),('high',0),('low',-1),('open',1000),('close',None)]:
            with self.subTest(field=field,value=value):
                a=self.args();a['ohlc'][1][field]=value
                self.assertEqual(derive_history_panel(**a)['status'],'held')

    def test_duplicate_dates_hold(self):
        a=self.args();a['ohlc'].append(copy.deepcopy(a['ohlc'][0]))
        self.assertEqual(derive_history_panel(**a)['status'],'held')

    def test_calendar_must_cover_all_requested_windows(self):
        a=self.args();a['session_manifest']['start_date']='2010-01-01'
        self.assertEqual(derive_history_panel(**a)['status'],'held')

    def test_short_history_is_visible_not_backfilled(self):
        a=self.args();a['ohlc']=[r for r in a['ohlc'] if r['date']>='2012-01-01']
        r=derive_history_panel(**a)
        self.assertEqual(r['status'],'passed')
        self.assertEqual(len(r['observations']),14)
        self.assertEqual(len(r['coverage']['missing_year_reasons']),6)
        self.assertEqual(r['coverage']['start_year'],2006)

    def test_stale_and_future_datasets_hold(self):
        a=self.args();a['ohlc'].pop()
        self.assertEqual(derive_history_panel(**a)['status'],'held')

    def test_stale_declared_boundary_cannot_hide_known_later_session(self):
        a=self.args();a['ohlc'].pop();a['session_manifest']['expected_latest_date']='2025-08-21'
        self.assertEqual(derive_history_panel(**a)['status'],'held')

    def test_extreme_return_holds_without_exception(self):
        a=self.args()
        for k in ('open','high','low','close'):a['ohlc'][0][k]=1e-308
        for k in ('open','high','low','close'):a['ohlc'][1][k]=1e308
        r=derive_history_panel(**a)
        self.assertEqual(r['status'],'held')
        self.assertIn('UNREPRESENTABLE_WINDOW_RETURN',[i['code'] for i in r['issues']])

    def test_audit_day_close_controls_boundary_even_when_caller_claims_yesterday(self):
        a=self.args();a['as_of']='2026-09-04T21:00:00Z'
        a['ohlc'][-1]['date']='2026-09-03'
        a['session_manifest']['sessions'].insert(-1,'2026-09-03')
        a['session_manifest']['expected_latest_date']='2026-09-03'
        a['session_manifest']['audit_session_close']='2026-09-04T20:00:00Z'
        self.assertEqual(derive_history_panel(**a)['status'],'held')
        a['as_of']='2026-09-04T15:00:00Z'
        self.assertEqual(derive_history_panel(**a)['status'],'passed')
        a['session_manifest'].pop('audit_session_close')
        self.assertEqual(derive_history_panel(**a)['status'],'held')
        a=self.args();a['ohlc'][-1]['date']='2026-09-07'
        self.assertEqual(derive_history_panel(**a)['status'],'held')

    def test_invalid_identity_clock_and_calendar_hold(self):
        for key,value in [('days',0),('days',True),('as_of','2026-09-06T12:00:00'),('anchor_date','2024-02-29'),('dataset_sha256','x'),('requested_years',100000)]:
            a=self.args();a[key]=value
            self.assertEqual(derive_history_panel(**a)['status'],'held')
        a=self.args();a['instrument'].pop('provider')
        self.assertEqual(derive_history_panel(**a)['status'],'held')

    def test_output_does_not_leak_raw_prices(self):
        import json
        r=derive_history_panel(**self.args());text=json.dumps(r)
        self.assertNotIn('"close":',text);self.assertNotIn('"high":',text)
        self.assertFalse(r['publishable'])


if __name__=='__main__':unittest.main()
