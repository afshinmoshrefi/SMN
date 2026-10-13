"""Synthetic, offline paired volume protocol and receiver failure checks."""
from datetime import datetime,timedelta,timezone
import ast,copy,json,os,socket,struct,subprocess,types
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from volume_feed_protocol import (FILES,build_bundle,validate_bundle,parse_feed,
    canonical,digest,install_bundle,resolve_feed,verified_peer)
from volume_feed_receiver import receive_dated_volume,existing_nginx_peer
from volume_evidence import read_verified_volume_csv,volume_lookup,bind_verified_volume

NOW=datetime(2026,10,13,7,tzinfo=timezone.utc)
CSV=b'rank,ticker,avg_volume_30d,today_volume,rvol\n1,HPQ,5000001,14000001,2.79\n'
AUTH={'ok':True,'marker_sha256':'a'*64,'generation_fingerprint':'b'*64,
    'completed_session':'2026-10-12','completed_at':'2026-10-13T05:10:00Z'}
OBS={'HPQ':{'observation_date':'2026-10-12','source_csv_sha256':'c'*64}}

def fixture(now=NOW,raw=CSV,observations=None):
    return build_bundle({name:raw for name in FILES},observations or OBS,AUTH,'d'*40,now=now)

def rebind(bundle):
    bundle['delivery_id']=digest(canonical({k:v for k,v in bundle.items() if k!='delivery_id'}))
    return bundle

class VolumeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.bundle=fixture()
    def test_date_only_preserves_values_without_observation_clock(self):
        rows=validate_bundle(self.bundle,now=NOW)[FILES[0]]
        self.assertEqual(rows[0]['rvol'],2.79)
        self.assertEqual(rows[0]['avg_volume_30d'],5000001)
        self.assertEqual(rows[0]['today_volume'],14000001)
        proof=rows[0]['volume_provenance']
        self.assertEqual(proof['date_precision'],'day');self.assertIsNone(proof['observed_at'])
        self.assertEqual(proof['scope'],'prior_session')
        self.assertEqual(proof['generated_at'],NOW.isoformat())
    def test_bundle_captures_provenance_without_sharing_mutable_input(self):
        obs=copy.deepcopy(OBS);authority=copy.deepcopy(AUTH)
        bundle=build_bundle({name:CSV for name in FILES},obs,authority,'d'*40,now=NOW)
        obs['HPQ']['observation_date']='2026-10-13';authority['ok']=False
        self.assertEqual(len(validate_bundle(bundle,now=NOW)[FILES[0]]),1)
    def test_completed_volume_never_becomes_current_rvol(self):
        rows=validate_bundle(self.bundle,now=NOW)[FILES[0]]
        item={'ticker':'HPQ','news_reason':'Reports earnings'}
        bind_verified_volume([item],volume_lookup(rows))
        self.assertIsNone(item['rvol']);self.assertEqual(item['news_reason'],'Reports earnings')
        self.assertEqual(item['volume_context']['observation_date'],'2026-10-12')
    def test_changed_csv_without_hash_fails(self):
        self.bundle['files'][FILES[0]]['csv']=CSV.decode().replace('2.79','9.99')
        with self.assertRaises(ValueError):validate_bundle(rebind(self.bundle),now=NOW)
    def test_changed_envelope_without_delivery_hash_fails(self):
        self.bundle['files'][FILES[0]]['provenance']['generated_at']='2026-10-13T06:00:00Z'
        with self.assertRaises(ValueError):validate_bundle(self.bundle,now=NOW)
    def test_session_after_verified_engine_completion_fails(self):
        self.bundle['files'][FILES[0]]['provenance']['observations']['HPQ']['observation_date']='2026-10-13'
        with self.assertRaises(ValueError):validate_bundle(rebind(self.bundle),now=NOW)
    def test_current_date_cannot_be_relabelled_completed(self):
        for item in self.bundle['files'].values():item['provenance']['completion_proof']={**AUTH,'completed_session':'2026-10-13'}
        with self.assertRaises(ValueError):validate_bundle(rebind(self.bundle),now=NOW)
    def test_missing_row_source_hash_fails(self):
        self.bundle['files'][FILES[0]]['provenance']['observations']['HPQ']['source_csv_sha256']='bad'
        with self.assertRaises(ValueError):validate_bundle(rebind(self.bundle),now=NOW)
    def test_false_completion_fails(self):
        for item in self.bundle['files'].values():item['provenance']['completion_proof']={**AUTH,'ok':False}
        with self.assertRaises(ValueError):validate_bundle(rebind(self.bundle),now=NOW)
    def test_future_export_clock_fails(self):
        with self.assertRaises(ValueError):validate_bundle(fixture(NOW+timedelta(seconds=1)),now=NOW)
    def test_naive_clock_fails(self):
        with self.assertRaises(ValueError):validate_bundle(self.bundle,now=NOW.replace(tzinfo=None))
    def test_expired_export_fails(self):
        with self.assertRaises(ValueError):validate_bundle(self.bundle,now=NOW+timedelta(hours=25))
    def test_date_only_age_bound_omits_old_row_without_relabelling(self):
        obs={'HPQ':{**OBS['HPQ'],'observation_date':'2026-10-08'}}
        bundle=fixture(observations=obs)
        self.assertEqual(validate_bundle(bundle,now=NOW)[FILES[0]],[])
        self.assertEqual(bundle['files'][FILES[0]]['provenance']['observations']['HPQ']['observation_date'],'2026-10-08')
    def test_mixed_sessions_keep_individual_dates(self):
        raw=CSV+b'2,JPM,6000001,18000003,3.00\n'
        obs={**OBS,'JPM':{'observation_date':'2026-10-08','source_csv_sha256':'e'*64}}
        bundle=fixture(raw=raw,observations=obs)
        self.assertEqual([r['ticker'] for r in validate_bundle(bundle,now=NOW)[FILES[0]]],['HPQ'])
    def test_generation_mismatch_between_feeds_fails(self):
        self.bundle['files'][FILES[1]]['provenance']['generated_at']='2026-10-13T06:59:00Z'
        with self.assertRaises(ValueError):validate_bundle(rebind(self.bundle),now=NOW)
    def test_missing_file_fails(self):
        del self.bundle['files'][FILES[0]]
        with self.assertRaises(ValueError):validate_bundle(rebind(self.bundle),now=NOW)
    def test_extra_file_cannot_escape_directory(self):
        self.bundle['files']['../outside.csv']=self.bundle['files'][FILES[0]]
        with self.assertRaises(ValueError):install_bundle(self.root,self.bundle,now=NOW)
        self.assertFalse((self.root.parent/'outside.csv').exists())
    def test_empty_provenance_does_not_match_nonempty_csv(self):
        self.bundle['files'][FILES[0]]['provenance']['observations']={}
        with self.assertRaises(KeyError):validate_bundle(rebind(self.bundle),now=NOW)
    def test_install_keeps_legacy_files_and_reader_uses_verified_generation(self):
        legacy=self.root/FILES[0];legacy.write_bytes(b'legacy stale')
        receipt=install_bundle(self.root,self.bundle,now=NOW)
        self.assertEqual(legacy.read_bytes(),b'legacy stale')
        self.assertEqual(receipt['row_counts'][FILES[0]],1)
        self.assertEqual(read_verified_volume_csv(legacy,now=NOW)[0]['rvol'],2.79)
    def test_repeat_is_same_identity_receipt_and_files(self):
        first=install_bundle(self.root,self.bundle,now=NOW)
        before={p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        second=install_bundle(self.root,self.bundle,now=NOW+timedelta(seconds=1))
        self.assertEqual(first,second)
        self.assertEqual(before,{p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
    def test_interruption_before_pointer_keeps_previous_generation(self):
        install_bundle(self.root,self.bundle,now=NOW)
        later=fixture(NOW+timedelta(minutes=1));old=(self.root/'current-volume-delivery.json').read_bytes()
        def fail(stage):
            if stage=='before_pointer':raise RuntimeError('synthetic interruption')
        with self.assertRaises(RuntimeError):install_bundle(self.root,later,now=NOW+timedelta(minutes=1),failure=fail)
        self.assertEqual((self.root/'current-volume-delivery.json').read_bytes(),old)
        self.assertEqual(read_verified_volume_csv(self.root/FILES[0],now=NOW)[0]['rvol'],2.79)
    def test_interruption_after_pointer_retry_verifies_committed_generation(self):
        def fail(stage):
            if stage=='after_pointer':raise RuntimeError('lost acknowledgement')
        with self.assertRaises(RuntimeError):install_bundle(self.root,self.bundle,now=NOW,failure=fail)
        self.assertEqual(install_bundle(self.root,self.bundle,now=NOW)['delivery_id'],self.bundle['delivery_id'])
    def test_lost_ack_retry_has_no_new_generation(self):
        def fail(stage):
            if stage=='before_ack':raise RuntimeError('lost response')
        with self.assertRaises(RuntimeError):install_bundle(self.root,self.bundle,now=NOW,failure=fail)
        install_bundle(self.root,self.bundle,now=NOW)
        self.assertEqual(len(list((self.root/'.volume-generations').iterdir())),1)
    def test_old_delivery_cannot_regress_pointer(self):
        later=fixture(NOW+timedelta(minutes=1));install_bundle(self.root,later,now=NOW+timedelta(minutes=1))
        before=(self.root/'current-volume-delivery.json').read_bytes()
        with self.assertRaises(ValueError):install_bundle(self.root,self.bundle,now=NOW+timedelta(minutes=1))
        self.assertEqual((self.root/'current-volume-delivery.json').read_bytes(),before)
    def test_changed_generation_bytes_fail_optional_reader_without_legacy_fallback(self):
        (self.root/FILES[0]).write_bytes(CSV)
        install_bundle(self.root,self.bundle,now=NOW)
        resolve_feed(self.root/FILES[0]).write_bytes(b'changed')
        warnings=[];self.assertEqual(read_verified_volume_csv(self.root/FILES[0],now=NOW,warn=warnings.append),[])
        self.assertTrue(warnings)
    def test_receipt_collision_is_rejected(self):
        install_bundle(self.root,self.bundle,now=NOW)
        path=resolve_feed(self.root/FILES[0]).parent/'receipt.json'
        saved=json.loads(path.read_text());saved['status']='changed';path.write_text(json.dumps(saved))
        with self.assertRaises(ValueError):install_bundle(self.root,self.bundle,now=NOW)
    def test_missing_peer_configuration_fails_closed(self):
        self.assertFalse(verified_peer('10.0.0.96',None,''))
    def test_external_peer_cannot_forge_proxy_header(self):
        self.assertFalse(verified_peer('10.0.0.99','10.0.0.96','10.0.0.96'))
    def test_existing_trusted_loopback_proxy_accepts_fixed_real_ip(self):
        self.assertFalse(verified_peer('127.0.0.1','10.0.0.96','10.0.0.96'))
        self.assertTrue(verified_peer('127.0.0.1','10.0.0.96','10.0.0.96',proxy_transport='unix',verified_unix_peer=True))
        self.assertFalse(verified_peer('127.0.0.1','10.0.0.99','10.0.0.96'))
    def test_empty_unix_peer_requires_explicit_verified_proxy_mode(self):
        self.assertFalse(verified_peer('','10.0.0.96','10.0.0.96'))
        self.assertFalse(verified_peer('','10.0.0.96','10.0.0.96',proxy_transport='unix'))
        self.assertTrue(verified_peer('','10.0.0.96','10.0.0.96',proxy_transport='unix',verified_unix_peer=True))
        self.assertFalse(verified_peer('','10.0.0.99','10.0.0.96',proxy_transport='unix'))
    def test_foreign_tcp_peer_cannot_use_unix_mode_to_forge_header(self):
        self.assertFalse(verified_peer('10.0.0.99','10.0.0.96','10.0.0.96',proxy_transport='unix'))
    def kernel_fixture(self,*,uid=33,parent=100,mode=0o644,executable='/usr/sbin/nginx'):
        from contextlib import ExitStack
        from unittest.mock import patch
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(socket,'SO_PEERCRED',17,create=True))
        stack.enter_context(patch.object(socket,'AF_UNIX',1,create=True))
        def read(path,*args,**kwargs):return '100' if path.name=='nginx.pid' else f'200 (nginx worker) S {parent} 0'
        stack.enter_context(patch.object(Path,'read_text',autospec=True,side_effect=read))
        stack.enter_context(patch.object(Path,'stat',return_value=types.SimpleNamespace(st_uid=0,st_mode=mode)))
        stack.enter_context(patch.object(Path,'is_symlink',return_value=False))
        stack.enter_context(patch.object(Path,'resolve',autospec=True,side_effect=lambda p:Path(executable) if p.name=='exe' else p))
        return types.SimpleNamespace(family=socket.AF_UNIX,getsockopt=lambda *args:struct.pack('3i',200,uid,33))
    def test_kernel_peer_requires_existing_nginx_worker_uid_and_parent(self):
        self.assertTrue(existing_nginx_peer(self.kernel_fixture(),'33'))
    def test_other_local_user_cannot_forge_unix_nginx_peer(self):
        self.assertFalse(existing_nginx_peer(self.kernel_fixture(uid=1000),'33'))
    def test_separate_nginx_process_cannot_claim_existing_master(self):
        self.assertFalse(existing_nginx_peer(self.kernel_fixture(parent=999),'33'))
    def test_other_executable_cannot_claim_nginx_peer(self):
        self.assertFalse(existing_nginx_peer(self.kernel_fixture(executable='/usr/bin/python3'),'33'))
    def test_writable_master_pid_file_fails_closed(self):
        self.assertFalse(existing_nginx_peer(self.kernel_fixture(mode=0o666),'33'))
    def test_unix_peer_without_socket_cannot_authorize_header(self):
        self.assertFalse(existing_nginx_peer(None,'33'))
    def test_receiver_forbids_untrusted_upload_before_directory_creation(self):
        directory=self.root/'not-created'
        result,status=receive_dated_volume(self.bundle,remote_addr='10.0.0.99',real_ip='10.0.0.96',producer_ip='10.0.0.96',directory=directory,expected_producer_commit='d'*40,now=NOW)
        self.assertEqual(status,403);self.assertFalse(directory.exists())
    def test_receiver_acknowledges_exact_identity(self):
        result,status=receive_dated_volume(self.bundle,remote_addr='10.0.0.96',real_ip=None,producer_ip='10.0.0.96',directory=self.root,expected_producer_commit='d'*40,now=NOW)
        self.assertEqual(status,200);self.assertEqual(result['delivery_id'],self.bundle['delivery_id'])
    def test_receiver_hash_failure_does_not_mutate_pointer(self):
        result,status=receive_dated_volume({**self.bundle,'delivery_id':'0'*64},remote_addr='10.0.0.96',real_ip=None,producer_ip='10.0.0.96',directory=self.root,expected_producer_commit='d'*40,now=NOW)
        self.assertEqual(status,400);self.assertFalse((self.root/'current-volume-delivery.json').exists())
    def test_receiver_missing_revision_pin_does_not_create_state(self):
        target=self.root/'absent'
        result,status=receive_dated_volume(self.bundle,remote_addr='10.0.0.96',real_ip=None,producer_ip='10.0.0.96',directory=target,expected_producer_commit='',now=NOW)
        self.assertEqual(status,503);self.assertFalse(target.exists())
    def test_wrong_producer_revision_is_rejected(self):
        result,status=receive_dated_volume(self.bundle,remote_addr='10.0.0.96',real_ip=None,producer_ip='10.0.0.96',directory=self.root,expected_producer_commit='e'*40,now=NOW)
        self.assertEqual(status,400);self.assertFalse((self.root/'current-volume-delivery.json').exists())
    def test_receiver_receipt_has_auditable_peer_code_and_first_timestamp(self):
        result,status=receive_dated_volume(self.bundle,remote_addr='10.0.0.96',real_ip=None,producer_ip='10.0.0.96',directory=self.root,expected_producer_commit='d'*40,now=NOW)
        again,status=receive_dated_volume(self.bundle,remote_addr='10.0.0.96',real_ip=None,producer_ip='10.0.0.96',directory=self.root,expected_producer_commit='d'*40,now=NOW+timedelta(minutes=1))
        self.assertEqual(result,again);self.assertEqual(result['received_at'],NOW.isoformat())
        self.assertEqual(result['attribution']['source_peer_ip'],'10.0.0.96')
        self.assertEqual(len(result['attribution']['receiver_module_sha256']),64)
    def test_first_receiver_code_attribution_survives_code_upgrade_retry(self):
        before={'source_peer_ip':'10.0.0.96','receiver_module_sha256':'a'*64}
        first=install_bundle(self.root,self.bundle,now=NOW,attribution=before)
        again=install_bundle(self.root,self.bundle,now=NOW+timedelta(seconds=1),attribution={**before,'receiver_module_sha256':'b'*64})
        self.assertEqual(first,again);self.assertEqual(again['attribution'],before)
    def test_pointer_switch_already_has_durable_receipt_after_interruption(self):
        def fail(stage):
            if stage=='after_pointer':raise RuntimeError('synthetic process loss')
        with self.assertRaises(RuntimeError):install_bundle(self.root,self.bundle,now=NOW,failure=fail)
        self.assertTrue((resolve_feed(self.root/FILES[0]).parent/'receipt.json').is_file())
        self.assertEqual(len(read_verified_volume_csv(self.root/FILES[0],now=NOW)),1)
        install_bundle(self.root,self.bundle,now=NOW)
        self.assertEqual(len(read_verified_volume_csv(self.root/FILES[0],now=NOW)),1)
    def test_newer_bundle_replaces_generation_after_pointer_crash(self):
        def fail(stage):
            if stage=='after_pointer':raise RuntimeError('lost process')
        with self.assertRaises(RuntimeError):install_bundle(self.root,self.bundle,now=NOW,failure=fail)
        later=fixture(NOW+timedelta(minutes=1))
        receipt=install_bundle(self.root,later,now=NOW+timedelta(minutes=1))
        self.assertEqual(receipt['delivery_id'],later['delivery_id'])
        self.assertEqual(resolve_feed(self.root/FILES[0]).parent.name,later['delivery_id'])
    def test_newer_bundle_recovers_after_interrupted_bundle_expires(self):
        def fail(stage):
            if stage=='after_pointer':raise RuntimeError('lost process')
        with self.assertRaises(RuntimeError):install_bundle(self.root,self.bundle,now=NOW,failure=fail)
        clock=NOW+timedelta(hours=25)
        with self.assertRaises(ValueError):install_bundle(self.root,self.bundle,now=clock)
        later=fixture(clock);install_bundle(self.root,later,now=clock)
        self.assertEqual(resolve_feed(self.root/FILES[0]).parent.name,later['delivery_id'])
        self.assertEqual(len(read_verified_volume_csv(self.root/FILES[0],now=clock)),1)
    def test_receipt_crash_boundaries_never_publish_incomplete_generation(self):
        install_bundle(self.root,self.bundle,now=NOW)
        previous=(self.root/'current-volume-delivery.json').read_bytes()
        for stage in ('before_receipt','after_receipt','before_pointer'):
            with self.subTest(stage=stage):
                later=fixture(NOW+timedelta(minutes=1))
                def fail(actual):
                    if actual==stage:raise RuntimeError('lost process')
                with self.assertRaises(RuntimeError):install_bundle(self.root,later,now=NOW+timedelta(minutes=1),failure=fail)
                self.assertEqual((self.root/'current-volume-delivery.json').read_bytes(),previous)
                self.assertEqual(len(read_verified_volume_csv(self.root/FILES[0],now=NOW)),1)
        clock=NOW+timedelta(hours=25)
        later=fixture(clock);install_bundle(self.root,later,now=clock)
        self.assertEqual(resolve_feed(self.root/FILES[0]).parent.name,later['delivery_id'])
    def test_missing_receipt_from_external_corruption_still_omits(self):
        install_bundle(self.root,self.bundle,now=NOW)
        receipt=resolve_feed(self.root/FILES[0]).parent/'receipt.json';receipt.unlink()
        self.assertEqual(read_verified_volume_csv(self.root/FILES[0],now=NOW,warn=lambda _:None),[])
    def test_unknown_revision_pin_type_returns_configuration_failure(self):
        result,status=receive_dated_volume(self.bundle,remote_addr='10.0.0.96',real_ip=None,producer_ip='10.0.0.96',directory=self.root,expected_producer_commit=None,now=NOW)
        self.assertEqual(status,503)
    def test_broken_pointer_symlink_cannot_select_legacy_fallback(self):
        try:(self.root/'current-volume-delivery.json').symlink_to(self.root/'missing.json')
        except OSError:self.skipTest('Symlink creation unavailable on this host')
        with self.assertRaises(ValueError):resolve_feed(self.root/FILES[0])
    def test_unsafe_generation_directory_symlink_rejected(self):
        outside=self.root/'outside';outside.mkdir()
        try:(self.root/'.volume-generations').symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('Symlink creation unavailable on this host')
        with self.assertRaises(ValueError):install_bundle(self.root,self.bundle,now=NOW)
        self.assertEqual(list(outside.iterdir()),[])
    def test_receipt_hash_binding_is_checked_by_consumer(self):
        install_bundle(self.root,self.bundle,now=NOW)
        path=resolve_feed(self.root/FILES[0]).parent/'receipt.json'
        receipt=json.loads(path.read_text());receipt['csv_sha256'][FILES[0]]='0'*64;path.write_text(json.dumps(receipt))
        self.assertEqual(read_verified_volume_csv(self.root/FILES[0],now=NOW,warn=lambda _:None),[])
    def route_client(self):
        from flask import Flask,jsonify,request
        from unittest.mock import patch
        source=(Path(__file__).resolve().parents[1]/'blog_queue.py').read_text(encoding='utf-8')
        handler=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='update_volume_lists')
        app=Flask('volume-http-fixture');app.testing=True
        def receive(data,**kwargs):
            self.assertEqual(kwargs['directory'],'/home/flask/blog/volume_lists')
            return receive_dated_volume(data,**{**kwargs,'directory':self.root,'now':NOW})
        namespace={'app':app,'jsonify':jsonify,'request':request,'config':types.SimpleNamespace(appserver_ip='10.0.0.96'),
                   'os':os,'receive_dated_volume':receive}
        exec(compile(ast.Module(body=[handler],type_ignores=[]),'<exact-volume-handler>','exec'),namespace)
        self.addCleanup(patch.stopall);patch.dict(os.environ,{'SMN_VOLUME_PRODUCER_COMMIT':'d'*40}).start()
        return app.test_client()
    def test_actual_http_handler_returns_bound_receipt_for_trusted_peer(self):
        response=self.route_client().post('/update_volume_lists',json=self.bundle,environ_overrides={'REMOTE_ADDR':'10.0.0.96'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['delivery_id'],self.bundle['delivery_id'])
    def test_actual_http_handler_rejects_forged_foreign_forwarded_peer(self):
        response=self.route_client().post('/update_volume_lists',json=self.bundle,headers={'X-Real-IP':'10.0.0.96'},environ_overrides={'REMOTE_ADDR':'10.0.0.99'})
        self.assertEqual(response.status_code,403);self.assertFalse((self.root/'current-volume-delivery.json').exists())
    def test_existing_legacy_handler_is_semantically_unchanged(self):
        repo=Path(__file__).resolve().parents[2]
        baseline=os.environ.get('SMN_VOLUME_LEGACY_BASELINE_FILE')
        raw=Path(baseline).read_bytes() if baseline else subprocess.check_output(['git','-C',str(repo),'show','94023f7d75cc5a96d4d2f81b1073eaa9d3fcf0df:blog/blog_queue.py'])
        self.assertEqual(digest(raw),'9245384c43ef256a4d51934bac7acb0720b64c05bbe42ac4c911d248aeda37ff')
        old=raw.decode('utf-8')
        new=(repo/'blog/blog_queue.py').read_text(encoding='utf-8')
        def function(text):return next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='update_volume_lists')
        baseline=function(old);candidate=function(new)
        candidate.body=[n for n in candidate.body if not (isinstance(n,ast.If) and any(isinstance(x,ast.Name) and x.id=='receive_dated_volume' for x in ast.walk(n)))]
        self.assertEqual(ast.dump(baseline),ast.dump(candidate))
    @unittest.skipUnless(os.name=='posix','Requires native Linux process locks')
    def test_native_process_death_after_pointer_recovers_lock_and_receipt(self):
        payload=self.root/'payload.json';payload.write_bytes(canonical(self.bundle))
        code="""import json,os,sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0,sys.argv[1])
from volume_feed_protocol import install_bundle
bundle=json.loads(Path(sys.argv[3]).read_text())
def die(stage):
 if stage=='after_pointer':os._exit(73)
install_bundle(Path(sys.argv[2]),bundle,now=datetime.fromisoformat(sys.argv[4]),failure=die)
"""
        result=subprocess.run([sys.executable,'-B','-c',code,str(Path(__file__).resolve().parents[1]),str(self.root),str(payload),NOW.isoformat()],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,73)
        self.assertEqual(len(read_verified_volume_csv(self.root/FILES[0],now=NOW)),1)
        clock=NOW+timedelta(hours=25);later=fixture(clock)
        install_bundle(self.root,later,now=clock)
        self.assertEqual(resolve_feed(self.root/FILES[0]).parent.name,later['delivery_id'])
        self.assertEqual(len(read_verified_volume_csv(self.root/FILES[0],now=clock)),1)
    @unittest.skipUnless(os.name=='posix','Requires native Linux process locks')
    def test_native_concurrent_duplicate_uploads_share_one_generation(self):
        payload=self.root/'payload.json';payload.write_bytes(canonical(self.bundle))
        code="""import json,sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0,sys.argv[1])
from volume_feed_protocol import install_bundle
install_bundle(Path(sys.argv[2]),json.loads(Path(sys.argv[3]).read_text()),now=datetime.fromisoformat(sys.argv[4]))
"""
        argv=[sys.executable,'-B','-c',code,str(Path(__file__).resolve().parents[1]),str(self.root),str(payload),NOW.isoformat()]
        processes=[subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
        for process in processes:
            stdout,stderr=process.communicate(timeout=15);self.assertEqual(process.returncode,0,stderr.decode())
        self.assertEqual(len(list((self.root/'.volume-generations').iterdir())),1)
        self.assertEqual(len(read_verified_volume_csv(self.root/FILES[0],now=NOW)),1)

if __name__=='__main__':unittest.main()
