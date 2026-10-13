"""Publication copies must not create new unfinished model effects."""
import tempfile
import unittest
import json
from pathlib import Path
from production_continuity import _copy_subject
from daily_budget import snapshot


class SnapshotJobs(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source=Path(self.temp.name)/'reader'
        self.target=Path(self.temp.name)/'transaction'
        (self.source/'results/ABC').mkdir(parents=True)
        (self.source/'results/ABC/article.json').write_text('{}')
        self.target.mkdir()
        self.review=self.job('review', completed=True)

    def job(self,stage,completed=False):
        job=self.source/'jobs'/('ABC-20261013-'+stage)
        job.mkdir(parents=True)
        identity={'job_id':job.name,'input_hashes':{},'evidence_sha256':'fixture-evidence'}
        (job/'job.json').write_text(json.dumps(identity))
        if completed:(job/'receipt.json').write_text(json.dumps(identity))
        return job

    def copy(self):
        _copy_subject(self.source,self.target,'ABC','2026-10-13','review')

    def test_failed_research_and_all_archives_stay_in_canonical_reader(self):
        failed=self.job('research')
        for n in range(1,4):
            archive=failed/('failed-attempt-'+str(n));archive.mkdir()
            (archive/'diagnostic.log').write_text('invalid_json_schema')
        before={str(p):p.read_bytes() for p in failed.rglob('*') if p.is_file()}
        self.copy()
        self.assertFalse((self.target/'jobs'/failed.name).exists())
        self.assertEqual(before,{str(p):p.read_bytes() for p in failed.rglob('*') if p.is_file()})
        self.assertTrue((self.target/'jobs'/self.review.name/'receipt.json').is_file())

    def test_running_visual_claim_is_not_copied(self):
        visual=self.job('visual');(visual/'.claim').mkdir()
        (visual/'.claim/owner.json').write_text('{"pid":123}')
        (visual/'state.json').write_text('{"status":"running"}')
        self.copy()
        self.assertFalse((self.target/'jobs'/visual.name).exists())
        self.assertTrue((visual/'.claim/owner.json').is_file())

    def test_completed_corrected_research_and_failed_history_are_reused(self):
        corrected=self.job('research-schema-recovery-v1',completed=True)
        archive=corrected/'failed-attempt-1';archive.mkdir()
        (archive/'diagnostic.log').write_text('retained previous attempt')
        self.copy()
        self.assertEqual((self.target/'jobs'/corrected.name/'receipt.json').read_bytes(),
                         (corrected/'receipt.json').read_bytes())
        self.assertEqual((self.target/'jobs'/corrected.name/'failed-attempt-1/diagnostic.log').read_bytes(),
                         (archive/'diagnostic.log').read_bytes())

    def test_legacy_bound_review_is_preserved_without_changing_approval_gate(self):
        (self.review/'receipt.json').unlink()
        (self.review/'output.json').write_text('{"passed":true}')
        self.copy()
        self.assertEqual((self.target/'jobs'/self.review.name/'output.json').read_bytes(),
                         (self.review/'output.json').read_bytes())

    def test_repeated_publication_keeps_real_failed_attempt_budget_unchanged(self):
        failed=self.job('research')
        for n in range(1,4):
            archive=failed/('failed-attempt-'+str(n));archive.mkdir()
            (archive/'diagnostic.log').write_text('original schema failure '+str(n))
        self.job('research-schema-recovery-v1',completed=True)
        def inventory():return snapshot(Path(self.temp.name),'2026-10-13',
                                       Path(self.temp.name)/'registry',date_root=True)
        before=inventory()
        self.assertEqual(before['consumed_conservative'],6)
        for n in range(5):
            self.target=Path(self.temp.name)/('publication-'+str(n));self.target.mkdir()
            self.copy()
        after=inventory()
        self.assertEqual(after['consumed_conservative'],6)
        self.assertEqual(after['archived_unique_attempts'],3)
        self.assertEqual(after['prepared_or_uncertain_paths'],1)
        self.assertEqual(after['retirement_exclusions'],[])

    def test_publication_does_not_manufacture_an_extra_active_claim(self):
        visual=self.job('visual');(visual/'.claim').mkdir()
        (visual/'.claim/owner.json').write_text('{"pid":123}')
        (visual/'state.json').write_text('{"status":"running"}')
        self.copy()
        result=snapshot(Path(self.temp.name),'2026-10-13',
                        Path(self.temp.name)/'registry',date_root=True)
        self.assertEqual(result['consumed_conservative'],2)
        self.assertEqual(result['running_or_claimed_without_receipt'],[str(visual.resolve())])


if __name__=='__main__':unittest.main()
