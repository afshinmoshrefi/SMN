"""Failure-only retry and custody controls, with no model/mail/network calls."""
from datetime import datetime,timedelta,timezone
import json,os,socket,subprocess,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import reconcile_tick_guard as g

NOW=datetime(2026,10,9,10,tzinfo=timezone.utc);DAY='2026-10-09'
IDENTITY={'start_ticks':'synthetic','argv_sha256':'a'*64}

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name)
        self.launch=Mock(return_value=types.SimpleNamespace(returncode=0))
        self.birth=patch.object(g,'_identity',return_value=IDENTITY);self.birth.start();self.addCleanup(self.birth.stop)
        self.path=self.root/DAY/'reconcile-tick-guard.json'
    def tick(self,seconds=0):return g.run_tick(self.root,DAY,['harmless-mocked-worker'],NOW+timedelta(seconds=seconds),self.launch)
    def state(self):return json.loads(self.path.read_text())
    def write(self,value):self.path.parent.mkdir(exist_ok=True);self.path.write_text(json.dumps(value))
    def reservation(self):
        return {'policy':g.POLICY,'date':DAY,'failures':[],'inflight':{'host':socket.gethostname(),'pid':99999999,
            'identity':IDENTITY,'started_utc':(NOW-timedelta(minutes=2)).isoformat(),'attempt_id':'saved-private-attempt'}}
    def test_sixty_five_normal_ticks_do_not_consume_failure_budget(self):
        for minute in range(65):self.assertTrue(self.tick(minute*60)['child_succeeded'])
        self.assertEqual(self.launch.call_count,65);self.assertEqual(self.state()['failures'],[])
    def test_three_failures_block_fourth_despite_successful_ticks_between_them(self):
        self.launch.side_effect=[types.SimpleNamespace(returncode=2),types.SimpleNamespace(returncode=0),
                                types.SimpleNamespace(returncode=-9),types.SimpleNamespace(returncode=0),types.SimpleNamespace(returncode=2)]
        for minute in range(5):self.tick(minute*60)
        self.assertEqual(self.tick(5*60)['status'],'failure_budget_exhausted');self.assertEqual(self.launch.call_count,5)
        self.assertEqual(len(self.state()['failures']),3)
    def test_failure_backoff_is_enforced_before_retry(self):
        self.launch.return_value.returncode=2;self.tick()
        self.assertEqual(self.tick(59)['status'],'child_backoff');self.assertEqual(self.launch.call_count,1)
        self.tick(60);self.assertEqual(self.launch.call_count,2)
    def test_dead_reserved_worker_counts_once_then_backs_off(self):
        self.write(self.reservation())
        with patch.object(g,'_alive',return_value=False):
            self.assertEqual(self.tick()['status'],'child_backoff')
        self.launch.assert_not_called();self.assertEqual(len(self.state()['failures']),1)
        self.tick(60);self.assertEqual(len(self.state()['failures']),1);self.launch.assert_called_once()
    def test_live_reservation_never_starts_another_child_or_changes_evidence(self):
        self.write(self.reservation());before=self.path.read_bytes()
        with patch.object(g,'_alive',return_value=True):self.assertEqual(self.tick()['status'],'reconciliation_in_progress')
        self.assertEqual(self.path.read_bytes(),before);self.launch.assert_not_called()
    def test_reused_pid_is_uncertain_and_never_signalled(self):
        self.write(self.reservation());before=self.path.read_bytes()
        with patch.object(g,'_alive',return_value=True),patch.object(g,'_identity',return_value={'start_ticks':'different'}),patch.object(g.os,'kill') as kill:
            self.assertEqual(self.tick()['status'],'reconcile_owner_uncertain');kill.assert_not_called()
        self.assertEqual(self.path.read_bytes(),before);self.launch.assert_not_called()
    def test_foreign_reservation_is_preserved(self):
        value=self.reservation();value['inflight']['host']='other-host';self.write(value);before=self.path.read_bytes()
        self.assertEqual(self.tick()['status'],'reconcile_guard_needs_attention')
        self.assertEqual(self.path.read_bytes(),before);self.launch.assert_not_called()
    def test_timeout_records_a_failed_attempt_and_no_completion(self):
        self.launch.side_effect=subprocess.TimeoutExpired('mocked',85)
        self.assertEqual(self.tick()['status'],'child_failed');self.assertNotIn('inflight',self.state())
        self.assertEqual(self.state()['successful_ticks'],0);self.assertEqual(len(self.state()['failures']),1)
    def test_os_failure_records_attempt_and_is_bounded(self):
        self.launch.side_effect=OSError('mocked launch error')
        for minute in range(4):self.tick(minute*60)
        self.assertEqual(self.launch.call_count,3);self.assertEqual(self.state()['status'],'failure_budget_exhausted')
    def test_rolling_hour_boundary_expires_failures_without_fake_success(self):
        self.launch.return_value.returncode=2
        for minute in range(3):self.tick(minute*60)
        self.launch.return_value.returncode=0
        self.assertEqual(self.tick(3599)['status'],'failure_budget_exhausted')
        self.assertTrue(self.tick(3600)['child_succeeded']);self.assertEqual(len(self.state()['failures']),2)
    def test_malformed_state_is_preserved_and_child_not_called(self):
        self.path.parent.mkdir();self.path.write_text('{truncated');before=self.path.read_bytes()
        self.assertEqual(self.tick()['status'],'reconcile_guard_needs_attention');self.assertEqual(self.path.read_bytes(),before)
        self.launch.assert_not_called()
    def test_wrong_day_future_failure_and_naive_clock_are_refused(self):
        for field in ('date','future','naive'):
            value={'policy':g.POLICY,'date':DAY,'failures':[]}
            if field=='date':value['date']='2026-10-08'
            if field=='future':value['failures']=[{'observed_utc':(NOW+timedelta(seconds=1)).isoformat()}]
            if field=='naive':value['failures']=[{'observed_utc':'2026-10-09T09:00:00'}]
            self.write(value);before=self.path.read_bytes();self.assertEqual(self.tick()['status'],'reconcile_guard_needs_attention')
            self.assertEqual(self.path.read_bytes(),before)
        self.launch.assert_not_called()
    def test_missing_birth_identity_refuses_dispatch(self):
        with patch.object(g,'_identity',return_value=None):self.assertEqual(self.tick()['status'],'reconcile_guard_needs_attention')
        self.launch.assert_not_called();self.assertFalse(self.path.exists())
    @unittest.skipIf(os.name=='nt','Real Linux flock qualification')
    def test_real_competing_lock_is_preserved(self):
        self.path.parent.mkdir();lock=self.path.with_name('reconcile-tick-guard.lock')
        with lock.open('a') as out:
            g.fcntl.flock(out,g.fcntl.LOCK_EX|g.fcntl.LOCK_NB)
            self.assertEqual(self.tick()['status'],'reconcile_guard_busy');self.launch.assert_not_called()
    @unittest.skipIf(os.name=='nt','Linux symlink custody qualification')
    def test_symlink_state_is_refused_without_touching_target(self):
        self.path.parent.mkdir();target=self.root/'peer';target.write_text('peer');self.path.symlink_to(target)
        with self.assertRaises(ValueError):self.tick()
        self.assertEqual(target.read_text(),'peer');self.launch.assert_not_called()
    def test_preserved_release_day_creates_no_guard_or_child(self):
        phase=types.ModuleType('production_continuity_schedule');phase.require_production_host=Mock();phase.preserved_release_day=Mock(return_value=True)
        with patch.dict(sys.modules,{'production_continuity_schedule':phase}),patch('sys.argv',['guard','--enable-production-continuity','--root',str(self.root),'--date',DAY]),patch.object(g,'run_tick') as run,patch('builtins.print'):
            self.assertEqual(g.main(),0);run.assert_not_called()
        self.assertEqual(list(self.root.iterdir()),[])
    def test_exhaustion_builds_existing_opt_in_incident_without_sending(self):
        import smn_operational_alerts as alerts
        self.launch.return_value.returncode=2
        for minute in range(3):self.tick(minute*60)
        settings={'daily_generation':{'timezone':'UTC','start_time':'05:30'}}
        incidents=alerts._recovery_incidents(self.root,NOW,settings,'production')
        self.assertEqual(len(incidents),1);self.assertEqual(incidents[0]['kind'],'recovery-failure-budget')
        self.assertEqual(alerts._recovery_incidents(self.root,NOW,settings,'dev'),[])

if __name__=='__main__':unittest.main()
