"""Private publication journal and source binding; run with real Linux posts_lock."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import article_index
import membership_publication as publication
from article_content_store import ContentError


class MembershipPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        public = root / 'public'; public.mkdir()
        self.env = patch.dict(os.environ, {'SMN_READER_PRIVATE_ROOT': str(root / 'private')})
        self.env.start(); self.addCleanup(self.env.stop)
        for name, value in {'NEWS_ROOT': public, 'POSTS_JSON': public / 'posts.json',
                            'LOCK_FILE': root / 'posts.lock', 'BACKUP_DIR': root / 'backups'}.items():
            active = patch.object(article_index, name, value); active.start(); self.addCleanup(active.stop)
        asset = public / 'editions/2026-10-03/test/chart.png'
        asset.parent.mkdir(parents=True); asset.write_bytes(b'EXACT_ENGINE_CHART')
        self.post = {'url': 'https://smn-dev.trxstat.com/editions/2026-10-03/test/article.html',
                     'production_original': 'https://seasonalmarketnews.com/articles/original.html',
                     'title': 'Historical study', 'dek': 'Original introduction.',
                     'hero_image': '/editions/2026-10-03/test/chart.png', 'path': str(asset.parent / 'article.html')}
        self.raw = '<article><section data-role="opening"><p>Exact source introduction.</p></section><p>PRIVATE_BODY 19.92% and 12.04%; order unknown.</p><img src="chart.png"></article>'
        publication.store().set_enabled(True)

    def publish(self):
        updated, manifest = publication.prepare(self.post, self.raw)
        with article_index.posts_lock():
            publication.commit_post([], None, updated, manifest, 'initial')
        return updated, manifest

    def test_prepare_private_until_activated_preserves_canonical_and_exact_source(self):
        updated, manifest = publication.prepare(self.post, self.raw)
        self.assertEqual(updated['url'], self.post['url'])
        self.assertEqual(manifest['preview']['provenance']['source_original_url'], self.post['production_original'])
        self.assertEqual(updated['dek'], 'Exact source introduction.')
        self.assertNotIn('PRIVATE_BODY', str(manifest['preview']))
        with self.assertRaises(ContentError): publication.source(updated)
        _, repeated = publication.prepare(self.post, self.raw)
        self.assertEqual(repeated, manifest)

    def test_catalog_failure_recovers_exact_revision_and_keeps_unrelated_post(self):
        old, _ = self.publish()
        other = dict(self.post, url='https://smn-dev.trxstat.com/articles/other.html', title='Unrelated')
        article_index.save_posts([old, other])
        new, manifest = publication.prepare(old, self.raw.replace('Exact source', 'New exact source'), previous=publication.store().resolve(publication.path_for(old)))
        with article_index.posts_lock():
            with patch.object(article_index, 'save_posts', side_effect=OSError('interrupted catalog')):
                with self.assertRaises(OSError): publication.commit_post([old, other], old, new, manifest, 'edit')
            with self.assertRaises(ContentError): publication.source(old)
            self.assertEqual(len(list((publication.store().root / 'publishing').glob('*.json'))), 1)
            publication.recover(); publication.recover()
        self.assertEqual(article_index.load_posts(), [new, other])
        self.assertEqual(publication.source(new)[1]['revision'], manifest['revision'])
        self.assertEqual(list((publication.store().root / 'publishing').glob('*.json')), [])

    def test_recovery_rejects_newer_catalog_change(self):
        old, _ = self.publish()
        new, manifest = publication.prepare(old, self.raw.replace('Exact source', 'New source'), previous=publication.store().resolve(publication.path_for(old)))
        with article_index.posts_lock():
            with patch.object(article_index, 'save_posts', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError): publication.commit_post([old], old, new, manifest, 'conflict')
            changed = dict(old, title='Another editor update')
            article_index.save_posts([changed])
            with self.assertRaises(ContentError): publication.recover()
        self.assertEqual(article_index.load_posts(), [changed])
        self.assertTrue(list((publication.store().root / 'publishing').glob('*.json')))

    def test_completed_catalog_with_leftover_marker_replays_idempotently(self):
        updated, manifest = publication.prepare(self.post, self.raw)
        original_unlink = Path.unlink
        def interrupt_marker(path, *args, **kwargs):
            if path.parent.name == 'publishing': raise OSError('crash after catalog')
            return original_unlink(path, *args, **kwargs)
        with article_index.posts_lock():
            with patch.object(Path, 'unlink', interrupt_marker):
                with self.assertRaises(OSError): publication.commit_post([], None, updated, manifest, 'lost-response')
            publication.recover()
        self.assertEqual(article_index.load_posts(), [updated])

    def test_edit_preserves_engine_bytes_and_rejects_stale_asset_revision(self):
        old, previous = self.publish()
        body, _ = publication.source(old)
        stale = body.replace(previous['revision'], 'r-stale')
        with self.assertRaises(ContentError): publication.prepare(old, stale, previous=previous)
        with article_index.posts_lock():
            new = publication.publish_edited([old], old, dict(old, title='Edited title'), body.replace('Exact source introduction.', 'Changed source introduction.'), 'admin')
        rendered, manifest = publication.source(new)
        self.assertIn('19.92% and 12.04%; order unknown.', rendered)
        self.assertEqual(manifest['approval']['scope'], 'exact_source_excerpt')
        self.assertEqual(manifest['preview']['content']['preview'][0]['text'], 'Changed source introduction.')
        self.assertIn(manifest['revision'], rendered)
        self.assertNotIn(previous['revision'], rendered)
        name = next(iter(manifest['assets']))
        self.assertEqual(publication.store().private_asset_path(publication.path_for(new), manifest['revision'], name).read_bytes(), b'EXACT_ENGINE_CHART')


if __name__ == '__main__': unittest.main()
