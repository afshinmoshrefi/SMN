import hashlib,json,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]))
from subscription_daily import DailyController
SHA='a'*40
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
class FakeEdition:
    calls=[]
    def __init__(self,root,date,codex=None):self.root=Path(root);self.date=date
    def job(self,s,stage):return self.root/'jobs'/(s+'-'+self.date.replace('-','')+'-'+stage)
    def result(self,s):return self.root/'results'/s
    def prepare(self,s,stage):
        self.calls.append(('prepare',s));self.job(s,stage).mkdir(parents=True);self.result(s).mkdir(parents=True,exist_ok=True);(self.result(s)/'bundle.json').write_text('{}')
    def run(self,s,stage):
        self.calls.append(('run',stage,s));j=self.job(s,stage);j.mkdir(parents=True,exist_ok=True);(j/'output.json').write_text(json.dumps({'stage':stage,'symbol':s}));(j/'receipt.json').write_text(json.dumps({'output_sha256':sha(j/'output.json')}))
    def receive(self,s,stage='write'):
        self.calls.append(('receive',s));(self.result(s)/'article.json').write_text('{}');(self.result(s)/'mechanical-checks.json').write_text('{}')
    def review(self,s,stage='review'):self.calls.append(('review',s));self.job(s,stage).mkdir(parents=True)
    def finalize(self,s,stage='review'):
        self.calls.append(('finalize',s));(self.result(s)/'article.html').write_text('<article/>');(self.result(s)/'review-binding.json').write_text('{}')
class Tests(unittest.TestCase):
    def make(self,n=6):
        d=Path(tempfile.mkdtemp());(d/'production').mkdir();posts=[{'symbol':f'S{i}','published_date':'2026-09-10'} for i in range(n)];(d/'production/posts.json').write_text(json.dumps(posts));(d/'sources.json').write_text('{}');(d/'production-engine-export.json').write_text('{}')
        for p in posts:(d/'production'/p['symbol']).mkdir()
        return d
    def execute(self,d,budget=20):return DailyController(d,'2026-09-10',max_new_model_jobs=budget,source_commit=SHA).run()
    def test_repeat_is_noop_and_latest_snapshot(self):
        d=self.make();FakeEdition.calls=[]
        with patch('subscription_daily.Edition',FakeEdition):self.assertEqual(self.execute(d)['status'],'awaiting_visual_review');before=len(FakeEdition.calls);self.execute(d)
        self.assertEqual(len(FakeEdition.calls),before);self.assertIn('results/S0/review-binding.json',json.loads((d/'daily-state.json').read_text())['stages']['S0']['outputs'])
    def test_budget_resume_and_crash_receipt_reuse(self):
        d=self.make();FakeEdition.calls=[]
        with patch('subscription_daily.Edition',FakeEdition):
            self.assertEqual(self.execute(d,1)['status'],'budget_exhausted');self.assertEqual(self.execute(d)['status'],'awaiting_visual_review')
        self.assertEqual(len([x for x in FakeEdition.calls if x[0]=='run']),12)
    def test_completed_receipt_after_crash_before_checkpoint_is_reused(self):
        d=self.make();FakeEdition.calls=[]
        with patch('subscription_daily.Edition',FakeEdition):self.execute(d)
        state=json.loads((d/'daily-state.json').read_text());state['stages']['S0']={'prepare':{'status':'done'},'outputs':state['stages']['S0']['outputs']};state['status']='ready';(d/'daily-state.json').write_text(json.dumps(state));FakeEdition.calls=[]
        with patch('subscription_daily.Edition',FakeEdition):self.assertEqual(self.execute(d)['status'],'awaiting_visual_review')
        self.assertFalse(any(x[0]=='run' for x in FakeEdition.calls))
    def test_zero_budget_reuses_completed_receipts(self):
        d=self.make();FakeEdition.calls=[]
        with patch('subscription_daily.Edition',FakeEdition):self.execute(d)
        state=json.loads((d/'daily-state.json').read_text());state['stages']['S0']={'prepare':{'status':'done'},'outputs':state['stages']['S0']['outputs']};state['status']='ready';(d/'daily-state.json').write_text(json.dumps(state));FakeEdition.calls=[]
        with patch('subscription_daily.Edition',FakeEdition):self.assertEqual(self.execute(d,0)['status'],'awaiting_visual_review')
        self.assertFalse(any(x[0]=='run' for x in FakeEdition.calls))
    def test_hold_is_not_retried(self):
        class Broken(FakeEdition):
            def run(self,*args):self.calls.append(('broken',));raise RuntimeError('offline')
        d=self.make();FakeEdition.calls=[]
        with patch('subscription_daily.Edition',Broken):self.assertEqual(self.execute(d)['status'],'failed_needs_review');self.assertEqual(self.execute(d)['status'],'held_until_new_attempt')
        self.assertEqual(FakeEdition.calls.count(('broken',)),1)
    def test_output_tamper_and_partial_prepare_hold(self):
        d=self.make()
        with patch('subscription_daily.Edition',FakeEdition):self.execute(d)
        (d/'results/S0/article.html').write_text('tampered')
        with patch('subscription_daily.Edition',FakeEdition):self.assertEqual(self.execute(d)['status'],'failed_needs_review')
        d=self.make();out=d/'results/S0';out.mkdir(parents=True);(out/'bundle.json').write_text('{}')
        with patch('subscription_daily.Edition',FakeEdition):self.assertEqual(self.execute(d)['status'],'failed_needs_review')
    def test_input_lock_date_symlink_and_source_guards(self):
        d=self.make(5);c=DailyController(d,'bad',source_commit=SHA)
        with self.assertRaises(ValueError):c.run()
        self.assertFalse(c.lock.exists());self.assertFalse(c.state_path.exists())
        d=self.make();c=DailyController(d,'2026-09-31',source_commit=SHA)
        with self.assertRaises(ValueError):c.run()
        self.assertFalse(c.lock.exists());self.assertFalse(c.state_path.exists())
        d=self.make();c=DailyController(d,'2026-09-10',source_commit=SHA);c.lock.mkdir()
        with self.assertRaises(RuntimeError):c.run()
        self.assertFalse(c.state_path.exists())
        d=self.make()
        with patch.object(Path,'is_symlink',autospec=True,side_effect=lambda p:p.name=='production-engine-export.json'):
            with self.assertRaises(ValueError):self.execute(d)
        with self.assertRaises(ValueError):DailyController(self.make(),'2026-09-10',source_commit='bad').run()
if __name__=='__main__':unittest.main()
