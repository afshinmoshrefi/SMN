import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import subscription_publication as publication
import publication_continuity as rolling
import subscription_primary_publish as primary
from install_smn_recovery_edition import validate_package

DATE='2026-10-05'
COMMIT='a'*40


class ContinuityPackageTests(unittest.TestCase):
    def frozen(self,root):
        publication.write(root/'production/posts.json',[{'symbol':'AAA'},{'symbol':'BBB'}])
        publication.write(root/'input-selection.json',{'date':DATE,'symbols':['AAA','BBB'],
            'files':{'production/posts.json':publication.digest_bytes((root/'production/posts.json').read_bytes())}})
        publication.write(root/'smn-daily-state.json',{'date':DATE,'articles':{}})

    def package(self,root):
        with patch('membership_pipeline.capture_sources',return_value=({},{})):
            return publication.package(root,DATE,COMMIT,{},continuity={'revision':1,'revision_id':'b'*64})

    def test_missing_selection_emits_dated_unknown_lineup_notice(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            manifest=self.package(root)
            self.assertEqual((manifest['coverage_status'],manifest['selection_status'],manifest['complete']),('notice','pending',False))
            self.assertEqual(manifest['expected_symbols'],[])
            self.assertIsNone(manifest['selection_sha256'])
            validated,entries=validate_package(root/'publication-package')
            self.assertEqual(validated['revision_id'],'b'*64)
            self.assertEqual(entries,[])
            publication.validate_staged_reviews(root)

    def test_frozen_selection_zero_approved_preserves_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.frozen(root)
            manifest=self.package(root)
            self.assertEqual(manifest['expected_symbols'],['AAA','BBB'])
            self.assertEqual(manifest['pending_symbols'],['AAA','BBB'])
            self.assertEqual(manifest['published_symbols'],[])
            self.assertFalse(manifest['complete'])
            validate_package(root/'publication-package')
            publication.validate_staged_reviews(root)

    def test_unexpected_subject_rejected_before_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.frozen(root)
            with self.assertRaisesRegex(ValueError,'frozen selection'):
                publication.package(root,DATE,COMMIT,{'ZZZ':'review'},continuity={'revision':1,'revision_id':'b'*64})

    def test_legacy_manifest_cannot_adopt_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.frozen(root)
            manifest=self.package(root)
            manifest.pop('publication_policy')
            publication.write(root/'publication-package/manifest.json',manifest)
            with self.assertRaisesRegex(ValueError,'policy marker'):
                validate_package(root/'publication-package')

    def test_changed_selection_evidence_rejected_on_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.frozen(root);self.package(root)
            selection=publication.read(root/'input-selection.json');selection['symbols']=['BBB','AAA']
            publication.write(root/'input-selection.json',selection)
            with self.assertRaisesRegex(ValueError,'frozen selection|Selected lineup|coverage'):
                publication.validate_staged_reviews(root)


class RestartTests(unittest.TestCase):
    def fake_stage(self,tx,repo,date,stages,revision,*,candidate_base=None):
        with patch('membership_pipeline.capture_sources',return_value=({},{})):
            manifest=publication.package(tx,date,COMMIT,{},continuity=revision)
        publication.write(tx/'primary-stage.json',{'transaction_id':manifest['transaction_id']})
        return manifest

    def fake_verified(self,tx,repo,node,playwright):
        manifest=publication.read(tx/'publication-package/manifest.json')
        return {'status':'live_verified','revision_id':manifest['revision_id'],
                'transaction_id':manifest['transaction_id'],'source_commit':COMMIT,'urls':[]}

    def test_interrupted_snapshot_is_retained_and_rebuilt_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            identity=rolling._json_sha({'date':DATE,'selection':None,'source':COMMIT,'articles':{}})
            (root/'publication-revisions'/identity/'attempt-1').mkdir(parents=True)
            with patch.object(rolling.subprocess,'check_output',return_value=COMMIT), \
                    patch('subscription_primary_publish.stage_continuity',side_effect=self.fake_stage), \
                    patch.object(rolling,'_resume_or_publish',side_effect=self.fake_verified):
                receipt=rolling.publish_available(root,DATE,'dev',repo=root)
                repeat=rolling.publish_available(root,DATE,'dev',repo=root)
            self.assertEqual(receipt,repeat)
            self.assertEqual(receipt['coverage_status'],'notice')
            self.assertEqual(receipt['selection_status'],'pending')
            self.assertEqual(receipt['revision'],1)
            family=root/'publication-revisions'/identity
            self.assertTrue(any(p.name.startswith('incomplete-attempt-1-') for p in family.iterdir()))
            self.assertTrue((family/'attempt-1'/'coverage-receipt.json').is_file())

    def test_rolled_back_attempt_uses_new_transaction_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            identity=rolling._json_sha({'date':DATE,'selection':None,'source':COMMIT,'articles':{}})
            old=root/'publication-revisions'/identity/'attempt-1'
            old.mkdir(parents=True)
            publication.write(old/'primary-stage.json',{'transaction_id':'c'*64})
            with patch.object(rolling.subprocess,'check_output',return_value=COMMIT), \
                    patch.object(rolling,'_remote_status',return_value={'status':'rolled_back'}), \
                    patch('subscription_primary_publish.stage_continuity',side_effect=self.fake_stage), \
                    patch.object(rolling,'_resume_or_publish',side_effect=self.fake_verified):
                receipt=rolling.publish_available(root,DATE,'dev',repo=root)
            self.assertEqual(receipt['revision'],1)
            self.assertEqual(receipt['revision_id'],identity)
            self.assertEqual(receipt['transaction_id'],rolling._json_sha({'content_sha256':identity,'attempt':2}))
            self.assertTrue((root/'publication-revisions'/identity/'attempt-2'/'coverage-receipt.json').is_file())

    def test_candidate_requires_exact_pushed_head_and_unchanged_main_base(self):
        base='a'*40;head='b'*40
        def output(args,**kwargs):
            command=args[3]
            if command=='status':return ''
            if command=='rev-parse':return head if args[4]=='HEAD' else base
            if command=='branch':return 'codex/coverage-candidate'
            if command=='merge-base':return base
            if command=='ls-remote':return head+'\trefs/heads/codex/coverage-candidate'
            raise AssertionError(args)
        with tempfile.TemporaryDirectory() as folder,patch.object(primary.shared,'run'), \
                patch.object(primary.subprocess,'check_output',side_effect=output):
            self.assertEqual(primary.candidate_source(Path(folder),base)[1:],(head,'codex/coverage-candidate'))
            with self.assertRaisesRegex(ValueError,'base'):
                primary.candidate_source(Path(folder),'c'*40)
            with patch.object(primary.subprocess,'check_output',side_effect=lambda args,**kw:
                    ('c'*40+'\trefs/heads/codex/coverage-candidate') if args[3]=='ls-remote' else output(args,**kw)):
                with self.assertRaisesRegex(ValueError,'not pushed'):
                    primary.candidate_source(Path(folder),base)


if __name__=='__main__': unittest.main()
