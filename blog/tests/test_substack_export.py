import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import promotion_jobs as jobs
import promotion_worker as worker
import substack_export as exporter
from public_derivative import prepare_derivative
from subscription_writer import load_json, save_json
from visual_evidence import digest
import test_public_derivative as derivatives


class ExportTests(unittest.TestCase):
    def setUp(self):
        fixture = derivatives.DerivativeTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root, self.prepared, self.copy = fixture.root, fixture.prepared, fixture.copy

    def relative_source(self):
        f = self.fixture
        identity = '/editions/2026-10-02/ADP/article.html'
        source = load_json(f.article_dir/'source.json')
        source['card']['production_original'] = identity
        card_hash = digest(source['card'])
        bundle = load_json(f.article_dir/'bundle.json')
        bundle['seasonal_contract']['card_sha256'] = card_hash
        bundle.pop('evidence_sha256')
        bundle['evidence_sha256'] = digest(bundle)
        save_json(f.article_dir/'source.json', source)
        save_json(f.article_dir/'bundle.json', bundle)
        binding = load_json(f.article_dir/'review-binding.json')
        binding['engine_card_sha256'] = card_hash
        save_json(f.article_dir/'review-binding.json', binding)
        for file in [f.article_dir/'mechanical-checks.json', f.job/'receipt.json', f.job/'job.json']:
            value = load_json(file)
            value['evidence_sha256'] = bundle['evidence_sha256']
            save_json(file, value)
        self.prepared = prepare_derivative(f.article_dir, f.job, identity, 'v1')
        return identity

    def queued(self, kind):
        p = self.prepared['provenance']
        return jobs.create(self.root, kind, {'article_id':p['article_id'], 'source_revision':p['revision'],
            'source_hash':p['article_sha256'], 'payload_sha256':digest(self.copy)}, 'editor')

    def test_relative_queued_worker_generates_once_without_version_conflict(self):
        identity = self.relative_source()
        before = copy.deepcopy(self.prepared)
        job = self.queued('substack_export')
        config = {'generation_enabled':True, 'public_origin':'https://smn-dev.trxstat.com',
                  'resolve_source':lambda inputs:{'prepared':self.prepared, 'copy':self.copy}}
        result = worker.run_one(self.root, job['id'], config)
        self.assertEqual((result['id'], result['status'], result['version']), (job['id'], 'generated', 2))
        text, _ = jobs.get_artifact(self.root, job['id'], 'substack-free.txt')
        self.assertIn('https://smn-dev.trxstat.com'+identity, text.read_text())
        self.assertNotIn('Company cut its delivery outlook.', text.read_text())
        self.assertEqual(self.prepared, before)
        self.assertEqual(load_json(jobs._path(self.root, job['id']))['inputs']['article_id'], identity)
        repeated = worker.run_one(self.root, job['id'], config)
        self.assertEqual(repeated, result)
        direct = exporter.export(self.root, self.prepared, self.copy, 'editor', job_id=job['id'],
                                 public_origin=config['public_origin'])
        self.assertEqual(direct, result)
        self.assertEqual(result['dispatch_status'], 'disabled')

    def test_social_fallback_is_public_hook_qualification_and_link(self):
        self.relative_source()
        self.copy['video'] = None
        job = self.queued('social_export')
        result = worker.run_one(self.root, job['id'], {'generation_enabled':True,
            'public_origin':'https://smn-dev.trxstat.com',
            'resolve_source':lambda inputs:{'prepared':self.prepared, 'copy':self.copy}})
        self.assertEqual(result['status'], 'generated')
        self.assertEqual({x['name'] for x in result['artifacts']}, {'social-public.txt', 'export.receipt.json'})
        text, _ = jobs.get_artifact(self.root, job['id'], 'social-public.txt')
        expected = '\n\n'.join([self.copy['preview'][0]['text'], self.copy['qualification']['text']])
        self.assertTrue(text.read_text().startswith(expected))
        self.assertNotIn(self.copy['headline']['text'], text.read_text())
        self.assertNotIn(self.copy['full_article_value']['text'], text.read_text())
        self.assertNotIn('Company cut its delivery outlook.', text.read_text())

    def test_social_uses_exact_approved_social_copy(self):
        self.copy['social'] = [dict(self.copy['preview'][0], text='Stock declines can help, but rebounds can hurt.')]
        job = self.queued('social_export')
        result = exporter.export(self.root, self.prepared, self.copy, 'editor', job_id=job['id'])
        text, _ = jobs.get_artifact(self.root, job['id'], 'social-public.txt')
        self.assertTrue(text.read_text().startswith(self.copy['social'][0]['text']+'\n\n'+self.copy['qualification']['text']))
        self.assertEqual(result['version'], 2)

    def test_absolute_substack_preserves_html_text_and_no_protected_body(self):
        result = exporter.export(self.root, self.prepared, self.copy, 'editor')
        self.assertEqual({x['name'] for x in result['artifacts']},
                         {'substack-free.txt', 'substack-free.html', 'export.receipt.json'})
        for name in ('substack-free.txt', 'substack-free.html'):
            file, _ = jobs.get_artifact(self.root, result['id'], name)
            self.assertIn(self.prepared['provenance']['article_id'], file.read_text())
            self.assertNotIn('Company cut its delivery outlook.', file.read_text())

    def test_unsafe_urls_and_origins_fail_closed(self):
        bad = ['//evil.test/articles/a.html', '/editions/../a.html', '/articles/a.html?next=evil',
               '/articles/%2f%2fevil.html', 'http://evil.test/a.html', 'https://user:pass@evil.test/a.html',
               'https://evil.test\\a.html', 'https://evil.test/a.html\n', 'javascript:alert(1)',
               'https://evil.test:invalid/a.html', 'https://@evil.test/a.html', 'https://evil.test:0/a.html']
        for identity in bad:
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                exporter.canonical_url(identity, 'https://smn-dev.trxstat.com')
        for origin in [None, 'http://smn-dev.trxstat.com', '//evil.test', 'https://user@evil.test',
                       'https://evil.test/path', 'https://evil.test?query', 'https://evil.test#fragment']:
            with self.subTest(origin=origin), patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
                exporter.canonical_url('/articles/a.html', origin)

    def test_explicit_origin_precedence_and_installer_configuration(self):
        identity = '/articles/a.html'
        with patch.dict(os.environ, {'SMN_PUBLIC_ORIGIN':'https://public.test', 'SMN_SITE_BASE':'https://fallback.test'}):
            self.assertEqual(exporter.canonical_url(identity), 'https://public.test'+identity)
            self.assertEqual(exporter.canonical_url(identity, 'https://explicit.test'), 'https://explicit.test'+identity)
        import install_smn_membership as installer
        self.assertIn('SMN_PUBLIC_ORIGIN=https://smn-dev.trxstat.com\n', installer.configuration())

    def test_changed_retained_input_is_rejected_before_artifacts(self):
        job = self.queued('substack_export')
        Path(next(iter(self.prepared['input_hashes']))).write_text('changed')
        with self.assertRaises(ValueError):
            exporter.export(self.root, self.prepared, self.copy, 'editor', job_id=job['id'])
        self.assertEqual(jobs.get_job(self.root, job['id'])['status'], 'draft')
        self.assertEqual(jobs.get_job(self.root, job['id'])['artifacts'], [])

    def test_worker_preserves_concurrent_job_conflict(self):
        job = self.queued('substack_export')
        with patch('substack_export.jobs.update', side_effect=jobs.Conflict('Changed concurrently')):
            with self.assertRaises(jobs.Conflict):
                worker.run_one(self.root, job['id'], {'generation_enabled':True,
                    'resolve_source':lambda inputs:{'prepared':self.prepared, 'copy':self.copy}})
        self.assertEqual(jobs.get_job(self.root, job['id'])['status'], 'draft')


if __name__ == '__main__':
    unittest.main()
