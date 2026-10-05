import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import subscription_publication as p
import smn_held_recovery as recovery
import smn_runtime_assets as assets
from smn_daily import Hold

DATE='2026-10-05'
SYMBOLS=['XLF','SI','SPY','QQQ','AMZN','NVDA']


def fixture(root,held=()):
    p.write(root/'production/posts.json',[{'symbol':s} for s in SYMBOLS])
    p.write(root/'input-selection.json',{'date':DATE,'symbols':SYMBOLS,
        'files':{'production/posts.json':p.digest_bytes((root/'production/posts.json').read_bytes())}})
    p.write(root/'smn-daily-state.json',{'date':DATE,'profile':'chatgpt','roles':{},
        'publication_origin':None,'articles':{s:{'finalized':s not in held,**({'held':{'reason':'failed'}} if s in held else {})} for s in SYMBOLS}})


class LineupTests(unittest.TestCase):
    def test_partial_package_rejected_before_review_or_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root,('SI','NVDA'))
            with patch.object(p,'reviewed') as review, self.assertRaisesRegex(ValueError,'Incomplete selected edition'):
                p.package(root,DATE,'a'*40,{s:'review' for s in SYMBOLS if s not in ('SI','NVDA')})
            review.assert_not_called();self.assertFalse((root/'publication-package').exists())

    def test_exact_lineup_accepts_all_and_rejects_duplicate_extra_or_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            self.assertEqual(p.complete_lineup(root,DATE,SYMBOLS,True),SYMBOLS)
            for invalid in (SYMBOLS[:-1],SYMBOLS+['NVDA'],SYMBOLS[:-1]+['OTHER']):
                with self.assertRaises(ValueError):p.complete_lineup(root,DATE,invalid,True)
            fixture(root,('NVDA',))
            with self.assertRaisesRegex(ValueError,'unfinished'):p.complete_lineup(root,DATE,SYMBOLS,True)

    def test_changed_input_or_wrong_date_cannot_redefine_lineup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            with self.assertRaisesRegex(ValueError,'dated'):p.complete_lineup(root,'2026-10-06',SYMBOLS,True)
            p.write(root/'production/posts.json',[{'symbol':s} for s in SYMBOLS[:-1]])
            with self.assertRaisesRegex(ValueError,'changed'):p.complete_lineup(root,DATE,SYMBOLS,True)

    def test_stale_four_article_package_rejected_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            p.write(root/'publication-package/manifest.json',{'edition_date':DATE,'production_allowed':True})
            p.write(root/'publication-package/entries.json',[{'symbol':s} for s in SYMBOLS[:4]])
            with self.assertRaisesRegex(ValueError,'Incomplete selected edition'):p.validate_staged_reviews(root)

    def test_installer_rejects_unbound_or_partial_production_package(self):
        from install_smn_recovery_edition import validate_package
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);origin='https://seasonalmarketnews.com'
            manifest={'target_origin':origin,'production_allowed':True,'editorial_gate_version':1,
                      'edition_date':DATE,'source_commit':'a'*40,'files':{}}
            p.write(root/'entries.json',[{'symbol':s} for s in SYMBOLS[:4]])
            for expected in (None,SYMBOLS):
                manifest['expected_symbols']=expected;p.write(root/'manifest.json',manifest)
                with self.assertRaisesRegex(ValueError,'complete selected'):validate_package(root,origin,True)

    def test_direct_activation_refuses_old_partial_receipt_before_lock(self):
        import install_smn_primary_edition as installer
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record=root/'record'
            p.write(record/'receipt.json',{'editorial_gate_version':1,'status':'prepared',
                    'edition_date':DATE,'urls':[]})
            with patch.object(installer,'guard'),patch.object(installer,'PRODUCTION',True),patch.object(installer,'STATE',root):
                with self.assertRaisesRegex(ValueError,'complete selected'):installer.activate(record)
            self.assertFalse((root/installer.LOCK_NAME).exists())


