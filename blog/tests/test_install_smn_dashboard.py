import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path,PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

import install_smn_dashboard as installer


class DashboardInstallerTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        paths={'ENV':'etc/dashboard.env','SECRETS':'etc/secrets.env','STATE':'state',
               'UNITS':'units','HTTPS':'nginx/sites-enabled/smn-ssl.conf','HTTP':'nginx/sites-enabled/smn.conf',
               'SNIPPET':'nginx/snippets/dashboard.conf','MARKER':'etc/dashboard-production.json',
               'RECORDS':'records','PYTHON':'venv/python','GUNICORN':'venv/gunicorn','QUEUE':'live/blog_queue.py'}
        for name,relative in paths.items():
            path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True)
            changed=patch.object(installer,name,path);changed.start();self.addCleanup(changed.stop)
        self.key=self.root/'public.pem';self.key.write_text('PUBLIC KEY FIXTURE')
        self.env=('SMN_DASHBOARD_AUTH=required\nSMN_DASHBOARD_ENV=prod\nSMN_DASHBOARD_PUBLIC=1\n'
                  'SMN_DASHBOARD_LOGIN_URL=https://tradewave.ai/smn-dashboard/login\nSMN_DASHBOARD_TW_PUBKEY='+str(self.key)+'\n')
        installer.ENV.write_text(self.env);installer.SECRETS.write_text('EXISTING_SECRET_CONFIGURATION')
        installer.PYTHON.write_text('runtime');installer.GUNICORN.write_text('runtime')
        installer.QUEUE.write_text('def _dashboard_headers(): pass\ndef _dashboard_article_for_pattern(): pass\nSMN_DASHBOARD_SERVICE_KEY_FILE="existing"')
        for path,listen in ((installer.HTTPS,'443 ssl'),(installer.HTTP,'80')):
            path.write_text('server { listen '+listen+'; server_name seasonalmarketnews.com; root /var/www/smn; location / { try_files $uri =404; } }')
        self.proof=self.root/'dev-proof.json';self.snapshots=self.root/'snapshots.json'
        self.proof.write_text(json.dumps({'source_commit':'a'*40,'status':'dev_qualified','live_verification_sha256':'proof'}))
        self.snapshots.write_text(json.dumps({'source_commit':'a'*40,'date':datetime.now(timezone.utc).date().isoformat(),
            'production_web_snapshot':'web','production_app_snapshot':'app','approved_by':'operator'}))
        self.args=SimpleNamespace(dev_proof=self.proof,snapshots=self.snapshots)

    def mocked_run(self,*args):
        if args[0]=='git':return 'a'*40 if 'rev-parse' in args else ''
        if args[0]=='curl' and '%{http_code}' in args:return '401'
        return ''

    def activate(self):
        with patch.object(installer,'host_guard'),patch.object(installer,'private_path'), \
             patch.object(installer,'run',side_effect=self.mocked_run), \
             patch.object(installer.subprocess,'run',return_value=SimpleNamespace(stdout='not-found')):
            return installer.activate(self.args)

    def test_auth_off_wrong_environment_login_and_unknown_setting_rejected(self):
        self.assertEqual(installer.auth_config(self.env),self.key)
        for text in (self.env.replace('AUTH=required','AUTH=off'),self.env.replace('ENV=prod','ENV=dev'),
                     self.env.replace('https://tradewave.ai','http://tradewave.ai'),
                     self.env+'SMN_DASHBOARD_COOKIE_SECURE=0\n',self.env+'UNKNOWN=1\n'):
            with self.subTest(text=text.splitlines()[0]),self.assertRaises(ValueError):installer.auth_config(text)

    def test_proxy_preserves_existing_site_and_units_use_same_release_state(self):
        original=installer.HTTPS.read_text();updated=installer.site_text(original,True)
        self.assertIn('location / { try_files $uri =404; }',updated)
        self.assertIn('include snippets/smn_dashboard_prod.conf;',updated)
        self.assertIn('https://seasonalmarketnews.com$request_uri',installer.site_text(installer.HTTP.read_text(),False))
        units=installer.unit_texts(PurePosixPath('/opt/smn-release'))
        self.assertIn('--bind 127.0.0.1:7172',units['pub_dashboard.service'])
        self.assertNotIn('192.168.1.180',str(units))
        for name in ('pub_dashboard.service','pub_dashboard_sweep.service'):
            self.assertIn('WorkingDirectory=/opt/smn-release/blog',units[name]);self.assertIn(str(installer.STATE),units[name])
        self.assertIn('OnCalendar=*-*-* *:*:00',units['pub_dashboard_sweep.timer'])

    def test_stale_proof_or_missing_queue_integration_refuses_before_writes(self):
        self.proof.write_text(json.dumps({'source_commit':'old','status':'dev_qualified'}))
        with self.assertRaisesRegex(ValueError,'Dev proof'):self.activate()
        self.assertFalse(installer.MARKER.exists())
        self.proof.write_text(json.dumps({'source_commit':'a'*40,'status':'dev_qualified','live_verification_sha256':'proof'}))
        installer.QUEUE.write_text('old queue')
        with self.assertRaisesRegex(ValueError,'blog_queue'):self.activate()
        self.assertFalse(installer.MARKER.exists())

    def test_activation_and_rollback_preserve_site_env_and_state(self):
        before={p:p.read_bytes() for p in (installer.HTTPS,installer.HTTP,installer.ENV,installer.SECRETS)}
        receipt=self.activate();installer.STATE.joinpath('pins.json').write_text('retained dashboard state')
        with patch.object(installer,'host_guard'),patch.object(installer,'run'),patch.object(installer.subprocess,'run',return_value=SimpleNamespace(stdout='not-found')):
            result=installer.rollback(receipt['record'])
        self.assertTrue(result['dashboard_state_preserved'])
        self.assertEqual({p:p.read_bytes() for p in before},before)
        self.assertEqual(installer.STATE.joinpath('pins.json').read_text(),'retained dashboard state')
        self.assertFalse(installer.MARKER.exists());self.assertFalse(installer.SNIPPET.exists())

    def test_drifted_config_or_backup_is_rejected_before_rollback_mutations(self):
        receipt=self.activate();record=Path(receipt['record'])
        installer.HTTPS.write_text('peer edit')
        with patch.object(installer,'host_guard'),patch.object(installer.subprocess,'run') as process:
            with self.assertRaisesRegex(ValueError,'drift'):installer.rollback(record)
        process.assert_not_called()
        self.assertTrue((installer.RECORDS/'smn-production-activation.lock').exists())

    def test_failed_auth_smoke_restores_original_site(self):
        before=installer.HTTPS.read_bytes()
        original_run=self.mocked_run
        self.mocked_run=lambda *args:'200' if args[0]=='curl' and '%{http_code}' in args else original_run(*args)
        with self.assertRaisesRegex(ValueError,'authentication smoke'):self.activate()
        self.assertEqual(installer.HTTPS.read_bytes(),before);self.assertFalse(installer.MARKER.exists())

    def test_nginx_failure_before_daemon_reload_restores_files_with_unloaded_units(self):
        before=installer.HTTPS.read_bytes();calls=[];nginx_tests=0;original_run=self.mocked_run
        def run(*args):
            nonlocal nginx_tests
            calls.append(args)
            if args[:2]==('nginx','-t'):
                nginx_tests+=1
                if nginx_tests==2:raise RuntimeError('new nginx syntax failed')
            return original_run(*args)
        self.mocked_run=run
        with self.assertRaisesRegex(RuntimeError,'nginx syntax'):self.activate()
        self.assertEqual(installer.HTTPS.read_bytes(),before)
        self.assertFalse(installer.MARKER.exists());self.assertFalse((installer.RECORDS/'smn-production-activation.lock').exists())
        self.assertFalse(any(call[:2]==('systemctl','stop') for call in calls))

    def test_shared_activation_lock_is_never_stolen(self):
        installer.RECORDS.mkdir();lock=installer.RECORDS/'smn-production-activation.lock';lock.mkdir()
        (lock/'owner.json').write_text(json.dumps({'task':'other publication'}));before=installer.HTTPS.read_bytes()
        with self.assertRaises(FileExistsError):self.activate()
        self.assertEqual(json.loads((lock/'owner.json').read_text())['task'],'other publication')
        self.assertEqual(installer.HTTPS.read_bytes(),before)

    def test_single_secret_initializer_precedes_multiworker_start_without_contents(self):
        calls=[];original_run=self.mocked_run
        def run(*args):calls.append(args);return original_run(*args)
        self.mocked_run=run;self.activate()
        init=[i for i,c in enumerate(calls) if c[0]=='systemd-run']
        start=next(i for i,c in enumerate(calls) if c[:3]==('systemctl','enable','--now') and c[3]=='pub_dashboard.service')
        self.assertEqual(len(init),1);self.assertLess(init[0],start)
        self.assertIn('dashboard_auth.session_secret(); dashboard_auth.service_key()',calls[init[0]][-1])
        self.assertNotIn('print(',calls[init[0]][-1])

    def test_head_drift_recheck_aborts_without_site_mutation_and_releases_lock(self):
        original_run=self.mocked_run;head_reads=0;before=installer.HTTPS.read_bytes()
        def run(*args):
            nonlocal head_reads
            if args[0]=='git' and 'rev-parse' in args:
                head_reads+=1
                return 'a'*40 if head_reads==1 else 'different'
            return original_run(*args)
        self.mocked_run=run
        with self.assertRaisesRegex(ValueError,'changed before'):self.activate()
        self.assertEqual(installer.HTTPS.read_bytes(),before)
        self.assertFalse((installer.RECORDS/'smn-production-activation.lock').exists())


if __name__=='__main__':unittest.main()
