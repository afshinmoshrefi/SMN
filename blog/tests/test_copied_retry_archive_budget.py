"""Repeated publication preserves failed attempts without creating new jobs."""
from pathlib import Path
import json,shutil,tempfile,unittest
from daily_budget import snapshot,BudgetEvidenceError

class CopiedRetryArchiveBudget(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.date='2026-10-12'
        self.job=self.root/self.date/'chatgpt/jobs/JNJ-repair';self.job.mkdir(parents=True)
        self.save(self.job/'job.json',{'job_id':self.job.name,'input_hashes':{},'evidence_sha256':'evidence'})
        self.save(self.job/'receipt.json',{'job_id':self.job.name,'input_hashes':{},'evidence_sha256':'evidence','finished_utc':'original'})
        self.archive=self.job/'failed-attempt-1';self.archive.mkdir()
        self.save(self.archive/'state.json',{'status':'failed_needs_review','utc':'original-attempt','reason':'configuration'})
    def save(self,path,value):path.write_text(json.dumps(value),encoding='utf8')
    def copy(self):
        destination=self.root/self.date/'publication-2/jobs'/self.job.name
        shutil.copytree(self.job,destination);return destination
    def inventory(self):return snapshot(self.root,self.date,self.root/'registry')
    def test_completed_publication_copy_charges_original_attempt_once(self):
        self.copy();result=self.inventory()
        self.assertEqual(result['consumed_conservative'],2)
        self.assertEqual(result['archived_attempt_paths'],2)
        self.assertEqual(result['archived_unique_attempts'],1)
        self.assertFalse(result['retirement_exclusions'])
    def test_different_archive_bytes_are_distinct_attempts(self):
        copy=self.copy();self.save(copy/'failed-attempt-1/state.json',{'status':'failed','utc':'later'})
        self.assertEqual(self.inventory()['consumed_conservative'],3)
    def test_different_completed_parent_receipts_remain_distinct(self):
        copy=self.copy();receipt=json.loads((copy/'receipt.json').read_text());receipt['finished_utc']='another-run';self.save(copy/'receipt.json',receipt)
        self.assertEqual(self.inventory()['consumed_conservative'],4)
    def test_uncertain_parent_is_never_deduplicated(self):
        (self.job/'receipt.json').unlink();self.copy()
        self.assertEqual(self.inventory()['consumed_conservative'],4)
    def test_extra_retained_archive_file_prevents_deduplication(self):
        copy=self.copy();(copy/'failed-attempt-1/diagnostic.log').write_text('another dispatch')
        self.assertEqual(self.inventory()['consumed_conservative'],3)
    def test_different_archive_name_is_another_attempt(self):
        shutil.copytree(self.archive,self.job/'failed-attempt-2')
        self.assertEqual(self.inventory()['consumed_conservative'],3)

if __name__=='__main__':unittest.main()
