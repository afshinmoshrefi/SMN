"""Offline failure-injection tests. Providers, browser and installer are mocked.

The actual eligibility, package, coverage, snapshot and resume paths run against
temporary files. Factual source/engine contracts have their separate test suite.
"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editorial_gate
import model_job_evidence
import production_continuity as rolling
import production_continuity_schedule as schedule
import smn_daily
import smn_operational_alerts as alerts
import smn_subscription_daily
import subscription_publication as publication
from subscription_writer import save_json, sha256

DAY = '2026-10-07'
AT_DEADLINE = datetime(2026, 10, 7, 11, tzinfo=timezone.utc)
SYMBOLS = ['ALB', 'IWM', 'HPQ', 'PEP', 'GC', 'SPX']
COMMIT = 'a'*40


class PublicationFailures(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)/'edition'
        self.web = Path(temp.name)/'web'
        self.web.mkdir()
        self.prepare = Mock(side_effect=self.prepare_transaction)
        self.activate = Mock(side_effect=self.activate_transaction)
        self.finish = Mock(side_effect=self.finish_transaction)
        self.rollback = Mock(side_effect=self.rollback_transaction)
        installer = types.SimpleNamespace(WEB=self.web, prepare=self.prepare,
            activate=self.activate, finish=self.finish, rollback=self.rollback)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, {'install_smn_primary_edition': installer}))
        self.stack.enter_context(patch.object(rolling, 'require_policy'))
        self.stack.enter_context(patch.object(rolling.subprocess, 'check_output', return_value=COMMIT))
        self.stack.enter_context(patch('editorial_gate.verify_complete'))
        self.stack.enter_context(patch('editorial_gate.verify_content'))
        self.browser = self.stack.enter_context(patch.object(rolling.subprocess, 'run', side_effect=self.browser_check))
        self.records = {}
        self.active_record = None
        posts = [{'symbol': s} for s in SYMBOLS]
        save_json(self.root/'production/posts.json', posts)
        save_json(self.root/'input-selection.json', {'date': DAY, 'symbols': SYMBOLS,
            'files': {'production/posts.json': rolling._sha(self.root/'production/posts.json')}})
        save_json(self.root/'smn-daily-state.json', {'date': DAY, 'profile': 'chatgpt',
            'publication_origin': 'https://seasonalmarketnews.com',
            'articles': {s: {'finalized': True, 'review_stage': 'review'} for s in SYMBOLS}})
        for symbol in SYMBOLS:
            self.subject(symbol)

    def subject(self, symbol, failure=None, editorial_warning=False):
        result = self.root/'results'/symbol
        result.mkdir(parents=True, exist_ok=True)
        article = {'title': symbol+' outlook', 'dek': 'Source-backed investor context.'}
        review = {'passed': failure is None and not editorial_warning,
                  'checks': {k: {'passed': True} for k in publication.CHECKS}, 'issues': []}
        if failure:
            review['checks']['facts_and_sources']['passed'] = False
            review['issues'] = [{'category': 'facts', 'problem': failure}]
        if editorial_warning:
            review['checks']['why_now_and_opening']['passed'] = False
            review['issues'] = [{'category': 'style', 'problem': 'Opening connection could be clearer'}]
        job = self.root/'jobs'/(symbol+'-'+DAY.replace('-', '')+'-review')
        save_json(job/'output.json', review)
        save_json(result/'article.json', article)
        save_json(result/'mechanical-checks.json', {'passed': True, 'article_sha256': publication.digest(article)})
        save_json(result/'bundle.json', {})
        save_json(result/'review-binding.json', {'review_stage': 'review',
            'article_sha256': publication.digest(article), 'review_sha256': rolling._sha(job/'output.json')})
        url = 'https://seasonalmarketnews.com/editions/'+DAY+'/'+symbol+'/article.html'
        markup = '<html><meta name="robots" content="index,follow"><meta name="smn-generation" content="subscription"><link rel="canonical" href="'+url+'"><body>'+symbol+'</body></html>'
        (result/'article.html').write_text(markup, encoding='utf-8')
        save_json(result/'visual-checks.json', {'passed': True, 'article_html_sha256': rolling._sha(result/'article.html')})
        save_json(result/'commission.json', {'history_status': 'ok', 'production_article': {
            'symbol': symbol, 'market_family': 'Fixture market', 'lookback_years': '10',
            'published_date': DAY, 'source_mode': 'selected_inputs'}})
        save_json(result/'hero-asset.json', {'url': 'assets/hero.png', 'alt': 'Illustration'})
        (result/'assets').mkdir(exist_ok=True)
        (result/'assets/hero.png').write_bytes(b'fixture-hero')
        (result/'evidence').mkdir(exist_ok=True)
        role = {'provider': 'openai', 'model': 'gpt-test', 'billing_source': 'subscription', 'api_fallback': False}
        save_json(result/'generation.json', {'article_sha256': publication.digest(article),
            'writers': [role], 'reviewer': role, 'summary': role})

    def prepare_transaction(self, package):
        manifest = publication.read(package/'manifest.json')
        record = self.web.parent/'records'/manifest['transaction_id']
        record.mkdir(parents=True)
        receipt = {**manifest, 'status': 'prepared', 'record': str(record),
                   'urls': [row['url'] for row in publication.read(package/'entries.json')]}
        self.records[str(record)] = (package, receipt)
        save_json(record/'receipt.json', receipt)
        return receipt

    def activate_transaction(self, record):
        self.active_record = record
        package, receipt = self.records[str(record)]
        receipt = {**receipt, 'status': 'active_pending_live_verification'}
        save_json(record/'receipt.json', receipt)
        return receipt

    def finish_transaction(self, record):
        package, receipt = self.records[str(record)]
        for file in package.rglob('*'):
            if file.is_file():
                target = self.web/file.relative_to(package)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
        receipt = {**receipt, 'status': 'live_verified', 'production_written': True}
        save_json(record/'receipt.json', receipt)
        return receipt

    def rollback_transaction(self, record):
        receipt = publication.read(record/'receipt.json')
        save_json(record/'receipt.json', {**receipt, 'status': 'rolled_back'})

    def browser_check(self, command, **kwargs):
        tx = Path(command[-1])
        # Exact v1/v2 incident: missing primary-activation.json must fail before
        # browser verification; v2 writes this native handoff before invoking it.
        activation = publication.read(tx/'primary-activation.json')
        self.assertEqual(activation['status'], 'active_pending_live_verification')
        self.assertEqual(activation['transaction_id'], publication.read(tx/'production-stage.json')['transaction_id'])
        save_json(tx/'live-verification.json', {'passed': True,
            'origin': 'https://seasonalmarketnews.com', 'deterministic_landing': True})

    def publish(self):
        return rolling.publish_available(self.root, DAY, 'production', repo=self.root)

    def test_one_factual_hold_publishes_five_with_truthful_notice_and_repeat_is_noop(self):
        self.subject('HPQ', failure='Chart method not established')
        before = {p.relative_to(self.root): p.read_bytes() for folder in ('results', 'jobs')
                  for p in (self.root/folder).rglob('*') if p.is_file()}
        receipt = self.publish()
        self.assertEqual(receipt['published_symbols'], ['ALB', 'IWM', 'PEP', 'GC', 'SPX'])
        self.assertEqual(receipt['pending_symbols'], ['HPQ'])
        self.assertFalse(receipt['complete'])
        self.assertEqual(receipt['coverage_status'], 'partial')
        # Native installer rendering is verified separately with a temporary
        # candidate; this harness mocks the installer and exercises partitioning.
        self.assertEqual(publication.read(self.web/'manifest.json')['pending_symbols'], ['HPQ'])
        self.assertEqual(self.publish(), receipt)
        self.assertEqual(self.prepare.call_count, 1)
        self.assertEqual(self.activate.call_count, 1)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for folder in ('results', 'jobs')
                  for p in (self.root/folder).rglob('*') if p.is_file()})

    def test_multiple_holds_do_not_block_good_articles(self):
        for symbol in ('HPQ', 'ALB'):
            self.subject(symbol, failure='Unsupported fact')
        receipt = self.publish()
        self.assertEqual(len(receipt['published_symbols']), 4)
        self.assertEqual(receipt['pending_symbols'], ['ALB', 'HPQ'])

    def test_editorial_only_warning_is_retained_and_does_not_block(self):
        self.subject('HPQ', editorial_warning=True)
        receipt = self.publish()
        self.assertTrue(receipt['complete'])
        saved = publication.read(self.root/'jobs/HPQ-20261007-review/output.json')
        self.assertFalse(saved['passed'])
        self.assertEqual(saved['issues'][0]['category'], 'style')

    def test_factual_failure_in_style_category_still_holds(self):
        review = {'passed': False, 'checks': {k: {'passed': True} for k in publication.CHECKS},
                  'issues': [{'category': 'style', 'problem': 'Unsupported chart methodology'}]}
        self.assertFalse(editorial_gate.hard_review_passed(review))

    def test_stale_review_is_excluded_without_rerunning_good_subjects(self):
        result = self.root/'results/HPQ'
        save_json(result/'article.json', {'title': 'Changed after review'})
        self.assertEqual(self.publish()['pending_symbols'], ['HPQ'])

    def test_stale_public_receipt_refuses_duplicate_or_silent_success(self):
        self.publish()
        (self.web/'editions'/DAY/'ALB/article.html').write_text('Changed public article')
        with self.assertRaisesRegex(ValueError, 'published content changed'):
            self.publish()
        self.assertEqual(self.prepare.call_count, 1)

    def test_browser_failure_rolls_back_then_new_attempt_preserves_failed_record(self):
        self.browser.side_effect = RuntimeError('Native browser verifier failed')
        with self.assertRaisesRegex(RuntimeError, 'verifier failed'):
            self.publish()
        failed = self.active_record
        self.assertEqual(publication.read(failed/'receipt.json')['status'], 'rolled_back')
        self.assertFalse((self.root/'production-publication-receipt.json').exists())
        self.browser.side_effect = self.browser_check
        self.assertTrue(self.publish()['complete'])
        self.assertNotEqual(failed, self.active_record)
        self.assertEqual(publication.read(failed/'receipt.json')['status'], 'rolled_back')

    def test_interrupted_after_finish_reconciles_receipt_without_second_activation(self):
        original = rolling._write
        def crash(path, data):
            if Path(path) == self.root/'production-publication-receipt.json':
                raise RuntimeError('Crash after remote finish')
            original(path, data)
        with patch.object(rolling, '_write', side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, 'Crash'):
                self.publish()
        self.assertTrue(self.publish()['complete'])
        self.assertEqual(self.activate.call_count, 1)
        self.assertEqual(self.browser.call_count, 1)

    def test_three_failed_publication_attempts_stop_without_implicit_fourth(self):
        self.browser.side_effect = RuntimeError('browser failure')
        for _ in range(3):
            with self.assertRaisesRegex(RuntimeError, 'browser failure'):
                self.publish()
        with self.assertRaisesRegex(ValueError, 'Three publication attempts exhausted'):
            self.publish()
        self.assertEqual(self.activate.call_count, 3)

    def test_interrupted_finish_does_not_accept_stale_remote_receipt(self):
        original = rolling._write
        def crash(path, data):
            if Path(path) == self.root/'production-publication-receipt.json':
                raise RuntimeError('Crash after remote finish')
            original(path, data)
        with patch.object(rolling, '_write', side_effect=crash):
            with self.assertRaises(RuntimeError): self.publish()
        (self.web/'editions'/DAY/'ALB/article.html').write_text('Tampered after finish')
        with self.assertRaisesRegex(ValueError, 'published content changed'): self.publish()
        self.assertEqual(self.activate.call_count, 1)


class RetryAndReceiptFailures(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.day = smn_daily.Day(self.root, DAY, roles={}, profile='chatgpt')
        self.job = self.root/'jobs/ALB-20261007-review'
        self.job.mkdir(parents=True)

    def test_running_or_uncertain_job_is_never_archived_or_dispatched_again(self):
        for status in ('running', 'output_ready_for_smn_validation', 'failed_needs_review'):
            with self.subTest(status=status):
                save_json(self.job/'state.json', {'status': status, 'reason': 'Interrupted evidence'})
                before = (self.job/'state.json').read_bytes()
                with patch.object(smn_daily.smn_models, 'run') as provider:
                    for _ in range(2):
                        with self.assertRaisesRegex(smn_daily.Hold, 'explicit recovery'):
                            self.day.run_job(self.job)
                    provider.assert_not_called()
                self.assertEqual((self.job/'state.json').read_bytes(), before)
                self.assertEqual(list(self.job.glob('*-attempt-*')), [])
        save_json(self.job/'state.json', {'status': 'ready'})
        (self.job/'.claim').mkdir()
        save_json(self.job/'.claim/owner.json', {'pid': 1, 'host': 'unknown-owner'})
        with patch.object(smn_daily.smn_models, 'run') as provider:
            with self.assertRaisesRegex(smn_daily.Hold, 'unresolved claim'):
                self.day.run_job(self.job)
            provider.assert_not_called()
        self.assertTrue((self.job/'.claim/owner.json').exists())

    def saved_receipt(self):
        (self.job/'prompt.txt').write_text('Fixed evidence')
        save_json(self.job/'schema.json', {'type': 'object'})
        save_json(self.job/'output.json', {'passed': True})
        hashes = {name: rolling._sha(self.job/name) for name in ('prompt.txt', 'schema.json')}
        manifest = {'job_id': self.job.name, 'stage': 'review', 'provider': 'openai',
            'model': 'gpt-test', 'effort': 'low', 'publish': False,
            'evidence_sha256': 'bound-evidence', 'input_hashes': hashes,
            'valid_until': '2026-10-07T10:00:00+00:00'}
        save_json(self.job/'job.json', manifest)
        receipt = {'job_id': self.job.name, 'stage': 'review', 'provider': 'openai',
            'model_requested': 'gpt-test', 'effort_requested': 'low', 'publish': False,
            'status': 'output_ready_for_smn_validation', 'billing_source': 'subscription',
            'api_fallback': False, 'evidence_sha256': 'bound-evidence', 'input_hashes': hashes,
            'output_sha256': rolling._sha(self.job/'output.json'),
            'finished_utc': '2026-10-07T09:00:00+00:00'}
        save_json(self.job/'receipt.json', receipt)
        return receipt

    def test_successful_receipt_reused_after_expiry_and_budget_exhaustion_without_provider(self):
        receipt = self.saved_receipt()
        self.day.max_jobs = 0
        with patch('smn_models.run') as provider:
            self.assertEqual(self.day.run_job(self.job), receipt)
            provider.assert_not_called()

    def test_changed_input_output_receipt_and_late_completion_hold_without_provider(self):
        for field in ('input', 'output', 'model', 'completion'):
            with self.subTest(field=field):
                receipt = self.saved_receipt()
                if field == 'input': (self.job/'prompt.txt').write_text('Changed evidence')
                if field == 'output': save_json(self.job/'output.json', {'passed': False})
                if field == 'model': receipt['model_requested'] = 'wrong-model'
                if field == 'completion': receipt['finished_utc'] = '2026-10-07T11:00:00+00:00'
                save_json(self.job/'receipt.json', receipt)
                with patch('smn_models.run') as provider, self.assertRaises(smn_daily.Hold):
                    self.day.run_job(self.job)
                provider.assert_not_called()

    def test_provider_transient_failure_exhausts_cap_and_repeat_cannot_reset_it(self):
        self.day.max_jobs = 2
        with patch('smn_models.run', side_effect=RuntimeError('503 overloaded')) as provider, patch('smn_daily.time.sleep'):
            with self.assertRaisesRegex(smn_daily.Hold, 'budget'):
                self.day.run_job(self.job)
            self.assertEqual(provider.call_count, 2)
            with self.assertRaisesRegex(smn_daily.Hold, 'budget'):
                self.day.run_job(self.job)
            self.assertEqual(provider.call_count, 2)

    def test_all_retry_kinds_count_in_scheduler_and_daily_ledger(self):
        for prefix in model_job_evidence.ATTEMPT_PREFIXES:
            (self.job/(prefix+'1')).mkdir()
        self.assertEqual(self.day.jobs_used(), 4)


class SchedulerRecoveryFailures(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        fake = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=4, flock=lambda *_: None)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, {'fcntl': fake}))
        self.stack.enter_context(patch.object(schedule, '_eligible', return_value=True))
        self.stack.enter_context(patch.object(rolling, 'require_policy'))

    def test_terminal_hold_is_quiet_until_repaired_evidence_then_resumes_with_diagnostic(self):
        fingerprint, _ = schedule._fingerprint(self.root, DAY)
        path = self.root/DAY/'continuity-progress.json'
        save_json(path, {'date': DAY, 'status': 'needs_attention', 'fingerprint': fingerprint,
                         'reason': 'Provider failed', 'unchanged_attempts': 6})
        with patch.object(smn_subscription_daily, 'run', return_value={'providers': {'chatgpt': {'passed': True}}}) as run,patch.object(schedule,'verify_generation',return_value={'complete':True,'verified_symbols':['ALB']}):
            self.assertEqual(schedule.progress(self.root, DAY, AT_DEADLINE)['status'], 'needs_attention')
            run.assert_not_called()
            save_json(self.root/DAY/'chatgpt/research/ALB.json', {'repaired_source': True})
            record = schedule.progress(self.root, DAY, AT_DEADLINE)
            self.assertEqual(record['status'], 'generation_complete')
            self.assertEqual(record['recovered_from']['reason'], 'Provider failed')
            run.assert_called_once()

    def test_budget_exhaustion_still_allows_deadline_delivery_from_saved_approvals(self):
        jobs = self.root/DAY/'chatgpt/jobs'
        jobs.mkdir(parents=True)
        for i in range(40): (jobs/str(i)).mkdir()
        with patch.object(smn_subscription_daily, 'run') as run:
            self.assertEqual(schedule.progress(self.root, DAY, AT_DEADLINE)['status'], 'needs_attention')
            run.assert_not_called()
        with patch.object(rolling, 'publish_available', return_value={'status': 'live_verified', 'complete': False}) as publish:
            self.assertEqual(schedule.deliver(self.root, DAY, AT_DEADLINE)['status'], 'live_verified')
            publish.assert_called_once()

    def test_delivery_retry_preserves_held_status_instead_of_returning_empty_success(self):
        with patch.object(rolling, 'publish_available', side_effect=ValueError('Provider unavailable')) as publish:
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                schedule.deliver(self.root, DAY, AT_DEADLINE)
            record = schedule.deliver(self.root, DAY, AT_DEADLINE+timedelta(minutes=1))
            self.assertEqual(record['status'], 'held')
            self.assertIn('unavailable', record['reason'])
            self.assertEqual(publish.call_count, 1)

    def test_complete_repeat_rechecks_public_receipt_instead_of_cached_success(self):
        with patch.object(rolling, 'publish_available', return_value={'status': 'live_verified', 'complete': True}) as publish:
            schedule.deliver(self.root, DAY, AT_DEADLINE)
            publish.side_effect = ValueError('Previously published content changed')
            with self.assertRaisesRegex(ValueError, 'content changed'):
                schedule.deliver(self.root, DAY, AT_DEADLINE+timedelta(minutes=1))
            self.assertEqual(publish.call_count, 2)


class AlertDeliveryFailures(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(alerts.os.environ, {
            'RESEND_API_KEY': 'synthetic-test-key', 'SMN_DASHBOARD_STATE': str(self.root/'dashboard')}))
        self.incident = alerts._incident(DAY, 'deadline-missed', 'One article remains pending', 'SMN coverage pending')
        self.stack.enter_context(patch.object(alerts, 'inspect', return_value=[self.incident]))
        self.settings = {'alerts_enabled': True, 'alert_recipients': ['owner@example.com'],
                         'daily_generation': {'timezone': 'America/New_York'}}

    def test_mock_acceptance_is_not_delivery_and_exact_get_receipt_establishes_delivery(self):
        sender = Mock(return_value={'status': 'accepted', 'provider_id': 'known-id'})
        getter = Mock(return_value={'id': 'known-id', 'to': ['owner@example.com'],
                                   'subject': self.incident['subject'], 'last_event': 'delivered'})
        first = alerts.run(self.root, AT_DEADLINE, self.settings, sender=sender, delivery_fetcher=getter)
        self.assertEqual((first['accepted'], first['delivered']), (1, 0))
        getter.assert_not_called()
        second = alerts.run(self.root, AT_DEADLINE+timedelta(minutes=5), self.settings, sender=sender, delivery_fetcher=getter)
        self.assertEqual((second['accepted'], second['delivered']), (0, 1))
        third = alerts.run(self.root, AT_DEADLINE+timedelta(minutes=40), self.settings, sender=sender, delivery_fetcher=getter)
        self.assertEqual(third['delivered'], 0)
        sender.assert_called_once()
        getter.assert_called_once_with('known-id')

    def test_mock_relay_failure_is_bounded_and_never_claims_delivery(self):
        sender = Mock(side_effect=OSError('relay timeout'))
        getter = Mock(side_effect=AssertionError('No accepted provider ID'))
        for minute in (0, 1, 5, 10, 15, 20):
            result = alerts.run(self.root, AT_DEADLINE+timedelta(minutes=minute), self.settings,
                               sender=sender, delivery_fetcher=getter)
            self.assertEqual((result['accepted'], result['delivered']), (0, 0))
        self.assertEqual(sender.call_count, 3)
        getter.assert_not_called()
        state = publication.read(self.root/'operational-alerts-state.json')
        self.assertEqual(state['delivered'], {})
        self.assertEqual(state['accepted'], {})

    def test_identity_mismatch_and_provider_failure_do_not_trigger_duplicate_alert(self):
        sender = Mock(return_value={'status': 'accepted', 'provider_id': 'known-id'})
        getter = Mock(return_value={'id': 'wrong-id', 'to': ['owner@example.com'],
                                   'subject': self.incident['subject'], 'last_event': 'delivered'})
        alerts.run(self.root, AT_DEADLINE, self.settings, sender=sender, delivery_fetcher=getter)
        self.assertEqual(alerts.run(self.root, AT_DEADLINE+timedelta(minutes=1), self.settings,
                                   sender=sender, delivery_fetcher=getter)['delivered'], 0)
        getter.return_value['id'] = 'known-id'
        getter.return_value['last_event'] = 'bounced'
        self.assertEqual(alerts.run(self.root, AT_DEADLINE+timedelta(minutes=31), self.settings,
                                   sender=sender, delivery_fetcher=getter)['delivered'], 0)
        sender.assert_called_once()
        state = publication.read(self.root/'operational-alerts-state.json')
        self.assertEqual(state['delivered'], {})


if __name__ == '__main__':
    unittest.main()
