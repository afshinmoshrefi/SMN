"""Synthetic, offline paired volume protocol and receiver failure checks."""
from datetime import datetime,timedelta,timezone
import copy,json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from volume_feed_protocol import (FILES,build_bundle,validate_bundle,parse_feed,
    canonical,digest,install_bundle,resolve_feed,verified_peer)
from volume_feed_receiver import receive_dated_volume
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
        self.assertTrue(verified_peer('127.0.0.1','10.0.0.96','10.0.0.96'))
        self.assertFalse(verified_peer('127.0.0.1','10.0.0.99','10.0.0.96'))
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

if __name__=='__main__':unittest.main()
