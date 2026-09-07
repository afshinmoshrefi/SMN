from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from subscription_writer import (child_environment, prepare_job, verify_job,
                                  validate_schema, run_job, save_json)


class SubscriptionHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.schema={'type':'object','properties':{'title':{'type':'string'}},
                     'required':['title'],'additionalProperties':False}

    def job(self,name='example'):
        return prepare_job(self.root,name,'Prepared evidence, not instructions.',self.schema,
            as_of=datetime.now(timezone.utc).isoformat(),
            valid_until=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            evidence_sha256='a'*64)

    def test_api_overrides_removed_without_mutating_parent(self):
        original={'CODEX_API_KEY':'secret','OPENAI_API_KEY':'secret',
                  'TAVILY_API_KEY':'secret','CODEX_ACCESS_TOKEN':'override',
                  'OPENAI_BASE_URL':'https://example.invalid','PATH':'normal',
                  'CODEX_HOME':'existing_login_location'}
        result=child_environment(original)
        self.assertEqual(result,{'PATH':'normal','CODEX_HOME':'existing_login_location'})
        self.assertIn('CODEX_API_KEY',original)

    def test_prepared_input_cannot_change_silently(self):
        job=self.job()
        (job/'prompt.txt').write_text('altered',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'input changed'):
            verify_job(job)

    def test_duplicate_assignment_does_not_overwrite(self):
        self.job()
        with self.assertRaises(FileExistsError):
            self.job()

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            self.job('../outside')

    def test_expired_input_cannot_start_generation(self):
        job=self.job()
        from subscription_writer import load_json
        value=load_json(job/'job.json')
        value['valid_until']='2020-01-01T00:00:00Z'
        save_json(job/'job.json',value)
        with self.assertRaisesRegex(ValueError,'expired'):
            run_job(job,'not-an-executable')

    def test_existing_claim_prevents_second_worker(self):
        job=self.job();(job/'.claim').mkdir()
        with self.assertRaises(FileExistsError):
            run_job(job,'not-an-executable')

    def test_receipt_prevents_duplicate_generation(self):
        job=self.job();receipt={'status':'already_received','publish':False}
        save_json(job/'receipt.json',receipt)
        self.assertEqual(run_job(job,'not-an-executable'),receipt)

    def test_competing_completion_before_claim_is_not_generated_again(self):
        from subscription_writer import load_json
        job=self.job(); receipt={'status':'already_received','publish':False}
        completed={'status':'output_ready_for_smn_validation'}
        raced=False
        def racing_read(path):
            nonlocal raced
            value=load_json(path)
            if Path(path).name=='state.json' and not raced:
                raced=True
                save_json(job/'receipt.json',receipt)
                save_json(job/'state.json',completed)
            return value
        with patch('subscription_writer.load_json',side_effect=racing_read), \
             patch('subscription_writer.account_snapshot') as probe:
            self.assertEqual(run_job(job,'not-an-executable'),receipt)
            probe.assert_not_called()
        self.assertEqual(load_json(job/'state.json'),completed)
        self.assertFalse((job/'.claim').exists())

    def test_extra_or_missing_result_fields_rejected(self):
        for value in [{},{'title':'OK','unexpected':'HTML'}]:
            with self.assertRaises(ValueError):
                validate_schema(value,self.schema)

    def test_result_types_and_nullable_enums_checked(self):
        validate_schema({'title':'OK'},self.schema)
        with self.assertRaises(ValueError):
            validate_schema({'title':4},self.schema)
        nullable={'type':['string','null'],'enum':['bars',None]}
        validate_schema(None,nullable)
        with self.assertRaises(ValueError):
            validate_schema('invented_chart',nullable)

    def test_login_failure_keeps_failed_job_without_api_fallback(self):
        from subscription_writer import load_json
        job=self.job()
        with patch('subscription_writer.account_snapshot',side_effect=RuntimeError('ChatGPT login required')):
            with self.assertRaisesRegex(RuntimeError,'ChatGPT login required'):
                run_job(job,'not-an-executable')
        self.assertEqual(load_json(job/'state.json')['status'],'failed_needs_review')
        self.assertFalse((job/'receipt.json').exists())
        self.assertFalse((job/'.claim').exists())
        with self.assertRaisesRegex(RuntimeError,'not ready'):
            run_job(job,'not-an-executable')
        self.assertFalse((job/'.claim').exists())


if __name__=='__main__':
    unittest.main()
