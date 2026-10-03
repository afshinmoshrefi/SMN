import copy
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from article_content_store import ContentError
import membership_runtime as runtime
import promotion_jobs
from visual_evidence import digest


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        active = patch.object(runtime.publication, 'store', return_value=SimpleNamespace(root=self.root))
        active.start(); self.addCleanup(active.stop)
        self.prepared = {'provenance': {'article_id': 'https://smn-dev.trxstat.com/articles/test.html',
            'revision': 'source-r1', 'article_sha256': 'a' * 64}}

    def test_first_derivative_needs_prepared_source_only(self):
        with patch.object(runtime, '_source_record', return_value={'prepared': self.prepared}):
            job = runtime.create_job({'kind': 'derivative', 'slug': 'test'}, 'editor')
        saved = json.loads(promotion_jobs._path(runtime.jobs_root(), job['id']).read_text())
        self.assertNotIn('slug', saved['inputs'])
        self.assertEqual(saved['inputs']['payload_sha256'], digest(self.prepared))
        self.assertEqual(saved['inputs']['article_id'], self.prepared['provenance']['article_id'])

    def test_video_requires_approved_copy(self):
        with patch.object(runtime, '_source_record', return_value={'prepared': self.prepared}):
            with self.assertRaisesRegex(ContentError, 'approve'):
                runtime.create_job({'kind': 'article_video', 'slug': 'test'}, 'editor')

    def test_daily_source_bundle_and_avatar_are_distinct(self):
        folder = self.root / 'briefings' / '2026-10-03'; folder.mkdir(parents=True)
        source = {'edition_date': '2026-10-03', 'sources': []}
        (folder / 'sources.json').write_text(json.dumps(source))
        job = runtime.create_job({'kind': 'daily_briefing', 'briefing_id': folder.name}, 'editor')
        self.assertEqual(job['source_hash'], digest(source))
        self.assertEqual(runtime.list_briefings()[0]['kind'], 'source_bundle')
        with self.assertRaises(ContentError):
            runtime.create_job({'kind': 'daily_avatar', 'briefing_id': folder.name}, 'editor')
        with self.assertRaises(ContentError): runtime._briefing_record('../outside')

    def test_daily_resolver_rejects_changed_revision(self):
        with patch.object(runtime, '_briefing_record', return_value={'active_revision': 'new'}):
            with self.assertRaisesRegex(ContentError, 'changed'):
                runtime._resolve_source({'briefing_id': 'today', 'source_revision': 'old'})

    def test_preview_edit_cannot_replace_evidence_or_media(self):
        statement = lambda text: {'text': text, 'source_ids': ['source-1'], 'article_refs': ['opening/0']}
        original = {'headline': statement('Title'), 'preview': [statement('Lead')],
                    'full_article_value': statement('Value'), 'qualification': statement('Limit'),
                    'social': [], 'video': None}
        current = {'revision': 'r1', 'active_revision': 'r1',
                   'preview': {'content': original, 'provenance': {'revision': 'r1'}}}
        content = copy.deepcopy(original)
        content['headline'].update(text='New title', source_ids=['forged'], article_refs=['forged'])
        content['video'] = {'narration': 'unreviewed'}
        with patch.object(runtime.article_index, 'posts_lock', side_effect=nullcontext), \
                patch.object(runtime.publication, 'recover'), \
                patch.object(runtime, 'get_preview', return_value=current):
            runtime.save_preview('test', {'expected_revision': 'r1', 'content': content}, 'editor')
        saved = json.loads(runtime._draft_file('test').read_text())['preview']['content']
        self.assertEqual(saved['headline']['source_ids'], ['source-1'])
        self.assertEqual(saved['headline']['text'], 'New title')
        self.assertIsNone(saved['video'])

    def test_import_rejects_unfinished_job(self):
        with patch.object(runtime, '_source_record', return_value={'prepared': self.prepared}):
            job = runtime.create_job({'kind': 'derivative', 'slug': 'test'}, 'editor')
        with self.assertRaisesRegex(ContentError, 'generated'):
            runtime._import_job(job['id'], job['version'], 'editor')

    def test_controls_have_no_implicit_provider_enable(self):
        with patch.object(runtime, '_configuration', return_value={}):
            result = runtime.set_controls({'scope': 'all', 'paused': True}, 'editor')
        self.assertTrue(result['all'])
        self.assertFalse(result['providers']['ElevenLabs']['enabled'])
        with self.assertRaises(ValueError):
            runtime.set_controls({'scope': 'all', 'paused': 'false'}, 'editor')

    def test_generate_rejects_stale_preview(self):
        with patch.object(runtime, 'get_preview', return_value={'revision': 'new'}):
            with self.assertRaisesRegex(ContentError, 'changed'):
                runtime.generate_preview('test', {'expected_revision': 'old'}, 'editor')


if __name__ == '__main__':
    unittest.main()
