import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_subscription_writer as cw
from subscription_edition import verify_job as dispatch_verify

SCHEMA = {'type': 'object', 'properties': {'title': {'type': 'string'}},
          'required': ['title'], 'additionalProperties': False}


def fake_cli(directory, *, auth='claude.ai', models=('claude-opus-5-5',), output=None, error=False):
    """A stand-in `claude` executable: answers auth status, --version and one -p turn."""
    usage = {m: {'inputTokens': 1, 'outputTokens': 2, 'costUSD': 0.1} for m in models}
    result = {'is_error': error, 'result': 'boom' if error else '', 'modelUsage': usage,
              'structured_output': output if output is not None else {'title': 'T'},
              'usage': {}, 'num_turns': 2, 'duration_ms': 5}
    status = {'loggedIn': True, 'authMethod': auth, 'apiProvider': 'firstParty',
              'subscriptionType': 'pro', 'email': 'hidden@example.com'}
    path = Path(directory) / 'claude'
    path.write_text('#!/usr/bin/env python3\nimport sys,json,os\n'
                    'a=sys.argv[1:]\n'
                    'open(os.path.join(os.path.dirname(__file__),"env.json"),"w").write(json.dumps(sorted(os.environ)))\n'
                    f'if a[:2]==["auth","status"]: print(json.dumps({status!r}))\n'
                    'elif a==["--version"]: print("9.9.9 (Claude Code)")\n'
                    f'else:\n sys.stdin.read(); print(json.dumps({result!r})); sys.exit({1 if error else 0})\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class ClaudeWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        until = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self.job = cw.prepare_job(self.dir / 'jobs', 'ABC-20260925-write', 'prompt', SCHEMA,
                                  as_of='2026-09-25', valid_until=until, evidence_sha256='e' * 64)

    def tearDown(self):
        self.tmp.cleanup()

    def test_manifest_records_exact_model_and_effort(self):
        m = json.loads((self.job / 'job.json').read_text())
        self.assertEqual((m['provider'], m['model'], m['effort']), ('anthropic', 'claude-opus-5-5', 'medium'))
        self.assertEqual(dispatch_verify(self.job)['model'], 'claude-opus-5-5')

    def test_only_discovery_can_enable_bounded_web_tools(self):
        until = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        with self.assertRaisesRegex(ValueError, 'primary-source discovery'):
            cw.prepare_job(self.dir/'jobs', 'bad-web', 'p', SCHEMA, as_of='2026-09-25',
                           valid_until=until, evidence_sha256='e'*64, web_search=True)
        job = cw.prepare_job(self.dir/'jobs', 'discover', 'p', SCHEMA, as_of='2026-09-25',
                             valid_until=until, evidence_sha256='e'*64, stage='primary-discovery',
                             model=cw.REVIEW_MODEL, effort='low', web_search=True)
        cmd = cw.command('claude', job, cw.verify_job(job))
        self.assertEqual(cmd[cmd.index('--tools')+1], 'WebSearch,WebFetch')
        self.assertEqual(cmd[cmd.index('--max-turns')+1], '8')
        self.assertEqual(cw.command('claude', self.job, cw.verify_job(self.job))[
            cw.command('claude', self.job, cw.verify_job(self.job)).index('--tools')+1], '')

    def test_discovery_records_web_calls_but_not_structured_output(self):
        def event(name):
            return json.dumps({'message': {'content': [{'type': 'tool_use', 'name': name, 'id': 'test'}]}})
        self.assertEqual(cw.discovery_tools(event('WebSearch')+'\n'+event('StructuredOutput')),
                         [{'name': 'WebSearch', 'id': 'test'}])
        with self.assertRaisesRegex(RuntimeError, 'Unexpected discovery tool'):
            cw.discovery_tools(event('Bash'))
        with self.assertRaisesRegex(RuntimeError, 'without web evidence'):
            cw.discovery_tools(event('StructuredOutput'))

    def test_subscription_receipt_without_api_fallback_or_secrets(self):
        os.environ['ANTHROPIC_API_KEY'] = 'sk-test'
        try:
            receipt = cw.run_job(self.job, fake_cli(self.dir))
        finally:
            del os.environ['ANTHROPIC_API_KEY']
        self.assertEqual(receipt['billing_source'], 'subscription')
        self.assertIs(receipt['api_fallback'], False)
        self.assertEqual(receipt['model_used'], ['claude-opus-5-5'])
        self.assertNotIn('ANTHROPIC_API_KEY', json.loads((self.dir / 'env.json').read_text()))
        text = ''.join(p.read_text() for p in self.job.iterdir() if p.suffix == '.json')
        self.assertNotIn('hidden@example.com', text)
        self.assertEqual(cw.run_job(self.job, fake_cli(self.dir)), receipt)  # immutable reuse

    def test_api_key_login_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'subscription login is required'):
            cw.run_job(self.job, fake_cli(self.dir, auth='api_key'))
        self.assertEqual(json.loads((self.job / 'state.json').read_text())['status'], 'failed_needs_review')

    def test_model_substitution_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'no model substitution'):
            cw.run_job(self.job, fake_cli(self.dir, models=('claude-opus-5-5', 'claude-sonnet-5')))
        self.assertFalse((self.job / 'receipt.json').exists())

    def test_schema_violation_is_refused(self):
        with self.assertRaises(ValueError):
            cw.run_job(self.job, fake_cli(self.dir, output={'title': 'T', 'extra': 1}))
        self.assertFalse((self.job / 'receipt.json').exists())

    def test_changed_prompt_is_refused(self):
        (self.job / 'prompt.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed after ready'):
            cw.run_job(self.job, fake_cli(self.dir))


