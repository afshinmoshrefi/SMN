import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smn_cost_report import discover_runs, last_run_status, summarize_run


def saved(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class CostReportTests(unittest.TestCase):
    def test_subscription_primary_nested_and_direct_roots_are_discovered_safely(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            primary = state / 'subscription-primary'
            saved(primary / 'comparison-state.json', {'reader_provider': 'chatgpt',
                  'comparison_dates': ['2026-09-30'], 'target': 'production'})
            for provider, model in (('chatgpt', 'gpt-6-sol'), ('claude', 'claude-sonnet-5')):
                job = primary / '2026-09-30' / provider / 'jobs' / (provider.upper()+'-20260930-write')
                saved(job / 'job.json', {'job_id': job.name, 'stage': 'write', 'model': model})
                saved(job / 'state.json', {'status': 'output_ready_for_smn_validation'})
            runs = discover_runs(state)
            self.assertEqual({(r['run_id'], r['kind'], r['provider'], r['role']) for r in runs}, {
                ('2026-09-30-chatgpt', 'subscription-primary-chatgpt', 'chatgpt', 'reader'),
                ('2026-09-30-claude', 'subscription-primary-claude', 'claude', 'comparison')})
            self.assertTrue(all('path' not in r for r in runs))

            direct = state / 'generator-qualification-v2'
            saved(direct / 'comparison-state.json', {'reader_provider': 'chatgpt',
                  'comparison_dates': [], 'target': 'dev'})
            job = direct / '2026-09-30' / 'chatgpt' / 'jobs' / 'A-20260930-write'
            saved(job / 'job.json', {'job_id': job.name, 'stage': 'write', 'model': 'gpt-6-sol'})
            saved(job / 'state.json', {'status': 'output_ready_for_smn_validation'})
            direct_runs = discover_runs(direct)
            self.assertEqual([r['run_id'] for r in direct_runs], ['2026-09-30-chatgpt'])

    def test_subscription_primary_discovery_ignores_symlinked_date_and_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            primary = state / 'subscription-primary'
            saved(primary / 'comparison-state.json', {'reader_provider': 'chatgpt',
                  'comparison_dates': [], 'target': 'dev'})
            outside = state / 'outside'
            job = outside / 'jobs' / 'A-20260930-write'
            saved(job / 'job.json', {'job_id': job.name, 'stage': 'write'})
            saved(job / 'state.json', {'status': 'output_ready_for_smn_validation'})
            day = primary / '2026-09-30'
            day.mkdir()
            try:
                (day / 'claude').symlink_to(outside, target_is_directory=True)
                (primary / '2026-09-29').symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable')
            self.assertEqual(discover_runs(state), [])

    def test_last_run_status_is_allowlisted_and_redacted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved(root / 'last-run.json', {'utc': '2026-09-30T12:00:00Z', 'date': '2026-09-30',
                  'status': 'held', 'target': 'production', 'publish': True, 'exit_code': 2,
                  'reason': 'Auth failed for bearer secret-value sk-test-token at a@example.com /var/private/state',
                  'result': {'providers': {'chatgpt': {'passed': True, 'status': 'ready',
                                                       'reason': 'see /var/private/file'},
                                           'claude': {'passed': False, 'status': 'held'}},
                             'secret_path': '/var/private/path'}, 'extra': 'not allowed'})
            status = last_run_status(root)
            self.assertEqual(status['status'], 'held')
            self.assertEqual(status['providers']['chatgpt']['status'], 'ready')
            combined = json.dumps(status)
            for secret in ('secret-value', 'sk-test-token', 'a@example.com', '/var/private', 'secret_path', 'extra'):
                self.assertNotIn(secret, combined)

    def test_job_cost_counts_once_and_missing_usage_is_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / '2026-09-26'
            first = root / 'jobs' / 'ABC-20260926-write'
            saved(first / 'job.json', {'job_id': first.name, 'stage': 'write', 'model': 'claude-opus-5-5'})
            saved(first / 'state.json', {'status': 'output_ready_for_smn_validation'})
            saved(first / 'receipt.json', {'billing_source': 'subscription', 'usage': {'inputTokens': 1}})
            model = {'inputTokens': 2, 'cacheReadInputTokens': 3, 'cacheCreationInputTokens': 4,
                     'outputTokens': 5, 'costUSD': 0.25}
            saved(first / 'turn-usage.json', {'modelUsage': {'claude-opus-5-5': model}})
            saved(first / 'result.json', {'modelUsage': {'claude-opus-5-5': model}, 'total_cost_usd': 0.25})
            failed = root / 'jobs' / 'ABC-20260926-repair'
            saved(failed / 'job.json', {'job_id': failed.name, 'stage': 'repair', 'model': 'claude-opus-5-5'})
            saved(failed / 'state.json', {'status': 'failed_needs_review'})
            report = summarize_run(root)
            self.assertEqual(report['totals']['reported_api_equivalent_usd'], 0.25)
            self.assertEqual(report['totals']['input_tokens'], 2)
            self.assertEqual((report['totals']['failed'], report['totals']['retries']), (1, 1))
            self.assertIn('output_tokens', report['missing_fields'])
            self.assertIsNone(report['billing']['actual_cash_charged_usd'])
            self.assertEqual(report['billing']['source'], 'unknown')
            self.assertEqual(report['by_stage']['write']['jobs'], 1)
            self.assertEqual(report['by_model']['claude-opus-5-5']['jobs'], 1)
            self.assertEqual(report['by_article']['ABC']['jobs'], 2)
            self.assertEqual(report['totals']['coverage']['input_tokens'],
                             {'reported_jobs': 1, 'total_jobs': 2})
            saved(root / 'coordination-usage.json', {'provider': 'codex', 'model': 'gpt-6-astra',
                  'input_tokens': 100, 'cached_input_tokens': 50, 'output_tokens': 30,
                  'reasoning_output_tokens': 10, 'api_equivalent_usd': None,
                  'scope': 'generation monitoring only', 'complete': False, 'secret': 'omit'})
            with_overhead = summarize_run(root)
            self.assertEqual(with_overhead['overhead']['coordination_usage']['input_tokens'], 100)
            self.assertNotIn('secret', with_overhead['overhead']['coordination_usage'])
            self.assertEqual(with_overhead['totals'], report['totals'])

    def test_multiple_models_and_comparison_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            run = state / 'comparisons' / 'control-A'
            job = run / 'jobs' / 'XYZ-20260926-review'
            saved(job / 'job.json', {'job_id': job.name, 'stage': 'review', 'model': 'claude-sonnet-5'})
            saved(job / 'state.json', {'status': 'output_ready_for_smn_validation'})
            saved(job / 'receipt.json', {'billing_source': 'subscription', 'seconds': 9})
            saved(job / 'turn-usage.json', {'modelUsage': {
                'claude-sonnet-5': {'inputTokens': 10, 'outputTokens': 2, 'costUSD': 0.1},
                'claude-haiku-4-5': {'inputTokens': 3, 'outputTokens': 1, 'costUSD': 0.02}}})
            report = summarize_run(run)
            self.assertEqual(report['totals']['input_tokens'], 13)
            self.assertEqual(report['totals']['reported_api_equivalent_usd'], 0.12)
            self.assertEqual(report['totals']['seconds'], 9)
            self.assertIsNone(report['by_model']['claude-sonnet-5']['seconds'])
            self.assertEqual(report['by_model']['claude-sonnet-5']['coverage']['seconds']['reported_jobs'], 0)
            saved(run / 'run-metadata.json', {'label': 'Claude comparison run today',
                  'source_date': '2026-09-25', 'started_utc': '2026-09-26T14:00:00Z',
                  'status': 'running', 'publication_mode': 'private_preview'})
            self.assertEqual(summarize_run(run)['status'], 'running')
            self.assertEqual(summarize_run(run)['job_status'], 'complete')
            self.assertEqual(summarize_run(run)['source_date'], '2026-09-25')
            self.assertEqual(len(discover_runs(state)), 1)
            self.assertEqual(discover_runs(state)[0]['kind'], 'comparison')

    def test_result_fallback_and_ready_job_have_partial_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / '2026-09-26'
            completed = root / 'jobs' / 'XYZ-20260926-write'
            saved(completed / 'job.json', {'job_id': completed.name, 'stage': 'write', 'model': 'claude-opus-5-5'})
            saved(completed / 'state.json', {'status': 'output_ready_for_smn_validation'})
            (completed / 'result.json').write_text('non-json event\n' + json.dumps({
                'type': 'result', 'modelUsage': {'claude-opus-5-5': {'inputTokens': 7, 'costUSD': 0.03}}}) + '\n')
            ready = root / 'jobs' / 'XYZ-20260926-review'
            saved(ready / 'job.json', {'job_id': ready.name, 'stage': 'review', 'model': 'claude-sonnet-5'})
            saved(ready / 'state.json', {'status': 'ready'})
            shared = root / 'jobs' / 'EDITION-20260926-landing-visual'
            saved(shared / 'job.json', {'job_id': shared.name, 'stage': 'landing-visual',
                                       'model': 'claude-haiku-4-5'})
            saved(shared / 'state.json', {'status': 'output_ready_for_smn_validation'})
            saved(shared / 'turn-usage.json', {'modelUsage': {'claude-haiku-4-5':
                  {'inputTokens': 4, 'costUSD': 0.02}}})
            report = summarize_run(root)
            self.assertEqual(report['status'], 'unknown')
            self.assertEqual(report['job_status'], 'incomplete')
            self.assertEqual(report['totals']['reported_api_equivalent_usd'], 0.05)
            self.assertEqual(report['article_totals']['reported_api_equivalent_usd'], 0.03)
            self.assertEqual(report['overhead']['batch']['reported_api_equivalent_usd'], 0.02)
            self.assertIsNone(next(job for job in report['jobs'] if job['scope'] == 'batch')['article'])
            self.assertEqual(next(job for job in report['jobs'] if job['stage'] == 'write')['usage_source'],
                             'result.json')

    def test_does_not_follow_job_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            outside = state / 'outside'
            saved(outside / 'job.json', {'job_id': 'secret', 'stage': 'write'})
            jobs = state / '2026-09-26' / 'jobs'
            jobs.mkdir(parents=True)
            try:
                (jobs / 'link').symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable')
            self.assertEqual(summarize_run(jobs.parent)['totals']['jobs'], 0)

    def test_archived_failed_attempt_and_codex_usage_are_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / '2026-09-25'
            job = root / 'jobs' / 'F-20260925-research'
            saved(job / 'job.json', {'job_id': job.name, 'stage': 'research', 'model': 'gpt-6-astra'})
            saved(job / 'state.json', {'status': 'output_ready_for_smn_validation'})
            saved(job / 'receipt.json', {'billing_source': 'subscription', 'seconds': 12,
                                        'usage': {'input_tokens': 100, 'cached_input_tokens': 40,
                                                  'output_tokens': 20}})
            saved(job / 'turn-usage.json', [{'input_tokens': 100, 'cached_input_tokens': 40,
                                             'output_tokens': 20}])
            failed = job / 'failed-attempt-1'
            saved(failed / 'state.json', {'status': 'failed_needs_review'})
            saved(failed / 'turn-usage.json', {'usage': {'input_tokens': 2, 'output_tokens': 1},
                                              'modelUsage': {}, 'duration_ms': 1000})
            saved(root / 'dev-publication-receipt.json', {'status': 'live_verified'})
            report = summarize_run(root)
            self.assertEqual(report['status'], 'live_verified')
            self.assertEqual(report['job_status'], 'complete_with_failed_attempts')
            self.assertEqual(report['totals']['jobs'], 2)
            self.assertEqual(report['totals']['failed'], 1)
            self.assertEqual(report['totals']['input_tokens'], 102)
            self.assertEqual(report['totals']['cache_read_tokens'], 40)
            self.assertEqual(report['totals']['seconds'], 13)
            self.assertEqual(report['by_article']['F']['jobs'], 2)
            self.assertEqual(report['totals']['coverage']['reported_api_equivalent_usd']['reported_jobs'], 0)
            self.assertEqual(report['billing']['source'], 'subscription')

    def test_retry_suffix_and_daily_hold_without_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / '2026-09-25'
            job = root / 'jobs' / 'F-20260925-research-two'
            saved(job / 'job.json', {'job_id': job.name, 'stage': 'research-two', 'model': 'claude-sonnet-5'})
            saved(job / 'state.json', {'status': 'output_ready_for_smn_validation'})
            saved(root / 'smn-daily-state.json', {'articles': {'F': {'held': 'review'}}})
            report = summarize_run(root)
            self.assertEqual(report['status'], 'held')
            self.assertEqual(report['totals']['retries'], 1)

    def test_discovery_retry_id_and_safe_hold_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'comparisons' / '2026-09-26-claude'
            job = root / 'jobs' / 'F-20260925-primary-discovery-two'
            saved(job / 'job.json', {'job_id': job.name, 'stage': 'primary-discovery',
                                    'model': 'claude-sonnet-5'})
            saved(job / 'state.json', {'status': 'output_ready_for_smn_validation'})
            saved(root / 'run-metadata.json', {'status': 'hold'})
            saved(root / 'HOLD.json', {'reason': 'Quote failed; bearer secret-token; contact x@y.com'})
            report = summarize_run(root)
            self.assertEqual(report['totals']['retries'], 1)
            self.assertEqual(report['hold_reason'], 'Quote failed; [redacted] contact [redacted]')


if __name__ == '__main__':
    unittest.main()
