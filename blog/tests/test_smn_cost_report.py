import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smn_cost_report import discover_runs, summarize_run


def saved(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class CostReportTests(unittest.TestCase):
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
            saved(job / 'receipt.json', {'billing_source': 'subscription'})
            saved(job / 'turn-usage.json', {'modelUsage': {
                'claude-sonnet-5': {'inputTokens': 10, 'outputTokens': 2, 'costUSD': 0.1},
                'claude-haiku-4-5': {'inputTokens': 3, 'outputTokens': 1, 'costUSD': 0.02}}})
            report = summarize_run(run)
            self.assertEqual(report['totals']['input_tokens'], 13)
            self.assertEqual(report['totals']['reported_api_equivalent_usd'], 0.12)
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
            report = summarize_run(root)
            self.assertEqual(report['status'], 'incomplete')
            self.assertEqual(report['totals']['reported_api_equivalent_usd'], 0.03)
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


if __name__ == '__main__':
    unittest.main()