class GenerationProvenanceTests(unittest.TestCase):
    def test_summary_binds_writer_and_reviewer_receipts(self):
        from engine_edition_workflow import Edition
        from visual_evidence import digest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'sources.json').write_text('{}')
            ed = Edition(root, '2026-09-25', provider='claude', claude=fake_cli(root))
            until = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
            for stage in ('write', 'review'):
                job = cw.prepare_job(root / 'jobs', 'ABC-20260925-' + stage, 'p', SCHEMA, as_of='x',
                                     valid_until=until, evidence_sha256='e' * 64, stage=stage)
                cw.run_job(job, fake_cli(root))
            article = {'title': 'T'}
            g = ed.generation('ABC', 'review', article)
            self.assertEqual(g['article_sha256'], digest(article))
            self.assertEqual(g['summary'], {'provider': 'anthropic', 'model': 'claude-opus-5-5',
                'effort': 'medium', 'billing_source': 'subscription', 'api_fallback': False,
                'reviewer': {'provider': 'anthropic', 'model': 'claude-opus-5-5', 'effort': 'medium',
                             'billing_source': 'subscription'}})
            (root / 'jobs' / 'ABC-20260925-write' / 'output.json').write_text('{"title":"X"}')
            with self.assertRaisesRegex(ValueError, 'Receipt output changed'):
                ed.generation('ABC', 'review', article)


class StageModelTests(unittest.TestCase):
    def test_only_writing_uses_the_high_end_model(self):
        from engine_edition_workflow import Edition
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'sources.json').write_text('{}')
            ed = Edition(tmp, '2026-09-25', provider='claude')
            for stage in ('write', 'repair', 'repair-two'):
                self.assertEqual(ed._job_options(stage), {'model': 'claude-opus-5-5', 'effort': 'medium'})
            for stage in ('review', 'rereview'):
                self.assertEqual(ed._job_options(stage), {'model': 'claude-sonnet-5', 'effort': 'low'})
            self.assertEqual(Edition(tmp, '2026-09-25')._job_options('review'), {'effort': 'xhigh'})


if __name__ == '__main__':
    unittest.main()
