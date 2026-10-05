import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import production_continuity as rolling
import subscription_publication as publication
from install_smn_recovery_edition import validate_package

DATE='2026-10-05'
COMMIT='a'*40

class ProductionContinuityTests(unittest.TestCase):
    def test_policy_requires_exact_operator_opt_in(self):
        installer=types.SimpleNamespace(configure_production=lambda:None,guard=lambda:None)
        with patch.dict(sys.modules,{'install_smn_primary_edition':installer}), patch.object(rolling,'read',return_value={'source_commit':COMMIT,'reader_provider':'chatgpt'}):
            with self.assertRaisesRegex(ValueError,'opt-in'):rolling.require_policy(COMMIT)
        with patch.dict(sys.modules,{'install_smn_primary_edition':installer}), patch.object(rolling,'read',return_value={'source_commit':COMMIT,'publication_policy':'continuity-v1'}), patch.object(rolling.subprocess,'check_output',return_value=''):
            rolling.require_policy(COMMIT)
            with self.assertRaises(ValueError):rolling.require_policy('b'*40)

    def test_production_notice_preserves_unknown_selection_and_requires_optin(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            publication.write(root/'smn-daily-state.json',{'date':DATE,'profile':'chatgpt','publication_origin':'https://seasonalmarketnews.com'})
            with patch.object(rolling,'require_policy') as policy:
                m=publication.package(root,DATE,COMMIT,{},target_origin='https://seasonalmarketnews.com',continuity={'revision':1,'revision_id':'b'*64})
                validate_package(root/'publication-package',origin='https://seasonalmarketnews.com',production=True)
                publication.validate_staged_reviews(root)
                self.assertGreaterEqual(policy.call_count,3)
            self.assertEqual(m['coverage_status'],'notice')
            self.assertFalse(m['complete'])
            self.assertEqual(m['expected_symbols'],[])

    def test_wrong_target_and_increased_budget_rejected(self):
        with patch.object(rolling,'require_policy'):
            with self.assertRaises(ValueError):rolling.publish_available(Path('.'),DATE,'dev')
            with self.assertRaisesRegex(ValueError,'cap'):rolling.publish_available(Path('.'),DATE,'production',max_jobs=41)

    def test_legacy_day_reuses_receipt_without_transaction(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            publication.write(root/'production/posts.json',[{'symbol':'AAA'}])
            publication.write(root/'input-selection.json',{'date':DATE,'symbols':['AAA'],'files':{'production/posts.json':rolling._sha(root/'production/posts.json')}})
            publication.write(root/'production-publication-receipt.json',{'status':'live_verified','publication_policy':'legacy'})
            with patch.object(rolling,'require_policy'),patch.object(rolling,'reconcile_legacy',return_value={'complete':True,'reused_legacy':True}) as reconcile,patch.object(rolling,'_eligible') as eligible:
                result=rolling.publish_available(root,DATE,'production')
                eligible.assert_not_called()
                reconcile.assert_called_once_with(root,DATE)
                self.assertTrue(result['reused_legacy'])

    def test_local_interrupted_activation_rolls_back_without_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            publication.write(root/'production-stage.json',{'record':str(root),'transaction_id':'tx'})
            publication.write(root/'receipt.json',{'status':'activating','source_commit':COMMIT,'transaction_id':'tx'})
            installer=types.SimpleNamespace(rollback=lambda record:None)
            with patch.dict(sys.modules,{'install_smn_primary_edition':installer}),patch.object(rolling,'require_policy'),patch.object(rolling.subprocess,'run') as browser:
                with self.assertRaisesRegex(ValueError,'rolled back'):rolling._resume_or_publish(root,root,'node',None)
                browser.assert_not_called()

if __name__=='__main__':unittest.main()
