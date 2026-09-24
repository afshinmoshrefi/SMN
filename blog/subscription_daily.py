"""Bounded, resumable controller for one immutable six-article SMN Dev edition."""
from __future__ import annotations
import argparse, hashlib, json, os, re, socket, subprocess
from datetime import datetime, timezone
from pathlib import Path
from engine_edition_workflow import Edition
from subscription_writer import load_json, save_json

STAGES=('prepare','write','receive','review','finalize')
RESULT_FILES=('article.json','article.html','mechanical-checks.json','bundle.json','review-binding.json')
def now(): return datetime.now(timezone.utc).isoformat()
def digest(path):
    p=Path(path)
    if not p.exists() or p.is_symlink(): raise ValueError('missing or unsafe input')
    h=hashlib.sha256()
    if p.is_file(): h.update(p.read_bytes())
    else:
        for f in sorted(p.rglob('*')):
            if f.is_symlink(): raise ValueError('missing or unsafe input')
            if f.is_file(): h.update(str(f.relative_to(p)).encode()); h.update(f.read_bytes())
    return h.hexdigest()

class DailyController:
    def __init__(self,root,date,codex=None,max_new_model_jobs=2,source_commit=None,provider='astra',claude=None):
        self.root=Path(root).resolve();self.date=date;self.codex=codex;self.provider=provider;self.claude=claude;self.max_new=max_new_model_jobs;self.source_commit=source_commit
        self.state_path=self.root/'daily-state.json';self.lock=self.root/'.daily-lock'
    def _commit(self):
        value=self.source_commit
        if value is None:
            try:value=subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.root,text=True).strip()
            except (OSError,subprocess.CalledProcessError) as exc: raise ValueError('source commit is required outside a git checkout') from exc
        if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{40}',value):raise ValueError('source commit must be a 40-character lowercase SHA')
        return value
    def _inputs(self):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',self.date):raise ValueError('invalid edition date')
        try: datetime.strptime(self.date,'%Y-%m-%d')
        except ValueError: raise ValueError('invalid edition date')
        prod=self.root/'production';posts=load_json(prod/'posts.json');rows=[p for p in posts if p.get('published_date','')[:10]==self.date]
        if len(rows)!=6 or len({p.get('symbol') for p in rows})!=6 or any(not re.fullmatch(r'[A-Z0-9]{1,12}',p.get('symbol','')) for p in rows):raise ValueError('expected exactly six distinct production subjects for requested date')
        paths={'sources':self.root/'sources.json','production_posts':prod/'posts.json','engine_export':self.root/'production-engine-export.json'}
        paths.update({'production/'+p['symbol']:prod/p['symbol'] for p in rows})
        return rows,{name:digest(path) for name,path in paths.items()}
    def _claim(self):
        try:self.lock.mkdir()
        except FileExistsError:raise RuntimeError('daily edition is already claimed; inspect lock and recover explicitly')
        save_json(self.lock/'owner.json',{'pid':os.getpid(),'host':socket.gethostname(),'utc':now()})
    def _release(self):
        (self.lock/'owner.json').unlink(missing_ok=True);self.lock.rmdir()
    def _outputs(self,sym):
        out=self.root/'results'/sym
        return {(out/n).relative_to(self.root).as_posix():digest(out/n) for n in RESULT_FILES if (out/n).exists()}
    def _verify_outputs(self,checks):
        for rel,expected in checks.get('outputs',{}).items():
            if digest(self.root/rel)!=expected:raise ValueError('checkpoint output changed')
    def _hold(self,state,checks,stage,exc):
        checks['error']={'class':type(exc).__name__,'stage':stage};state['status']='failed_needs_review';state['automatic_retry']=False;save_json(self.state_path,state);return self.summary(state)
    def _receipt_ok(self,job):
        receipt=job/'receipt.json'
        if not receipt.exists():return False
        expected=load_json(receipt).get('output_sha256')
        if not re.fullmatch(r'[0-9a-f]{64}',str(expected)) or digest(job/'output.json')!=expected:raise ValueError('receipt output changed')
        return True
    def run(self):
        if not isinstance(self.max_new,int) or self.max_new<0:raise ValueError('max-new-model-jobs must be nonnegative')
        self._claim()
        try:
            rows,hashes=self._inputs();symbols=[p['symbol'] for p in rows];commit=self._commit()
            if self.state_path.exists():
                state=load_json(self.state_path)
                if state.get('status')=='failed_needs_review':return {'date':self.date,'status':'held_until_new_attempt'}
                if state.get('date')!=self.date or state.get('source_commit')!=commit or state.get('input_hashes')!=hashes or state.get('symbols')!=symbols:raise ValueError('prepared input or source changed; use a new edition attempt directory')
            else:
                state={'version':2,'date':self.date,'source_commit':commit,'symbols':symbols,'input_hashes':hashes,'stages':{},'status':'ready','publish':False};save_json(self.state_path,state)
            edition=Edition(self.root,self.date,self.codex) if self.provider=='astra' else Edition(self.root,self.date,self.codex,provider=self.provider,claude=self.claude);created=0
            for sym in symbols:
                checks=state['stages'].setdefault(sym,{})
                try:self._verify_outputs(checks)
                except Exception as exc:return self._hold(state,checks,'checkpoint',exc)
                for stage in STAGES:
                    if checks.get(stage,{}).get('status')=='done':continue
                    try:
                        if stage=='prepare':
                            job=edition.job(sym,'write')
                            if not job.exists():
                                out=self.root/'results'/sym
                                if out.exists() and any(out.iterdir()):raise ValueError('partial prepare exists without immutable job')
                                edition.prepare(sym,'write')
                        elif stage=='write':
                            if not self._receipt_ok(edition.job(sym,'write')):
                                if created>=self.max_new:
                                    state['status']='budget_exhausted';save_json(self.state_path,state);return self.summary(state)
                                edition.run(sym,'write');created+=1
                        elif stage=='receive':
                            checks_result=edition.receive(sym)
                            # Hold before spending a review job on a draft that failed mechanical checks.
                            if isinstance(checks_result,dict) and checks_result.get('passed') is False:
                                raise ValueError('mechanical checks failed; repair the draft before review')
                        elif stage=='review':
                            job=edition.job(sym,'review')
                            if not job.exists():edition.review(sym)
                            if not self._receipt_ok(job):
                                if created>=self.max_new:
                                    state['status']='budget_exhausted';save_json(self.state_path,state);return self.summary(state)
                                edition.run(sym,'review');created+=1
                        else:edition.finalize(sym)
                        checks[stage]={'status':'done'};checks['outputs']=self._outputs(sym);save_json(self.state_path,state)
                    except Exception as exc:return self._hold(state,checks,stage,exc)
            state['status']='awaiting_visual_review';save_json(self.state_path,state);return self.summary(state)
        finally:self._release()
    def summary(self,state):return {'date':self.date,'status':state['status'],'symbols':state['symbols'],'completed':{s:[x for x in STAGES if c.get(x,{}).get('status')=='done'] for s,c in state['stages'].items()}}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--date',required=True);ap.add_argument('--codex',type=Path);ap.add_argument('--max-new-model-jobs',type=int,default=2);ap.add_argument('--source-commit');ap.add_argument('--provider',choices=['astra','claude'],default='astra');ap.add_argument('--claude',type=Path)
    a=ap.parse_args();print(json.dumps(DailyController(a.root,a.date,a.codex,a.max_new_model_jobs,a.source_commit,a.provider,a.claude).run()))
