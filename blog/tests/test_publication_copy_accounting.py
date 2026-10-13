"""Identity-proved artifact copies never refund real model attempts."""
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
import json,shutil,tempfile,unittest
from daily_budget import snapshot,BudgetEvidenceError
from publication_job_copies import evidence,proof_path,prevent_dispatch

DATE='2026-10-13'
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf8')

class CopyAccounting(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.reader=self.root/DATE/'chatgpt'
        self.job=self.reader/'jobs/ABC-20261013-research';self.job.mkdir(parents=True)
        self.manifest={'job_id':self.job.name,'input_hashes':{},'evidence_sha256':'evidence'}
        save(self.job/'job.json',self.manifest);save(self.job/'state.json',{'status':'ready'})
        for n in range(1,4):save(self.job/('failed-attempt-'+str(n))/'state.json',{'status':'failed','utc':str(n)})
        self.tx=self.reader/'publication-revisions'/('a'*64)/'attempt-1'
        self.copy=self.tx/'jobs'/self.job.name
        shutil.copytree(self.job,self.copy)
        save(self.tx/'coverage-receipt.json',{'edition_date':DATE,'status':'live_verified',
             'production_written':True,'transaction_root':str(self.tx),'published_symbols':['ABC']})
        self.registry=self.root/'registry'

    def inventory(self):return snapshot(self.root,DATE,self.registry)
    def attest(self):
        proof=evidence(self.copy,DATE)
        proof.update(actor='independent test auditor',observed_utc=datetime.now(timezone.utc).isoformat())
        save(proof_path(self.registry,self.copy),proof)
        return proof

    def test_no_proof_keeps_both_uncertain_paths_charged(self):
        self.assertEqual(self.inventory()['consumed_conservative'],8)

    def test_exact_attestation_preserves_four_real_attempts_and_every_byte(self):
        before={str(p):p.read_bytes() for p in self.reader.rglob('*') if p.is_file()}
        self.attest();result=self.inventory()
        self.assertEqual(result['consumed_conservative'],4)
        self.assertEqual(result['prepared_or_uncertain_paths'],1)
        self.assertEqual(result['archived_unique_attempts'],3)
        self.assertEqual(result['retirement_exclusions'],[])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.reader.rglob('*') if p.is_file()})
        self.assertEqual(len(result['verified_publication_copies']),1)

    def test_mutated_archive_fails_closed(self):
        self.attest();save(self.copy/'failed-attempt-1/state.json',{'status':'different attempt'})
        with self.assertRaises(BudgetEvidenceError):self.inventory()

    def test_mutated_canonical_contract_fails_closed(self):
        self.attest();save(self.job/'job.json',{**self.manifest,'evidence_sha256':'changed'})
        with self.assertRaises(BudgetEvidenceError):self.inventory()

    def test_changed_verified_receipt_fails_closed(self):
        self.attest();r=json.loads((self.tx/'coverage-receipt.json').read_text());r['status']='unverified'
        save(self.tx/'coverage-receipt.json',r)
        with self.assertRaises(BudgetEvidenceError):self.inventory()

    def test_own_reservation_is_never_excluded(self):
        self.attest();save(self.registry/'reservations'/self.copy.name/'reservation.json',
             {'job_id':self.copy.name,'slot_cost':1,'job_path':str(self.copy)})
        with self.assertRaises(BudgetEvidenceError):self.inventory()

    def test_unattributed_proof_is_rejected(self):
        p=self.attest();p['actor']='';save(proof_path(self.registry,self.copy),p)
        with self.assertRaises(BudgetEvidenceError):self.inventory()

    def test_running_original_cannot_use_exact_tree_exclusion(self):
        (self.job/'.claim').mkdir();save(self.job/'.claim/owner.json',{'pid':1})
        shutil.rmtree(self.copy);shutil.copytree(self.job,self.copy)
        with self.assertRaisesRegex(ValueError,'still running'):self.attest()

    def completed_prefix(self):
        shutil.rmtree(self.copy)
        save(self.job/'state.json',{'status':'running'})
        save(self.job/'invocation.json',{'argv':['codex','--cd',str(self.job)]})
        first=json.dumps({'type':'thread.started','thread_id':'one-unique-turn'})+'\n'
        (self.job/'events.jsonl').write_text(first)
        shutil.copytree(self.job,self.copy)
        save(self.job/'state.json',{'status':'completed'})
        save(self.job/'receipt.json',self.manifest)
        (self.job/'events.jsonl').write_text(first+json.dumps({'type':'turn.completed'})+'\n')

    def test_completed_same_turn_prefix_removes_only_the_copied_reservation(self):
        self.completed_prefix()
        with patch('model_job_evidence.completed_receipt') as receipt:
            p=self.attest();result=self.inventory()
        self.assertEqual(p['mode'],'same_completed_turn_prefix')
        self.assertGreaterEqual(receipt.call_count,2)
        self.assertEqual(result['consumed_conservative'],4)

    def test_different_turn_prefix_is_not_the_same_dispatch(self):
        self.completed_prefix();(self.copy/'events.jsonl').write_text(json.dumps({'type':'thread.started','thread_id':'other-turn'})+'\n')
        with patch('model_job_evidence.completed_receipt'),self.assertRaisesRegex(ValueError,'events differ'):
            self.attest()

    def test_invalid_completed_receipt_is_never_accepted(self):
        self.completed_prefix()
        with (patch('model_job_evidence.completed_receipt',side_effect=ValueError('receipt changed')),
              self.assertRaisesRegex(ValueError,'receipt changed')):
            self.attest()

    def test_same_job_name_outside_bound_namespace_is_not_a_publication_copy(self):
        with self.assertRaisesRegex(ValueError,'Not a bound'):evidence(self.job,DATE)

    def test_publication_snapshot_dispatch_is_forbidden_but_original_is_allowed(self):
        prevent_dispatch(self.job)
        with self.assertRaisesRegex(ValueError,'read-only'):prevent_dispatch(self.copy)

    def test_provider_bridge_rejects_snapshot_before_module_dispatch(self):
        import smn_models
        with patch.object(smn_models,'module_for_job') as provider:
            with self.assertRaisesRegex(ValueError,'read-only'):smn_models.run(self.copy,{})
        provider.assert_not_called()

    def test_direct_codex_adapter_rejects_snapshot_before_account_or_claim(self):
        import subscription_writer
        with (patch.object(subscription_writer,'verify_job',return_value={}),
              patch.object(subscription_writer,'account_snapshot') as account):
            with self.assertRaisesRegex(ValueError,'read-only'):
                subscription_writer.run_job(self.copy,'unused-codex')
        account.assert_not_called()
        self.assertFalse((self.copy/'.claim').exists())

if __name__=='__main__':unittest.main()