class RuntimeAssetTests(unittest.TestCase):
    def test_real_subprocess_preflight_uses_explicit_asset_from_empty_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);asset=root/'motifs.json';p.write(asset,{'SI':['silver']})
            empty=root/'isolated-release-cwd';empty.mkdir()
            output=subprocess.check_output([sys.executable,assets.__file__],cwd=empty,text=True,
                env={**os.environ,'SMN_TICKER_MOTIFS_FILE':str(asset)})
            self.assertEqual(json.loads(output)['ticker_motifs_sha256'],p.digest_bytes(asset.read_bytes()))

    def test_missing_malformed_and_relative_override_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.json'
            for value in ('relative.json',str(path)):
                with patch.dict(os.environ,{'SMN_TICKER_MOTIFS_FILE':value}),self.assertRaises((ValueError,OSError)):
                    assets.preflight()
            path.write_text('[]')
            with patch.dict(os.environ,{'SMN_TICKER_MOTIFS_FILE':str(path)}),self.assertRaises(ValueError):assets.preflight()


class RecoveryTests(unittest.TestCase):
    def test_reinspection_uses_the_reviewer_role(self):
        import smn_models
        self.assertEqual(smn_models.role_of('reinspect-review'),'review')

    def prepare(self,root):
        fixture(root,('SI','NVDA'));state=p.read(root/'smn-daily-state.json')
        state['articles']['SI']={'held':{'reason':'fewer than two accessible primary pages for SI'}}
        state['articles']['NVDA']={'mechanical_ok':True,'review_stage':'rereview','held':{'reason':'failed review twice'}}
        p.write(root/'smn-daily-state.json',state)
        for n in range(37):(root/'jobs'/str(n)).mkdir(parents=True)
        return root

    def test_plan_counts_retained_jobs_and_required_checks_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.prepare(Path(tmp));before=recovery.hashes(root)
            plan=recovery.plan(root,['SI'],['NVDA'])
            self.assertEqual((plan['jobs_used'],plan['minimum_new_jobs'],plan['minimum_total_jobs']),(37,9,46))
            self.assertFalse(plan['budget_sufficient']);self.assertEqual(recovery.hashes(root),before)
            self.assertEqual(set(plan['approved_articles']),{'XLF','SPY','QQQ','AMZN'})

    def test_insufficient_budget_rejects_before_copy_or_model(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            root=self.prepare(Path(tmp)/'original');target=root.with_name('recovery')
            with patch.object(recovery,'lock',return_value=nullcontext()),patch.object(recovery.shutil,'copytree') as copy:
                with self.assertRaisesRegex(Hold,'46 cumulative'):recovery.recover(root,target,['SI'],['NVDA'])
                copy.assert_not_called();self.assertFalse(target.exists())

    def test_recovery_cannot_select_successes_or_omit_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.prepare(Path(tmp))
            for sources,reviews in ((['XLF'],['NVDA']),(['SI'],[]),(['SI'],['SI'])):
                with self.assertRaises(Hold):recovery.plan(root,sources,reviews)

    def test_successful_recovery_copy_retains_original_and_is_idempotent(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            root=self.prepare(Path(tmp)/'original');target=root.with_name('recovery')
            before=recovery.hashes(root)
            def finish(day):
                self.assertEqual(set(day.symbols),{'SI','NVDA'})
                for sym in day.symbols:day.state['articles'][sym].update(finalized=True)
                day.save()
            with patch.object(recovery,'lock',return_value=nullcontext()),patch('editorial_gate.verify_complete'), \
                 patch.object(recovery.Day,'research'),patch.object(recovery.Day,'articles'), \
                 patch.object(recovery.Day,'visual',autospec=True,side_effect=finish):
                result=recovery.recover(root,target,['SI'],['NVDA'],46)
                self.assertTrue(result['passed'])
                result=recovery.recover(root,target,['SI'],['NVDA'],46)
                self.assertTrue(result['passed'])
            self.assertEqual(recovery.hashes(root),before)
            self.assertEqual(p.read(target/'held-recovery.json')['jobs_used_after'],37)


if __name__=='__main__':unittest.main()
