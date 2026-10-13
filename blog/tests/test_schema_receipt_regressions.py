"""October 13 regressions: no provider, browser or production operation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import smn_research
import smn_models
import smn_recovery
import subscription_writer as writer
from test_smn_reliability_regressions import PublicationFailures, DAY, SYMBOLS


class SchemaFailures(unittest.TestCase):
    def test_research_schema_requires_all_nested_properties(self):
        writer.validate_strict_output_schema(smn_research.SCHEMA)
        context = smn_research.SCHEMA['properties']['material_context']['items']
        self.assertIn('alternative_sources', context['required'])

    def broken(self):
        schema = deepcopy(smn_research.SCHEMA)
        schema['properties']['material_context']['items']['required'].remove('alternative_sources')
        return schema

    def test_invalid_research_schema_rejected_before_reservation_or_preparation(self):
        roles = {'research': {'provider': 'codex', 'effort': 'xhigh', 'model': 'gpt-6-astra'}}
        with patch('daily_budget.prepare') as reserve, patch.object(writer, 'prepare_job') as prepare:
            with self.assertRaisesRegex(ValueError, 'invalid_json_schema'):
                smn_models.prepare(roles, 'research', Path('/synthetic/jobs'), 'research', 'prompt', self.broken())
            reserve.assert_not_called()
            prepare.assert_not_called()

    def test_saved_invalid_schema_rejected_before_claim_account_probe_or_process(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            writer.save_json(job/'schema.json', self.broken())
            writer.save_json(job/'state.json', {'status': 'ready'})
            with patch.object(writer, 'verify_job', return_value={}), patch.object(
                    writer, 'account_snapshot') as account, patch.object(writer.subprocess, 'Popen') as process:
                with self.assertRaisesRegex(ValueError, 'invalid_json_schema'):
                    writer.run_job(job, 'not-a-real-cli')
                account.assert_not_called(); process.assert_not_called()
                self.assertFalse((job/'.claim').exists())
                self.assertEqual(writer.load_json(job/'state.json'), {'status': 'ready'})

    def test_completed_receipt_is_reused_without_revalidating_old_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            writer.save_json(job/'receipt.json', {'completed': 'existing-bound-receipt'})
            with patch.object(writer, 'verify_job', return_value={}), patch.object(
                    writer, 'validate_strict_output_schema') as schema:
                self.assertEqual(writer.run_job(job, 'not-a-real-cli')['completed'], 'existing-bound-receipt')
                schema.assert_not_called()

    def test_schema_failures_are_non_retryable_and_security_stays_security(self):
        result = smn_recovery.classify('invalid_json_schema: required field missing')
        self.assertEqual(result['category'], 'configuration')
        self.assertFalse(result['automatic_retry_allowed'])
        self.assertEqual(smn_recovery.classify('unsafe tool plus invalid_json_schema')['category'], 'security')
        self.assertTrue(smn_recovery.classify('429 temporary provider overload')['automatic_retry_allowed'])


class PendingReceiptTransition(PublicationFailures):
    # Reuse the actual publication/snapshot/package paths with mocked native I/O.
    def pending_notice(self):
        selected = self.root/'input-selection.json'
        posts = self.root/'production/posts.json'
        original = (selected.read_bytes(), posts.read_bytes())
        selected.unlink(); posts.unlink()
        receipt = self.publish()
        self.assertEqual(receipt['selection_status'], 'pending')
        self.assertEqual(receipt['expected_symbols'], [])
        selected.write_bytes(original[0]); posts.write_bytes(original[1])
        return receipt

    def test_empty_pending_notice_can_freeze_once_and_publish_all(self):
        old = self.pending_notice()
        old_receipt_bytes = (Path(old['transaction_root'])/'coverage-receipt.json').read_bytes()
        receipt = self.publish()
        self.assertEqual(receipt['expected_symbols'], SYMBOLS)
        self.assertEqual(receipt['published_symbols'], SYMBOLS)
        self.assertEqual(receipt['selection_status'], 'frozen')
        self.assertEqual(receipt['revision'], old['revision'] + 1)
        self.assertEqual((Path(old['transaction_root'])/'coverage-receipt.json').read_bytes(), old_receipt_bytes)
        self.assertEqual(self.publish(), receipt)

    def test_empty_pending_notice_can_freeze_with_zero_approved_articles(self):
        self.pending_notice()
        for symbol in SYMBOLS:
            self.subject(symbol, failure='Unverified primary claim')
        receipt = self.publish()
        self.assertEqual(receipt['published_symbols'], [])
        self.assertEqual(receipt['pending_symbols'], SYMBOLS)
        self.assertFalse(receipt['complete'])
        self.assertEqual(receipt['selection_status'], 'frozen')

    def test_wrong_date_pending_notice_is_not_admitted(self):
        self.pending_notice()
        path = self.root/'production-publication-receipt.json'
        value = writer.load_json(path); value['edition_date'] = '2026-10-06'; writer.save_json(path, value)
        with self.assertRaisesRegex(ValueError, 'coverage partition'):
            self.publish()

    def test_frozen_receipt_never_reopens_for_a_changed_selection(self):
        self.pending_notice(); self.publish()
        path = self.root/'input-selection.json'; value = writer.load_json(path)
        value['symbols'] = list(reversed(value['symbols'])); writer.save_json(path, value)
        with self.assertRaises(ValueError):
            self.publish()

    def test_only_a_genuine_empty_pending_notice_can_transition(self):
        self.pending_notice()
        path = self.root/'production-publication-receipt.json'; original = writer.load_json(path)
        for change in [{'selection_status': 'frozen'}, {'selection_fingerprint': 'foreign'},
                       {'expected_symbols': [SYMBOLS[0]]}, {'published_symbols': [SYMBOLS[0]]},
                       {'review_stages': {SYMBOLS[0]: 'review'}}, {'review_stages': []},
                       {'complete': True}, {'coverage_status': 'complete'}]:
            with self.subTest(change=change):
                writer.save_json(path, {**original, **change})
                with self.assertRaises(ValueError):self.publish()


def load_tests(loader, tests, pattern):
    # Only the ten new regressions here; the shared harness's inherited cases
    # run once in their own module and must not inflate this module's count.
    suite = loader.loadTestsFromTestCase(SchemaFailures)
    suite.addTests(PendingReceiptTransition(name) for name in sorted(PendingReceiptTransition.__dict__)
                   if name.startswith('test_'))
    return suite


if __name__ == '__main__':
    unittest.main()
