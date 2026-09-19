import json, tempfile, unittest, sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from subscription_daily import DailyController

class FakeEdition:
    def __init__(self,*a,**k): pass
    def job(self,s,stage): return Path(tempfile.mkdtemp()) / 'job'
    def prepare(self,s,stage): pass
    def run(self,s,stage): pass
    def receive(self,s,stage='write'): pass
    def review(self,s,stage='review'): pass
    def finalize(self,s,stage='review'): pass

class DailyTests(unittest.TestCase):
    def make(self, n=6):
        d=Path(tempfile.mkdtemp()); (d/'production').mkdir()
        posts=[{'symbol':f'S{i}','published_date':'2026-09-10'} for i in range(n)]
        (d/'production/posts.json').write_text(json.dumps(posts)); (d/'sources.json').write_text('{}'); (d/'production-engine-export.json').write_text('{}')
        for p in posts: (d/'production'/p['symbol']).mkdir()
        return d
    def test_repeat_resumes_without_new_calls(self):
        d=self.make()
        with patch('subscription_daily.Edition',FakeEdition):
            a=DailyController(d,'2026-09-10',max_new_model_jobs=20).run(); b=DailyController(d,'2026-09-10',max_new_model_jobs=20).run()
        self.assertEqual(a['status'],'awaiting_visual_review'); self.assertEqual(b['status'],a['status'])
    def test_malformed_six_is_rejected(self):
        with self.assertRaises(ValueError): DailyController(self.make(5),'2026-09-10').run()
    def test_mutated_input_is_rejected(self):
        d=self.make()
        with patch('subscription_daily.Edition',FakeEdition): DailyController(d,'2026-09-10',max_new_model_jobs=0).run()
        (d/'sources.json').write_text('{"changed":true}')
        with self.assertRaises(ValueError): DailyController(d,'2026-09-10').run()
    def test_failure_is_held(self):
        class Broken(FakeEdition):
            def run(self,*a): raise RuntimeError('model unavailable')
        d=self.make()
        with patch('subscription_daily.Edition',Broken):
            self.assertEqual(DailyController(d,'2026-09-10').run()['status'],'failed_needs_review')
    def test_duplicate_lock(self):
        d=self.make(); c=DailyController(d,'2026-09-10'); c.lock.mkdir()
        with self.assertRaises(RuntimeError): c.run()

if __name__=='__main__': unittest.main()
