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
                            'LOCK_FILE': root / 'posts.lock', 'BACKUP_DIR': root / 'backups',
                            'UNPUBLISHED_JSON': root / 'held.json'}.items():
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

    def test_open_by_default_and_members_lock_binds_revision(self):
        _, default = publication.prepare(self.post, self.raw)
        _, opened = publication.prepare(dict(self.post, access='open'), self.raw)
        self.assertEqual(opened['revision'], default['revision'])
        self.assertNotIn('access', default)
        with self.assertRaises(ContentError): publication.prepare(dict(self.post, access='free'), self.raw)
        # Locking a published article re-reads its assets from the prior revision.
        old, _ = self.publish()
        raw, previous = publication.source(old)
        _, locked = publication.prepare(dict(old, access='members'), raw, previous=previous)
        self.assertEqual(locked['access'], 'members')
        self.assertNotEqual(locked['revision'], previous['revision'])
        self.assertEqual({a['sha256'] for a in locked['assets'].values()},
                         {a['sha256'] for a in previous['assets'].values()})

    def test_native_cache_version_is_replaced_with_private_hash_bound_url(self):
        version = publication.sha(b'EXACT_ENGINE_CHART')[:16]
        updated, manifest = publication.prepare(self.post, self.raw.replace('src="chart.png"', f'src="chart.png?v={version}"'))
        self.assertTrue(manifest['assets'])
        self.assertNotIn('?v=', publication.store()._private_file(manifest, 'full.html').read_text())
        with self.assertRaises(ContentError):
            publication.prepare(self.post, self.raw.replace('src="chart.png"', 'src="chart.png?download=../../outside"'))

    def test_catalog_failure_recovers_exact_revision_and_keeps_unrelated_post(self):
        old, _ = self.publish()
        other = dict(self.post, url='https://smn-dev.trxstat.com/articles/other.html', title='Unrelated')
        article_index.save_posts([old, other])
        new, manifest = publication.prepare(old, self.raw.replace('Exact source', 'New exact source'), previous=publication.store().resolve(publication.path_for(old)))
        with article_index.posts_lock():
            with patch.object(article_index, 'save_posts', side_effect=OSError('interrupted catalog')):
                with self.assertRaises(OSError): publication.commit_post([old, other], old, new, manifest, 'edit')
            self.assertEqual(publication.source(old)[1]['revision'], old['membership_revision'])
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

    def test_held_creation_catalog_and_pointer_failure_resume_without_early_access(self):
        updated, _ = publication.prepare(self.post, self.raw)
        record = {'post': updated, 'held_path': updated['path']}
        with article_index.posts_lock():
            with patch.object(article_index, 'save_unpublished', side_effect=OSError('held write')):
                with self.assertRaises(OSError): publication.commit_state(None, None, 'test', None, record)
            with self.assertRaises(ContentError): publication.source(updated)
            publication.recover()
            for boundary in ('save_posts', 'save_unpublished', 'activate_revision'):
                module = publication.ContentStore if boundary == 'activate_revision' else article_index
                with patch.object(module, boundary, side_effect=OSError(boundary)):
                    with self.assertRaises(OSError): publication.commit_state(None, updated, 'test', record, None)
                with self.assertRaises(ContentError): publication.source(updated)
                publication.recover()
                self.assertEqual(article_index.load_posts(), [updated])
                self.assertNotIn('test', article_index.load_unpublished())
                publication.commit_state(updated, None, 'test', None, record)

    def test_withdrawal_and_delete_boundaries_resume_preserving_peer_held_record(self):
        updated, _ = self.publish()
        record = {'post': updated, 'held_path': updated['path']}
        peer = {'post': {'title': 'Peer held row'}}
        article_index.save_unpublished({'peer': peer})
        with article_index.posts_lock():
            for boundary in ('withdraw', 'save_posts', 'save_unpublished'):
                module = publication.ContentStore if boundary == 'withdraw' else article_index
                with patch.object(module, boundary, side_effect=OSError(boundary)):
                    with self.assertRaises(OSError): publication.commit_state(updated, None, 'test', None, record)
                if boundary != 'withdraw':
                    with self.assertRaises(ContentError): publication.source(updated)
                publication.recover()
                self.assertEqual(article_index.load_posts(), [])
                self.assertEqual(article_index.load_unpublished(), {'peer': peer, 'test': record})
                publication.commit_state(None, updated, 'test', record, None)
            with patch.object(article_index, 'save_posts', side_effect=OSError('delete catalog')):
                with self.assertRaises(OSError): publication.commit_state(updated, None, 'test', None, None)
            with self.assertRaises(ContentError): publication.source(updated)
            publication.recover()
            publication.commit_state(None, None, 'test', None, record)
            with patch.object(article_index, 'save_unpublished', side_effect=OSError('held delete')):
                with self.assertRaises(OSError): publication.commit_state(None, None, 'test', record, None)
            publication.recover()
        self.assertEqual(article_index.load_posts(), [])
        self.assertEqual(article_index.load_unpublished(), {'peer': peer})

    def test_recovery_rejects_changed_held_record_and_private_pointer(self):
        updated, _ = publication.prepare(self.post, self.raw)
        record = {'post': updated, 'held_path': updated['path']}
        with article_index.posts_lock():
            with patch.object(article_index, 'save_unpublished', side_effect=OSError('held write')):
                with self.assertRaises(OSError): publication.commit_state(None, None, 'test', None, record)
            changed = dict(record, reason='Newer administrator action')
            article_index.save_unpublished({'test': changed})
            with self.assertRaises(ContentError): publication.recover()
            self.assertEqual(article_index.load_unpublished()['test'], changed)
            article_index.save_unpublished({})
            publication.store().activate_revision(publication.path_for(updated), updated['membership_revision'], 'external')
            with self.assertRaises(ContentError): publication.recover()

    def test_completed_transaction_receipt_cannot_rebind_or_reactivate_after_withdrawal(self):
        updated, _ = publication.prepare(self.post, self.raw)
        with article_index.posts_lock():
            publication.commit_state(None, updated, transaction_id='exact-create')
            publication.commit_state(updated, None, transaction_id='withdraw-later')
            publication.commit_state(None, updated, transaction_id='exact-create')
            with self.assertRaises(ContentError):
                publication.commit_state(None, dict(updated, title='Rebound'), transaction_id='exact-create')
        self.assertEqual(article_index.load_posts(), [])
        with self.assertRaises(ContentError): publication.source(updated)


if __name__ == '__main__': unittest.main()
