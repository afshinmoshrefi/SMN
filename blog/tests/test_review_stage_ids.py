"""Recovery review IDs retain genuine receipts; path components remain closed."""
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from editorial_gate import valid_review_stage
import production_continuity as publication

class ReviewStageIds(unittest.TestCase):
    def test_genuine_recovery_stage_with_digits_is_supported(self):
        for stage in ['review','rereview','binding-review','approved64-rereview','revision2-review']:
            with self.subTest(stage=stage):self.assertTrue(valid_review_stage(stage))
    def test_unsafe_paths_non_ascii_types_and_excess_length_are_rejected(self):
        for stage in ['',None,{},'../review','a/b','a\\b','a..review','Review','réview','64-review','a'*81,'-review']:
            with self.subTest(stage=stage):self.assertFalse(valid_review_stage(stage))
    def test_numeric_stage_reaches_original_source_qualification_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);result=root/'results/BLK';result.mkdir(parents=True)
            (result/'review-binding.json').write_text(json.dumps({'review_stage':'approved64-rereview'}))
            (result/'article.json').write_text('{}');review=root/'jobs/BLK-20261013-approved64-rereview/output.json'
            review.parent.mkdir(parents=True);review.write_text('{}')
            with patch.object(publication,'qualified_html',return_value=('Genuine gated HTML',None,{})) as gate:
                stages,_=publication._eligible(root,'2026-10-13',['BLK'])
            self.assertEqual(stages,{'BLK':'approved64-rereview'})
            self.assertEqual(gate.call_args.args[1],review)
    def test_unsafe_stage_never_reaches_source_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);result=root/'results/BLK';result.mkdir(parents=True)
            (result/'review-binding.json').write_text(json.dumps({'review_stage':'../review'}))
            with patch.object(publication,'qualified_html') as gate:
                self.assertEqual(publication._eligible(root,'2026-10-13',['BLK'])[0],{})
            gate.assert_not_called()

if __name__=='__main__':unittest.main()
