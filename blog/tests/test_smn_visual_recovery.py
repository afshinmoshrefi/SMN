import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import smn_daily
from subscription_writer import load_json,save_json,sha256


class VisualRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.day=smn_daily.Day(tmp.name,'2026-09-30');self.day.symbols=['AMD']
        self.out=Path(tmp.name)/'results/AMD';self.out.mkdir(parents=True)
        (self.out/'article.html').write_bytes(b'reviewed')
        self.proof={'passed':True,'article_sha256':'reviewed'}
        save_json(self.out/'review-binding.held.json',{'review_stage':'rereview','editorial_audit':self.proof,
            'article_html_sha256':sha256(b'reviewed')})
        self.day.state['articles']={'AMD':{'held':{'reason':'screenshot check failed'},'editorially_finalized':True}}

    def test_recovery_restores_only_current_editorial_binding(self):
        with patch('smn_visual.verify_inconsistent_visual_hold'),patch('editorial_gate.verify_review',return_value=self.proof):
            self.day._release_inconsistent_visual_holds()
        self.assertNotIn('held',self.day.state['articles']['AMD'])
        self.assertTrue((self.out/'review-binding.json').exists())
        self.assertFalse(self.day.state['articles']['AMD']['finalized'])

    def test_failed_custody_or_major_defect_keeps_hold_and_binding(self):
        for target in ('smn_visual.verify_inconsistent_visual_hold','editorial_gate.verify_review'):
            with self.subTest(target=target),patch('smn_visual.verify_inconsistent_visual_hold'), \
                 patch('editorial_gate.verify_review',return_value=self.proof),patch(target,side_effect=ValueError('custody or major defect')):
                self.day._release_inconsistent_visual_holds()
            self.assertIn('held',self.day.state['articles']['AMD'])
            self.assertFalse((self.out/'review-binding.json').exists())

    def test_changed_held_binding_cannot_be_restored(self):
        (self.out/'article.html').write_bytes(b'changed')
        with patch('smn_visual.verify_inconsistent_visual_hold'),patch('editorial_gate.verify_review',return_value=self.proof):
            self.day._release_inconsistent_visual_holds()
        self.assertIn('held',self.day.state['articles']['AMD'])

    def test_crash_after_binding_restore_resumes_and_other_holds_stay(self):
        (self.out/'review-binding.held.json').rename(self.out/'review-binding.json')
        self.day.symbols.append('IBM');self.day.state['articles']['IBM']={'held':{'reason':'unverified cause'}}
        with patch('smn_visual.verify_inconsistent_visual_hold'),patch('editorial_gate.verify_review',return_value=self.proof):
            self.day._release_inconsistent_visual_holds()
        self.assertNotIn('held',self.day.state['articles']['AMD'])
        self.assertIn('held',self.day.state['articles']['IBM'])

    def test_five_cached_approvals_are_verified_only_held_article_inspected(self):
        peers=['SPY','QQQ','IBM','MU','VIX'];self.day.symbols+=peers
        for sym in self.day.symbols:
            out=self.day.root/'results'/sym;out.mkdir(parents=True,exist_ok=True)
            save_json(out/'layout-checks.json',{});save_json(out/'hero-check.json',{})
            if sym!='AMD':
                self.day.state['articles'][sym]={'editorially_finalized':True,'finalized':True}
                save_json(out/'review-binding.json',{'review_stage':'review'})
                save_json(out/'visual-checks.json',{'passed':True})
        with patch('smn_visual.verify_inconsistent_visual_hold'),patch('editorial_gate.verify_review',return_value=self.proof), \
             patch('editorial_gate.verify_complete',return_value=self.proof), \
             patch('smn_visual.verify_article_visual',return_value={'passed':True}) as cached, \
             patch('smn_visual.article',return_value={'passed':True}) as fresh:
            self.day.visual()
        self.assertEqual(cached.call_count,5);fresh.assert_called_once()
        self.assertEqual(fresh.call_args.args[2],'AMD');self.assertEqual(fresh.call_args.kwargs['max_jobs'],40)
        self.assertTrue(all(self.day.state['articles'][sym]['finalized'] for sym in self.day.symbols))

    def test_publication_exceptions_after_activation_roll_back(self):
        save_json(self.day.root/'primary-stage.json',{'record':'staged'})
        import subscription_primary_publish as publisher
        for defect in ('activation','landing','finish'):
            def activate(*args):
                save_json(self.day.root/'primary-activation-attempt.json',{'record':'staged'})
                if defect=='activation':raise RuntimeError('activation failure')
            with self.subTest(defect=defect),patch('subscription_publication.validate_staged_reviews'), \
                 patch.object(publisher,'activate',side_effect=activate), \
                 patch('smn_visual.landing',side_effect=smn_daily.Hold('model-job budget of40 exhausted') if defect=='landing' else None,
                       return_value={'passed':True}), \
                 patch.object(publisher,'finish',side_effect=ValueError('finish failure') if defect=='finish' else None), \
                 patch.object(publisher,'call') as rollback:
                with self.assertRaises((RuntimeError,smn_daily.Hold,ValueError)):self.day.publish(self.day.root)
            rollback.assert_called_once_with({'record':'staged'},'rollback')

    def test_preparation_failure_does_not_attempt_nonexistent_rollback(self):
        save_json(self.day.root/'primary-stage.json',{'record':'staged'})
        import subscription_primary_publish as publisher
        with patch('subscription_publication.validate_staged_reviews'), \
             patch.object(publisher,'activate',side_effect=ValueError('Edition already present')), \
             patch.object(publisher,'call') as rollback:
            with self.assertRaisesRegex(ValueError,'Edition already present'):
                self.day.publish(self.day.root)
        rollback.assert_not_called()

    def test_real_rollback_failure_remains_visible(self):
        save_json(self.day.root/'primary-stage.json',{'record':'staged'})
        save_json(self.day.root/'primary-activation-attempt.json',{'record':'staged'})
        import subscription_primary_publish as publisher
        with patch('subscription_publication.validate_staged_reviews',side_effect=ValueError('custody failed')), \
             patch.object(publisher,'call',side_effect=RuntimeError('rollback failed')):
            with self.assertRaisesRegex(smn_daily.Hold,'rollback also failed'):
                self.day.publish(self.day.root)

    def test_publisher_marks_only_the_actual_activation_boundary(self):
        import json
        import subscription_primary_publish as publisher
        receipt={'source_commit':'a'*40,'record':'staged','remote':'remote',
                 'source_tar_sha256':'hash','package_tar_sha256':'hash'}
        save_json(self.day.root/'primary-stage.json',receipt)
        for phase in ('prepare','activate'):
            marker=self.day.root/'primary-activation-attempt.json'
            if marker.exists():marker.unlink()
            def remote(command,**kwargs):
                if ' prepare ' in command:
                    if phase=='prepare':raise ValueError('prepare refused')
                    return json.dumps({'record':'staged'})
                return ''
            with self.subTest(phase=phase),patch('subscription_publication.validate_staged_reviews'), \
                 patch.object(publisher.shared,'clean_main',return_value=(self.day.root,'a'*40)), \
                 patch.object(publisher.shared,'sha',return_value='hash'), \
                 patch.object(publisher.shared,'run'),patch.object(publisher,'remote',side_effect=remote), \
                 patch.object(publisher,'call',side_effect=RuntimeError('activation transport failed')) as call:
                with self.assertRaises((ValueError,RuntimeError)):
                    publisher.activate(self.day.root,self.day.root,'node',None)
            self.assertEqual(marker.exists(),phase=='activate')
            self.assertEqual(call.call_count,1 if phase=='activate' else 0)


if __name__=='__main__':unittest.main()
