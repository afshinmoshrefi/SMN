import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine_edition_workflow import apply_copy_edits
from visual_evidence import digest


class ExplicitCopyEdits(unittest.TestCase):
    def setUp(self):
        self.article={'title':'A seasonal question','sections':[{'role':'risk','paragraphs':[{'text':'The entry-relative low was -4.09%.','source_ids':['engine']}]}]}
        self.request={'base_article_sha256':digest(self.article),'changes':[{'path':['sections',0,'paragraphs',0,'text'],'before':'The entry-relative low was -4.09%.','after':'The shares reached 4.09% below the starting price.'}]}

    def test_explicit_copy_revision_preserves_evidence_references_and_original(self):
        revised=apply_copy_edits(self.article,self.request)
        self.assertEqual(revised['sections'][0]['paragraphs'][0]['source_ids'],['engine'])
        self.assertEqual(self.article['sections'][0]['paragraphs'][0]['text'],self.request['changes'][0]['before'])
        self.assertEqual(revised['sections'][0]['paragraphs'][0]['text'],self.request['changes'][0]['after'])

    def test_stale_preimage_rejected(self):
        self.article['title']='Changed article'
        with self.assertRaisesRegex(ValueError,'base changed'):apply_copy_edits(self.article,self.request)

    def test_copyedit_cannot_reassign_sources_or_study_metadata(self):
        self.request['changes'][0]['path']=['sections',0,'paragraphs',0,'source_ids']
        with self.assertRaisesRegex(ValueError,'reader copy only'):apply_copy_edits(self.article,self.request)


if __name__=='__main__':unittest.main()
