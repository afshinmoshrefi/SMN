"""Protected dashboard/editor operations against isolated real Linux stores."""
from contextlib import contextmanager
import os
from unittest.mock import patch

from test_pub_dashboard import DashboardFixture
import article_editor as editor
import article_index
import membership_publication as publication
import pub_dashboard


class ProtectedTransactionTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        active = patch.dict(os.environ, {'SMN_READER_PRIVATE_ROOT': str(self.state / 'reader')})
        active.start(); self.addCleanup(active.stop)
        active = patch.dict(pub_dashboard.app.config, {'TESTING': True})
        active.start(); self.addCleanup(active.stop)
        posts = article_index.load_posts()
        old = posts[0]
        updated = dict(old, url='https://smn-dev.trxstat.com/articles/a0.html')
        asset = self.news / 'articles' / 'chart.png'
        asset.parent.mkdir(); asset.write_bytes(b'EXACT_ENGINE_CHART')
        raw = '<article><section data-role="opening"><p>Original opening word.</p></section><p>Historical result 19.92%; order unknown.</p><img src="/articles/chart.png"></article>'
        updated, manifest = publication.prepare(updated, raw)
        publication.store().set_enabled(True)
        # The fixture's former external origin is removed before migration.
        article_index.save_posts(posts[1:])
        with article_index.posts_lock(): publication.commit_post(posts[1:], None, updated, manifest)
        self.post = updated

    def changed_draft(self):
        response = self.client.post('/api/articles/a0/editor')
        self.assertEqual(response.status_code, 200, response.get_json())
        ident = response.get_json()['data']['id']
        with editor.database() as db:
            draft = editor.read(db, ident, 'test-owner')
            revision = dict(editor.current(draft), version=1,
                            html=editor.current(draft)['html'].replace('opening word', 'opening clear'),
                            title='Edited historical study')
            draft['revisions'].append(revision); draft['version'] = 1
            editor.save(db, draft)
        return draft

    def test_editor_database_commit_failure_recovers_exact_receipt_and_retry(self):
        draft = self.changed_draft()
        actual_database = editor.database
        class FailCommit:
            def __init__(self, db): self.db = db
            def __getattr__(self, name): return getattr(self.db, name)
            def commit(self): raise OSError('editor DB commit interrupted')
        @contextmanager
        def failed_database():
            with actual_database() as db: yield FailCommit(db)
        with patch.object(editor, 'database', failed_database):
            with self.assertRaises(OSError):
                self.client.post('/api/editor/' + draft['id'] + '/publish', json={'version': 1})
        self.assertEqual(editor.get(draft['id'], 'test-owner')['status'], 'idle')
        self.assertTrue(list((publication.store().root / 'publishing').glob('*.json')))
        response = self.client.post('/api/editor/' + draft['id'] + '/publish', json={'version': 1})
        self.assertEqual(response.status_code, 200, response.get_json())
        stored = editor.get(draft['id'], 'test-owner')
        published = next(p for p in article_index.load_posts() if p['slug'] == 'a0')
        raw, manifest = publication.source(published)
        self.assertEqual(stored['published_revision'], manifest['revision'])
        self.assertEqual(stored['published_fingerprint'], editor.fingerprint(published, raw))
        self.assertNotEqual(stored['published_fingerprint'], editor.fingerprint(published, editor.current(stored)['html']))
        self.assertIn('19.92%; order unknown.', raw)
        self.assertEqual(list((publication.store().root / 'publishing').glob('*.json')), [])

    def test_editor_recovery_refuses_changed_draft_version(self):
        draft = self.changed_draft()
        with patch.object(editor, 'save', side_effect=OSError('editor receipt save interrupted')):
            with self.assertRaises(OSError):
                self.client.post('/api/editor/' + draft['id'] + '/publish', json={'version': 1})
        with editor.database() as db:
            changed = editor.read(db, draft['id'], 'test-owner')
            changed['version'] = 2; editor.save(db, changed)
        with article_index.posts_lock():
            with self.assertRaises(publication.ContentError): publication.recover()
        self.assertEqual(editor.get(draft['id'], 'test-owner')['version'], 2)

    def test_dashboard_unpublish_publish_and_delete_failures_resume(self):
        with patch.object(article_index, 'save_unpublished', side_effect=OSError('held save')):
            with self.assertRaises(OSError): pub_dashboard.do_unpublish('a0', 'test')
        with self.assertRaises(publication.ContentError): publication.source(self.post)
        self.assertTrue(pub_dashboard.do_unpublish('a0', 'test')['already_unpublished'])
        record = article_index.load_unpublished()['a0']
        with patch.object(article_index, 'save_posts', side_effect=OSError('republish catalog')):
            with self.assertRaises(OSError): pub_dashboard.do_publish('a0', 'test')
        with self.assertRaises(publication.ContentError): publication.source(record['post'])
        self.assertTrue(pub_dashboard.do_publish('a0', 'test')['already_published'])
        self.assertNotIn('a0', article_index.load_unpublished())
        with patch.object(article_index, 'save_posts', side_effect=OSError('delete catalog')):
            with self.assertRaises(OSError): self.client.delete('/api/articles/a0?rebuild=0')
        with self.assertRaises(publication.ContentError): publication.source(self.post)
        with article_index.posts_lock(): publication.recover()
        self.assertFalse(any(p['slug'] == 'a0' for p in article_index.load_posts()))

    def test_unauthenticated_request_never_recovers_or_mutates(self):
        with patch.dict(os.environ, {'SMN_DASHBOARD_AUTH': 'on'}), \
                patch.object(pub_dashboard.dashboard_auth, 'check_bearer', return_value=None), \
                patch.object(pub_dashboard, '_session_identity', return_value=None), \
                patch.object(publication, 'recover') as recover:
            self.assertEqual(self.client.get('/api/articles').status_code, 401)
            self.assertEqual(self.client.delete('/api/articles/a0').status_code, 401)
        recover.assert_not_called()

    def test_created_held_revision_is_private_and_tampered_admin_read_is_rejected(self):
        payload = {'symbol': 'TEST', 'title': 'A held study', 'slug': 'held-test', 'publish': 'false',
                   'html': '<article><section data-role="opening"><p>A qualified opening.</p></section><p>PRIVATE_FULL_BODY</p></article>'}
        with patch.object(pub_dashboard, 'site_base', return_value='https://smn-dev.trxstat.com'):
            response = self.client.post('/api/articles', json=payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        post = article_index.load_unpublished()['held-test']['post']
        self.assertFalse(any(p['slug'] == 'held-test' for p in article_index.load_posts()))
        with self.assertRaises(publication.ContentError): publication.source(post)
        self.assertIn('PRIVATE_FULL_BODY', publication.stored_source(post)[0])
        from pathlib import Path
        Path(post['path']).write_text('Tampered private bytes')
        self.assertEqual(self.client.get('/api/articles/held-test?include=html').status_code, 409)
