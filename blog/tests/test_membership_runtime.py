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
import promotion_daemon
from visual_evidence import digest


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        active = patch.object(runtime.publication, 'store', return_value=SimpleNamespace(root=self.root))
        active.start(); self.addCleanup(active.stop)
        self.prepared = {'provenance': {'article_id': 'https://smn-dev.trxstat.com/articles/test.html',
            'revision': 'source-r1', 'article_sha256': 'a' * 64}}

    def test_export_origin_is_explicit_server_configuration(self):
        config = self.root/'promotion.json'
        config.write_text(json.dumps({'public_origin':'https://configuration.test'}))
        with patch.dict('os.environ', {'SMN_PROMOTION_CONFIG':str(config),
                'SMN_PUBLIC_ORIGIN':'https://smn-dev.trxstat.com','SMN_SITE_BASE':'https://fallback.test'}):
            self.assertEqual(runtime._configuration()['public_origin'], 'https://smn-dev.trxstat.com')

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

    def generated(self, kind, inputs, name, output):
        job = promotion_jobs.create(runtime.jobs_root(), kind, inputs, 'editor')
        folder = promotion_jobs._folder(runtime.jobs_root()) / 'artifacts' / job['id']
        folder.mkdir(parents=True)
        path = folder / name; path.write_text(json.dumps(output))
        promotion_jobs.update(runtime.jobs_root(), job['id'], job['version'], status='generated',
            artifacts=[{'name':name,'relative_path':name,'media_type':'application/json',
                        'sha256':runtime.publication.sha(path.read_bytes())}])
        return json.loads(promotion_jobs._path(runtime.jobs_root(), job['id']).read_text())

    def test_daily_import_keeps_original_bundle_and_approves_exact_persisted_draft(self):
        folder = self.root / 'briefings' / '2026-10-03'; folder.mkdir(parents=True)
        bundle = {'edition_date':'2026-10-03','sources':[]}
        (folder / 'sources.json').write_text(json.dumps(bundle))
        briefing = {'edition_date':'2026-10-03','title':'Draft for testing binding','script':[]}
        inputs = {'briefing_id':folder.name,'source_revision':digest(bundle),'source_hash':digest(bundle),'payload_sha256':digest(bundle)}
        job = self.generated('daily_briefing',inputs,'output.json',briefing)
        with patch('daily_briefing.inspect',return_value={'issues':[]}):
            runtime._action_job(job['id'],'import',{'expected_version':job['version']},'editor')
            self.assertIn('source_bundle',runtime._resolve_source(inputs))
            imported = runtime.get_job(job['id'])
            self.assertEqual(imported['imported_draft']['review_status'],'pending')
            reviewed = runtime._action_job(job['id'],'review',{'expected_version':imported['version'],
                'data':{'decision':'approved','payload_sha256':digest(inputs)}},'editor')
        self.assertEqual(reviewed['imported_draft']['review_status'],'approved')
        review = json.loads((folder / 'review.json').read_text())
        self.assertEqual(review['briefing_sha256'],digest(briefing))
        self.assertEqual(runtime._briefing_record(folder.name)['review']['status'],'approved')
        (folder / 'sources.json').write_text(json.dumps(dict(bundle,title='Changed')))
        with self.assertRaises(ContentError): runtime._resolve_source(inputs)
        with self.assertRaises(ContentError): runtime._briefing_record(folder.name)

    def test_daily_review_requires_import_and_no_status_changes_on_stale_draft(self):
        folder = self.root / 'briefings' / '2026-10-03'; folder.mkdir(parents=True)
        bundle = {'sources':[]}; (folder / 'sources.json').write_text(json.dumps(bundle))
        inputs = {'briefing_id':folder.name,'source_revision':digest(bundle),'source_hash':digest(bundle)}
        job = self.generated('daily_briefing',inputs,'output.json',{'script':[]})
        with self.assertRaises(ContentError):
            runtime._action_job(job['id'],'review',{'expected_version':job['version'],
                'data':{'decision':'approved','payload_sha256':digest(inputs)}},'editor')
        self.assertEqual(runtime.get_job(job['id'])['status'],'generated')
        with patch('daily_briefing.inspect',return_value={'issues':[]}):
            runtime._action_job(job['id'],'import',{'expected_version':job['version']},'editor')
        (folder/'briefing.json').write_text(json.dumps({'script':[],'title':'Changed after import'}))
        current = runtime.get_job(job['id'])
        with self.assertRaises(ContentError):
            runtime._action_job(job['id'],'review',{'expected_version':current['version'],
                'data':{'decision':'approved','payload_sha256':digest(inputs)}},'editor')
        self.assertEqual(runtime.get_job(job['id'])['status'],'generated')
        self.assertEqual(json.loads((folder/'review.json').read_text())['status'],'pending')

    def test_interrupted_daily_import_recovers_exact_files_and_job_version(self):
        folder = self.root/'briefings'/'2026-10-03';folder.mkdir(parents=True)
        bundle = {'edition_date':'2026-10-03','sources':[]};(folder/'sources.json').write_text(json.dumps(bundle))
        inputs = {'briefing_id':folder.name,'source_revision':digest(bundle),'source_hash':digest(bundle)}
        briefing = {'edition_date':'2026-10-03','script':[]}
        job = self.generated('daily_briefing',inputs,'output.json',briefing)
        write = runtime.publication._write
        def fail_review(path,value):
            if path.name == 'review.json': raise OSError('daily review receipt interrupted')
            return write(path,value)
        with patch('daily_briefing.inspect',return_value={'issues':[]}),patch.object(runtime.publication,'_write',side_effect=fail_review):
            with self.assertRaises(OSError): runtime._action_job(job['id'],'import',{'expected_version':job['version']},'editor')
        self.assertEqual(runtime.get_job(job['id'])['version'],job['version'])
        self.assertEqual(runtime._briefing_record(folder.name)['review'],{})
        runtime.recover_operations()
        imported = runtime.get_job(job['id'])
        self.assertEqual(imported['version'],job['version']+1)
        self.assertEqual(imported['imported_draft']['review_status'],'pending')
        self.assertEqual(json.loads((folder/'review.json').read_text())['briefing_sha256'],digest(briefing))

    def test_native_chart_review_failure_does_not_mark_job_approved(self):
        chart = self.root / 'chart.png'; chart.write_bytes(b'changed')
        copy = {'video':{'native_chart_id':'study','on_screen':{'text':'Exact chart'}}}
        source = {'slug':'test','prepared':self.prepared,'native_charts':{'study':{'path':str(chart),'sha256':'a'*64}},
                  'retained_input_hashes':{str(chart):'a'*64}}
        inputs = {'article_id':self.prepared['provenance']['article_id'],'source_revision':'source-r1',
                  'source_hash':'a'*64,'payload_sha256':digest(self.prepared)}
        job = self.generated('derivative',inputs,'copy.json',copy)
        with patch.object(runtime,'_resolve_source',side_effect=lambda _:copy_module(source)), patch('public_derivative.validate_derivative'):
            with self.assertRaises(ContentError):
                runtime._action_job(job['id'],'review',{'expected_version':job['version'],
                    'data':{'decision':'approved','payload_sha256':digest(inputs)}},'editor')
        self.assertEqual(runtime.get_job(job['id'])['status'],'generated')

    def test_review_job_save_failure_recovers_after_source_receipt_without_early_grant(self):
        source = {'slug':'test','prepared':self.prepared}
        inputs = {'article_id':self.prepared['provenance']['article_id'],'source_revision':'source-r1',
                  'source_hash':'a'*64,'payload_sha256':digest(self.prepared)}
        job = self.generated('derivative',inputs,'copy.json',{'headline':'Binding-only test'})
        with patch.object(runtime,'_resolve_source',side_effect=lambda _:copy_module(source)), patch('public_derivative.validate_derivative'):
            with patch('subscription_writer.save_json',side_effect=OSError('job receipt write')):
                with self.assertRaises(OSError):
                    runtime._action_job(job['id'],'review',{'expected_version':job['version'],
                        'data':{'decision':'approved','payload_sha256':digest(inputs)}},'editor')
            record = json.loads((self.root/'qualified-sources'/(runtime.publication.sha(b'test')+'.json')).read_text())
            with self.assertRaises(ContentError): runtime._approved_copy(record)
            runtime.recover_operations()
            approved = json.loads((self.root/'qualified-sources'/(runtime.publication.sha(b'test')+'.json')).read_text())
            self.assertEqual(runtime._approved_copy(approved),{'headline':'Binding-only test'})
        self.assertEqual(runtime.get_job(job['id'])['status'],'reviewed')
        self.assertEqual(list((self.root/'promotion-operations').glob('*.json')),[])

    def test_retained_capsule_hashes_and_exact_canonical_resolution(self):
        retained = self.root / 'article.json'; retained.write_text('original')
        record = {'prepared':self.prepared,'retained_input_hashes':{str(retained):runtime.publication.sha(retained.read_bytes())}}
        with patch('public_derivative.validate_prepared'):
            runtime._verify_retained(record)
            retained.write_text('changed')
            with self.assertRaises(ContentError): runtime._verify_retained(record)
        url = self.prepared['provenance']['article_id']
        inputs = {'article_id':url,'source_revision':'source-r1','source_hash':'a'*64}
        with patch.object(runtime.article_index,'load_posts',return_value=[{'url':url,'slug':'correct'},{'url':'https://other.test','slug':'other'}]), \
                patch.object(runtime,'_source_record',return_value={'prepared':self.prepared}) as read:
            self.assertEqual(runtime._resolve_source(inputs)['slug'],'correct'); read.assert_called_once_with('correct')
        with patch.object(runtime.article_index,'load_posts',return_value=[{'url':url},{'url':url}]):
            with self.assertRaises(ContentError): runtime._resolve_source(inputs)

    def test_generate_queues_durably_and_daemon_never_retries_orphaned_call(self):
        inputs = {'article_id':'canonical','source_revision':'r1','source_hash':'a'*64}
        job = promotion_jobs.create(runtime.jobs_root(),'article_video',inputs,'editor')
        with patch.object(runtime,'_resolve_source',return_value={}):
            queued = runtime._action_job(job['id'],'generate',{'expected_version':job['version']},'editor')
        self.assertEqual(queued['status'],'queued')
        promotion_jobs.update(runtime.jobs_root(),job['id'],queued['version'],status='running',stage='speech')
        with patch.object(runtime.article_index,'posts_lock',side_effect=nullcontext),patch.object(runtime.publication,'recover'), \
                patch.object(runtime,'_configuration',return_value={}),patch.object(promotion_daemon.promotion_worker,'run_one') as worker:
            promotion_daemon.run()
        worker.assert_not_called()
        held = runtime.get_job(job['id'])
        self.assertEqual(held['generation_status'],'unknown_outcome')
        with self.assertRaises(ValueError): promotion_jobs.transition(runtime.jobs_root(),job['id'],'retry',held['version'],'editor')

    def test_daemon_live_owner_blocks_duplicate_recovery_and_explicit_queue_is_consumed(self):
        inputs = {'article_id':'canonical','source_revision':'r1','source_hash':'a'*64}
        job = promotion_jobs.create(runtime.jobs_root(),'derivative',inputs,'editor')
        promotion_jobs.update(runtime.jobs_root(),job['id'],job['version'],status='queued',stage='queued')
        with promotion_jobs.locked(runtime.jobs_root(),name='.daemon.lock',timeout=0),patch.object(promotion_daemon,'_run_claimed') as work:
            self.assertEqual(promotion_daemon.run(),[])
        work.assert_not_called()
        with patch.object(runtime.article_index,'posts_lock',side_effect=nullcontext),patch.object(runtime.publication,'recover'), \
                patch.object(runtime,'_configuration',return_value={}), \
                patch.object(promotion_daemon.promotion_worker,'run_one',return_value=runtime.get_job(job['id'])) as worker:
            promotion_daemon.run()
        worker.assert_called_once()

    def test_exports_require_explicit_copy_approval_and_capture_holds_are_source_bound(self):
        with patch.object(runtime,'_source_record',return_value={'prepared':self.prepared,'copy':{'headline':'Unreviewed'}}):
            for kind in ('social_export','substack_export'):
                with self.assertRaisesRegex(ContentError,'approve'): runtime.create_job({'kind':kind,'slug':'test'},'editor')
        folder = self.root/'briefings'/'2026-10-03';folder.mkdir(parents=True)
        bundle = {'edition_date':'2026-10-03','sources':[]}
        (folder/'sources.json').write_text(json.dumps(bundle))
        (folder/'capture-status.json').write_text(json.dumps({'status':'held','source_bundle_sha256':digest(bundle),'holds':['Source provider unavailable']}))
        self.assertEqual(runtime.list_briefings()[0]['holds'],['Source provider unavailable'])
        (folder/'sources.json').write_text(json.dumps(dict(bundle,title='Changed')))
        self.assertEqual(runtime.list_briefings()[0]['capture_status'],'stale')


def copy_module(value):
    return copy.deepcopy(value)


if __name__ == '__main__':
    unittest.main()
