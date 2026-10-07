"""One SMN Dev edition as a fixed script workflow (no AI agent directs the run).

    capture -> research -> write -> checks -> (repair) -> review -> (repair, re-review)
    -> finalize -> layout -> screenshot check + hero check -> publish -> live check

Models come from named Claude or ChatGPT profiles. Every step is resumable from files in the edition root; rerunning the
same command continues where it stopped. Transient failures retry a bounded number
of times; anything else writes HOLD.json with the exact step and exits 2.

    python smn_daily.py --root /var/lib/tradewave/smn-daily/2026-09-25 --date 2026-09-25 [--publish]
"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

import smn_models
import smn_primary_sources
import smn_research
import smn_visual
import subscription_capture as capture
from subscription_publication import CHECKS
from subscription_writer import load_json, save_json, sha256

BLOG = Path(__file__).resolve().parent
CLIS = {'claude': os.environ.get('SMN_CLAUDE', '/root/.local/bin/claude'), 'codex': os.environ.get('SMN_CODEX')}
PLAYWRIGHT = os.environ.setdefault('SMN_PLAYWRIGHT', '/opt/smn-playwright/node_modules/playwright')
os.environ.setdefault('SMN_BROWSER_CHANNEL', 'bundled')
# Passing faults: a login-token refresh race, rate limits, provider overload, network drops.
# They clear within minutes, but every retry still consumes an attempt reservation.
TRANSIENT = re.compile(r'OAuth token|rate.?limit|overloaded|\b(429|500|502|503|529)\b|temporar|'
                       r'ECONNRESET|ETIMEDOUT|timed? ?out', re.I)
TRANSIENT_WAITS = (60, 180, 420)
ATTEMPT_FILES = ('state.json', 'diagnostic.log', 'result.json', 'turn-usage.json', 'usage-before.json',
                 'invocation.json', 'output.json', 'events.jsonl', 'usage-after.json',
                 'execution.json','managed-termination.json')


class Hold(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def log(**kw):
    print(json.dumps({'utc': now(), **kw}), flush=True)


def retry(step, fn, tries=3, wait=30):
    for n in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:
            log(step=step, attempt=n, error=str(exc)[:300])
            if n == tries:
                raise Hold('%s failed after %d attempts: %s' % (step, tries, exc))
            time.sleep(wait * n)


class Day:
    def __init__(self, root, date, models=None, max_jobs=40, profile='claude', roles=None,
                 publication_origin=None):
        self.root = Path(root).resolve()
        self.date = date
        self.models = models
        self.profile = profile
        self.publication_origin = publication_origin
        self.roles = roles if roles is not None else smn_models.load(models, profile)
        self.max_jobs = max_jobs
        self.state_path = self.root/'smn-daily-state.json'
        self.state = load_json(self.state_path) if self.state_path.exists() else {
            'date': date, 'created': now(), 'profile': profile if models is None else 'custom',
            'roles': self.roles, 'articles': {}, 'publication_origin': publication_origin}
        if self.state.get('date') != date or self.state.get('roles') != self.roles:
            raise Hold('Edition date or model roles changed; resume with the original profile/settings')
        if self.state.get('publication_origin') != publication_origin:
            raise Hold('Publication target changed; preserve the original edition')

    def save(self):
        save_json(self.state_path, self.state)

    # ---- model jobs -------------------------------------------------
    def jobs_used(self):
        from model_job_evidence import jobs_used
        return jobs_used(self.root)

    def _archive(self, job, exc, kind):
        """Move a failed attempt aside and mark the job ready to run again."""
        from smn_recovery import MAX_JOB_ATTEMPTS, attempts
        if (job/'.claim').exists() or (job/'receipt.json').exists():
            raise Hold('Running or completed model effect must be reconciled before retry')
        if attempts(job)>=MAX_JOB_ATTEMPTS or self.jobs_used()>=self.max_jobs:
            save_json(job/'retry-exhausted.json',{'utc':now(),'per_job_limit':MAX_JOB_ATTEMPTS,
                      'jobs_used':self.jobs_used(),'max_jobs':self.max_jobs,
                      'reason':str(exc)[:500],'automatic_retry':False})
            raise Hold('Per-job or cumulative model-job budget exhausted; retain the failed attempt')
        n = len(list(job.glob(kind + '-*'))) + 1
        archive = job/('%s-%d' % (kind, n))
        archive.mkdir()
        for name in ATTEMPT_FILES:
            if (job/name).exists():
                shutil.move(str(job/name), archive/name)
        save_json(job/'state.json', {'status': 'ready', 'utc': now(), 'requeued_after': str(exc)[:300]})
        log(step='requeue', job=job.name, kind=kind, error=str(exc)[:300])

    def run_job(self, job):
        """Run a prepared job. Passing faults are waited out (TRANSIENT_WAITS); any
        other failure gets one requeue, and a second one holds the article."""
        job = Path(job)
        if getattr(self,'continuity',False) and (job/'execution.json').is_file():
            from smn_recovery import reconcile_private_job
            reconcile_private_job(job)
        if (job/'receipt.json').exists():
            from model_job_evidence import completed_receipt
            try:
                return completed_receipt(job)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise Hold('Saved model receipt needs investigation: '+str(exc)) from exc
        if (job/'retry-exhausted.json').exists():
            raise Hold('Per-job or cumulative model-job budget exhausted; retained terminal attempt')
        waits = list(TRANSIENT_WAITS)
        while True:
            # A scheduler retry must not archive another worker's running job,
            # or turn an uncertain completion into a second provider dispatch.
            if (job/'.claim').exists():
                raise Hold('model job %s has an active or unresolved claim; preserve it for explicit recovery' % job.name)
            if (job/'state.json').exists():
                try:
                    state = load_json(job/'state.json')
                    if state.get('status') != 'ready':
                        raise Hold('model job %s is not ready; preserve it for explicit recovery' % job.name)
                    if state.get('retry_after_utc') and datetime.now(timezone.utc)<datetime.fromisoformat(state['retry_after_utc']):
                        raise Hold('Temporary retry backoff for model job '+job.name)
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    raise Hold('model job state needs investigation: '+str(exc)) from exc
            if self.jobs_used() > self.max_jobs:
                raise Hold('model-job budget of %d exhausted' % self.max_jobs)
            try:
                return smn_models.run(job, CLIS)
            except Exception as exc:
                if (job/'receipt.json').exists():
                    raise Hold('model job %s failed after its receipt: %s' % (job.name, exc))
                if getattr(self,'continuity',False):
                    from datetime import timedelta
                    from smn_recovery import BACKOFF_MINUTES, attempts, classify
                    failure=classify(exc)
                    if not failure['automatic_retry_allowed']:
                        raise Hold(str(exc)) from exc
                    self._archive(job,exc,'transient-attempt' if failure['category']=='transient' else 'failed-attempt')
                    ready=load_json(job/'state.json')
                    ready['retry_after_utc']=(datetime.now(timezone.utc)+timedelta(minutes=BACKOFF_MINUTES[min(attempts(job)-2,2)])).isoformat()
                    ready['failure']=failure;save_json(job/'state.json',ready)
                    # Yield to the next article, rather than sleeping through it.
                    raise Hold('Temporary retry backoff: '+str(exc)) from exc
                if TRANSIENT.search(str(exc)) and waits:
                    self._archive(job, exc, 'transient-attempt')
                    time.sleep(waits.pop(0))
                    continue
                if list(job.glob('failed-attempt-*')):
                    raise Hold('model job %s failed twice: %s' % (job.name, exc))
                self._archive(job, exc, 'failed-attempt')
                time.sleep(60)

    def release_transient_holds(self):
        """A rerun (the 06:00 timer) retries articles held only by a passing fault."""
        for sym, s in self.state['articles'].items():
            if s.get('finalized') or not TRANSIENT.search((s.get('held') or {}).get('reason', '')):
                continue
            if getattr(self,'continuity',False):
                # Reconcile ownership, saved completion and budgets per subject.
                # A capped or uncertain subject must not abort independent work.
                from smn_recovery import repair_held_articles
                repair_held_articles(self,datetime.now(timezone.utc))
                return
            for job in (self.root/'jobs').glob('%s-%s-*' % (sym, self.date.replace('-', ''))):
                state = load_json(job/'state.json') if (job/'state.json').exists() else {}
                if state.get('status') == 'failed_needs_review' and TRANSIENT.search(state.get('reason', '')):
                    self._archive(job, state.get('reason', ''), 'transient-attempt')
            del s['held']
            self.save()
            log(step='article_released', symbol=sym)

    def prepare_editorial_recovery(self, sym, *, controller_owned=False):
        """Explicitly permit one source-backed repair after a second failed review."""
        from engine_edition_workflow import Edition
        from editorial_gate import primary_sources
        controller_root = self.root.parent.parent
        last_run_path = controller_root/'last-run.json'
        last_run = load_json(last_run_path) if last_run_path.exists() else {}
        if not controller_owned and last_run.get('date') == self.date and last_run.get('status') == 'running':
            raise Hold('Daily controller is still running; preserve its final receipt before recovery')
        controller_lock = controller_root/'controller.lock'
        if not controller_owned and os.name == 'posix' and controller_lock.exists():
            import fcntl
            with controller_lock.open('rb') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Hold('Daily controller lock is held; wait for its completion') from exc
                finally:
                    try: fcntl.flock(handle, fcntl.LOCK_UN)
                    except OSError: pass
        ledger = self.root/('editorial-recovery-'+sym+'.json')
        if controller_owned and (not getattr(self,'continuity',False) or self.jobs_used()+2>self.max_jobs):
            raise Hold('Automatic source-backed repair requires continuity ownership and two reserved slots')
        if ledger.exists():
            raise Hold('Editorial recovery already prepared for '+sym)
        if any((self.root/'jobs'/(sym+'-'+self.date.replace('-','')+'-'+stage)).exists()
               for stage in ('repair-review-three','third-review')):
            raise Hold('Editorial recovery stages already exist for '+sym)
        state = self.state['articles'].get(sym, {})
        if (state.get('finalized') or state.get('review_stage') != 'rereview' or
                state.get('draft') != 'repair-two' or state.get('mechanical_ok') is not True or
                'failed review twice' not in (state.get('held') or {}).get('reason', '')):
            raise Hold('Only a held second-review failure permits editorial recovery')
        review_job = self.root/'jobs'/(sym+'-'+self.date.replace('-','')+'-rereview')
        review_path = review_job/'output.json'
        receipt = load_json(review_job/'receipt.json')
        if receipt.get('output_sha256') != sha256(review_path.read_bytes()):
            raise Hold('Second-review receipt or output changed')
        review = load_json(review_path)
        if review.get('passed') is not False or not review_problems(review):
            raise Hold('Second review does not contain repairable issues')
        primary_sources(self.root,sym,self.date)
        issues = self._issues(sym+'-review-three.txt',review_problems(review))
        ed = Edition(self.root,self.date,provider='config',claude=CLIS['claude'],
                     roles=self.roles,publication_origin=self.publication_origin or 'https://smn-dev.trxstat.com')
        ed.repair(sym,issues,'repair-review-three')
        approved = {}
        for other, row in self.state['articles'].items():
            if other == sym or not row.get('finalized') or row.get('held'):
                continue
            approved[other] = self._approved_snapshot(other)
        save_json(ledger, {'utc':now(),'symbol':sym,'prior_state':state.copy(),
                           'actor':'SMN continuity recovery' if controller_owned else 'explicit operator recovery',
                           'authorization':'continuity-v1 bounded source-backed repair' if controller_owned else 'explicit operator request',
                           'prior_controller_last_run_sha256':sha256(last_run_path.read_bytes()) if last_run_path.exists() else None,
                           'prior_controller_last_run':last_run,
                           'second_review_sha256':sha256(review_path.read_bytes()),
                           'second_review_receipt_sha256':sha256((review_job/'receipt.json').read_bytes()),
                           'repair_issues_sha256':sha256(issues.read_bytes()),
                           'approved_articles':approved,'new_stages':['repair-review-three','third-review'],
                           'prepared_without_model_jobs':True})
        state.update(draft='repair-review-three',mechanical_ok=False,review_stage='third-review')
        state.pop('held',None)
        self.save()
        return ledger

    def _approved_snapshot(self, sym):
        files = {}
        results = self.root/'results'/sym
        jobs = self.root/'jobs'
        paths = list(results.rglob('*')) + [p for job in jobs.glob(sym+'-'+self.date.replace('-','')+'-*')
                                                  for p in job.rglob('*')]
        for path in paths:
            if path.is_symlink():
                raise Hold('Approved evidence contains a symlink: '+str(path))
            if path.is_file():
                files[path.relative_to(self.root).as_posix()] = sha256(path.read_bytes())
        return files

    def verify_editorial_recovery(self):
        for ledger_path in self.root.glob('editorial-recovery-*.json'):
            ledger = load_json(ledger_path)
            for sym, files in ledger['approved_articles'].items():
                if self._approved_snapshot(sym) != files:
                    raise Hold('Approved article changed during editorial recovery: '+sym)

    # ---- steps -----------------------------------------------------
    def capture(self):
        result = retry('capture_production', lambda: capture.production(self.root, self.date))
        if result.get('status') == 'waiting_for_production':
            return False
        retry('capture_engine', lambda: capture.engine(self.root, self.date, 'engine'))
        self.symbols = [p['symbol'] for p in load_json(self.root/'production/posts.json')]
        return True

    def research(self):
        target = self.root/'sources.json'
        folder = self.root/'research'
        if target.exists() and all((folder/(sym + '.json')).exists() or
                self.state['articles'].get(sym, {}).get('held') for sym in self.symbols):
            if not getattr(self, 'continuity', False) or all(
                    sym in load_json(target) for sym in self.symbols if (folder/(sym + '.json')).exists()):
                return
        example = load_json(BLOG/'examples/subscription-sources-20260923.json')
        folder.mkdir(exist_ok=True)
        for sym in self.symbols:
            if self.state['articles'].get(sym, {}).get('held') or (folder/(sym + '.json')).exists():
                continue
            try:
                smn_primary_sources.collect(self.root, self.date, [sym], self.roles, CLIS, self.run_job)
                from editorial_gate import primary_sources
                primary_sources(self.root,sym,self.date)
                other = next(v for k, v in example.items() if k != sym)
                entry, problems = self._research_job(sym, other, 'research')
                if problems:
                    entry, problems = self._research_job(sym, other, 'research-two', problems)
                if problems:
                    raise Hold('research for %s failed checks twice: %s' % (sym, '; '.join(problems)))
            except Exception as exc:
                # Hold only this article; the others still get written.
                self.state['articles'].setdefault(sym, {})['held'] = {'utc': now(), 'reason': str(exc)[:500]}
                self.save()
                log(step='article_held', symbol=sym, reason=str(exc)[:300])
                continue
            save_json(folder/(sym + '.json'), smn_research.to_sources(entry))
            log(step='research', symbol=sym, sources=len(entry['sources']))
        ready = [s for s in self.symbols if (folder/(s + '.json')).exists()]
        if not ready:
            raise Hold('research failed for every article')
        sources = load_json(target) if getattr(self, 'continuity', False) and target.exists() else {}
        sources.update({sym: load_json(folder/(sym + '.json')) for sym in ready})
        save_json(target, sources)

    def _research_job(self, sym, example, stage, issues=None):
        job = self.root/'jobs'/(sym + '-' + self.date.replace('-', '') + '-' + stage)
        if not job.exists():
            # Prepare here and run through the budgeted runner.
            from datetime import timedelta
            from subscription_writer import sha256
            prompt = smn_research.RULES + '\n' + smn_research.evidence(self.root, self.date, sym, example)
            if issues:
                prompt += '\nYOUR PREVIOUS ANSWER HAD THESE PROBLEMS. Fix every one:\n' + '\n'.join(issues)
            until = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()
            smn_models.prepare(self.roles, 'research', self.root/'jobs', job.name, prompt, smn_research.SCHEMA,
                               as_of=self.date, valid_until=until, evidence_sha256=sha256(prompt.encode()),
                               stage=stage)
        self.run_job(job)
        entry = load_json(job/'output.json')
        problems = smn_research.check(entry, self.root, self.date, sym)
        save_json(job/'research-check.json', {'passed': not problems, 'problems': problems})
        return entry, problems

    def articles(self):
        from engine_edition_workflow import Edition
        ed = Edition(self.root, self.date, provider='config', claude=CLIS['claude'],
                     roles=self.roles, publication_origin=self.publication_origin or 'https://smn-dev.trxstat.com')
        ed.clis = CLIS
        for sym in self.symbols:
            s = self.state['articles'].setdefault(sym, {})
            if s.get('held'):
                continue
            try:
                self._article(ed, sym)
            except Exception as exc:
                # Preserve successful work, but publish only a complete edition.
                s['finalized'] = False
                s['held'] = {'utc': now(), 'reason': str(exc)[:500]}
                self.save()
                log(step='article_held', symbol=sym, reason=str(exc)[:300])
        self.done = [s for s in self.symbols if not self.state['articles'].get(s, {}).get('held') and
                     (self.state['articles'].get(s, {}).get('editorially_finalized') or self.state['articles'].get(s, {}).get('finalized'))]
        if not self.done:
            raise Hold('no article passed')

    def _issues(self, name, lines):
        path = self.root/'issues'/name
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        elif path.read_text(encoding='utf-8') != '\n'.join(lines) + '\n':
            raise Hold('Saved repair request differs; preserve the attempt and create an explicit evidence revision')
        return path

    def _receive_article_draft(self,ed,sym,s):
        draft=s.get('draft','write')
        checks=ed.receive(sym,draft)
        if not checks['passed'] and draft=='repair-two':
            issues=self._issues(sym+'-review-mechanical.txt',mechanical_problems(checks))
            if not ed.job(sym,'repair-three').exists():
                if self.jobs_used() >= self.max_jobs:
                    raise Hold('model-job budget of %d exhausted' % self.max_jobs)
                ed.repair(sym,issues,'repair-three')
            self.run_job(ed.job(sym,'repair-three'))
            # Persist before receive replaces article bytes; resume the new draft,
            # and require review of it rather than reusing the prior draft's approval.
            s.update(draft='repair-three',mechanical_ok=False,review_stage='rereview')
            self.save()
            checks=ed.receive(sym,'repair-three')
        return checks

    def _article(self, ed, sym):
        from editorial_gate import verify_complete, verify_review
        s = self.state['articles'].setdefault(sym, {})
        if s.get('finalized'):
            verify_complete(ed.result(sym))
            return
        if s.get('editorially_finalized'):
            verify_review(ed.result(sym),ed.job(sym,s['review_stage'])/'output.json')
            return
        if not ed.job(sym, 'write').exists():
            ed.prepare(sym, 'write')
        self.run_job(ed.job(sym, 'write'))
        if s.get('mechanical_ok'):
            from visual_evidence import digest
            m=load_json(ed.result(sym)/'mechanical-checks.json')
            if not m.get('passed') or m.get('article_sha256')!=digest(load_json(ed.result(sym)/'article.json')) or m.get('evidence_sha256')!=load_json(ed.result(sym)/'bundle.json')['evidence_sha256']:
                raise Hold('Saved mechanical approval is stale; explicit revision required')
        if not s.get('mechanical_ok'):
            draft = s.get('draft', 'write')
            if draft == 'repair-review-three' and s.get('review_stage') == 'third-review':
                self.run_job(ed.job(sym,draft))
            checks = self._receive_article_draft(ed,sym,s)
            if not checks['passed'] and draft == 'write':
                issues = self._issues(sym + '-mechanical.txt', mechanical_problems(checks))
                if not ed.job(sym, 'repair').exists():
                    ed.repair(sym, issues, 'repair')
                self.run_job(ed.job(sym, 'repair'))
                s['draft'] = 'repair'
                self.save()
                checks = ed.receive(sym, 'repair')
            if not checks['passed']:
                raise Hold('%s mechanical checks still fail after one repair: %s'
                           % (sym, '; '.join(mechanical_problems(checks))))
            s['mechanical_ok'] = True
            self.save()
        stage = s.get('review_stage', 'review')
        if not ed.job(sym, stage).exists():
            ed.review(sym, stage)
        self.run_job(ed.job(sym, stage))
        stage = self._review_binding_retry(ed,sym,stage)
        review = load_json(ed.job(sym, stage)/'output.json')
        problems = editorial_review_problems(ed,sym,stage)
        if problems:
            if (stage in {'rereview','third-review','reinspect-review'} or
                stage=='binding-review' and s.get('binding_review_origin')=='rereview'):
                raise Hold('%s failed %s: %s' % (sym,'editorial recovery review' if stage=='third-review' else 'review twice',problems))
            issues = self._issues(sym + '-review.txt', problems)
            if not ed.job(sym, 'repair-two').exists():
                ed.repair(sym, issues, 'repair-two')
            self.run_job(ed.job(sym, 'repair-two'))
            # Persist the next draft/stage before replacing article bytes so a crash
            # resumes its mechanical check, never the approval of the prior draft.
            s.update(draft='repair-two', mechanical_ok=False, review_stage='rereview')
            self.save()
            checks = self._receive_article_draft(ed,sym,s)
            if not checks['passed']:
                raise Hold('%s review repair mechanical checks still fail after one correction: %s'
                           % (sym,'; '.join(mechanical_problems(checks))))
            s['mechanical_ok'] = True
            stage = 'rereview'
            self.save()
            if not ed.job(sym, stage).exists():
                ed.review(sym, stage)
            self.run_job(ed.job(sym, stage))
            stage = self._review_binding_retry(ed,sym,stage)
            review = load_json(ed.job(sym, stage)/'output.json')
            problems = editorial_review_problems(ed,sym,stage)
            if problems:
                raise Hold('%s failed review twice: %s' % (sym, problems))
        ed.finalize(sym, stage)
        s['editorially_finalized'] = True
        s['finalized'] = False
        s['review_stage'] = stage
        self.save()
        log(step='article', symbol=sym, review_stage=stage)

    def _review_binding_retry(self,ed,sym,stage):
        """One independent receipt repair for a passed review with an invalid quote."""
        from editorial_gate import ReviewEvidenceError, verify_review
        s=self.state['articles'][sym]
        if stage not in {'review','rereview'} or s.get('binding_review_origin'):
            return stage
        try:
            verify_review(ed.result(sym),ed.job(sym,stage)/'output.json')
        except ReviewEvidenceError:
            retry_stage='binding-review'
            if not ed.job(sym,retry_stage).exists():
                if self.jobs_used()>=self.max_jobs:
                    raise Hold('model-job budget of %d exhausted before review reinspection' % self.max_jobs)
                ed.review(sym,retry_stage)
            s.update(binding_review_origin=stage,review_stage=retry_stage)
            self.save()
            self.run_job(ed.job(sym,retry_stage))
            return retry_stage
        except Exception:
            pass  # Content, custody and source errors use the existing hard-failure path.
        return stage

    def _release_inconsistent_visual_holds(self):
        from editorial_gate import verify_review
        from subscription_writer import sha256
        for sym in self.symbols:
            state=self.state['articles'].get(sym,{})
            if (state.get('held') or {}).get('reason')!='screenshot check failed':continue
            out=self.root/'results'/sym
            try:
                smn_visual.verify_inconsistent_visual_hold(self.root,sym)
                held=out/'review-binding.held.json';active=out/'review-binding.json'
                binding=load_json(held if held.exists() else active)
                stage=binding['review_stage']
                if not isinstance(stage,str) or not re.fullmatch('[a-z-]+',stage):
                    raise ValueError('Held review stage invalid')
                proof=verify_review(out,self.root/'jobs'/(sym+'-'+self.date.replace('-','')+'-'+stage)/'output.json')
                if binding.get('editorial_audit')!=proof or binding.get('article_html_sha256')!=sha256((out/'article.html').read_bytes()):
                    raise ValueError('Held editorial binding changed')
                if held.exists():
                    if active.exists():
                        if active.read_bytes()!=held.read_bytes():raise ValueError('Conflicting held review bindings')
                    else:held.rename(active)
                state.pop('held');state['editorially_finalized']=True;state['finalized']=False
                self.save()
            except (OSError,ValueError,KeyError,TypeError) as exc:
                log(step='visual_hold_retained',symbol=sym,reason=str(exc))

    def visual(self):
        from editorial_gate import verify_review, verify_complete
        self._release_inconsistent_visual_holds()
        done = [s for s in self.symbols if not self.state['articles'].get(s,{}).get('held') and
                (self.state['articles'].get(s,{}).get('editorially_finalized') or self.state['articles'].get(s,{}).get('finalized'))]
        missing = [s for s in done if not (self.root/'results'/s/'layout-checks.json').exists()]
        if missing:
            try:
                retry('layout', lambda: subprocess.run(['node', str(BLOG/'subscription_layout.cjs'), str(self.root),
                      *missing], check=True, cwd=BLOG,timeout=120))
            except Exception:
                if not getattr(self,'continuity',False):raise
                # Delivery may use the existing content-bound presentation fallback.
                # It still validates essential assets and browser checks, never a fake vision pass.
                return
        for sym in done:
            out = self.root/'results'/sym
            if not (out/'hero-check.json').exists():
                smn_visual.hero(self.root, self.date, sym, self.roles, CLIS, self.run_job)
            try:
                binding=load_json(out/'review-binding.json')
                verify_review(out,self.root/'jobs'/(sym+'-'+self.date.replace('-','')+'-'+binding['review_stage'])/'output.json')
                record = (smn_visual.verify_article_visual(self.root,sym) if (out/'visual-checks.json').exists() else
                          smn_visual.article(self.root, self.date, sym, self.roles, CLIS, self.run_job,max_jobs=self.max_jobs))
                if record['passed']:
                    proof=verify_complete(out)
                    self.state['articles'][sym]['finalized']=True
                    save_json(out/'completion-check.json',proof)
                    self.save()
            except Exception as exc:
                record={'passed':False,'reason':str(exc)}
            if not record['passed']:
                self.state['articles'][sym]['held'] = {'utc': now(), 'reason': record.get('reason','screenshot check failed')}
                self.state['articles'][sym]['finalized'] = False
                # Remove the final binding so the publisher skips this article.
                binding = out/'review-binding.json'
                if binding.exists(): binding.rename(out/'review-binding.held.json')
                self.save()
                log(step='article_held', symbol=sym, reason='screenshot check failed')

    def check(self):
        """Overall day check: every captured article must pass; a held one fails the day."""
        rows = {}
        for sym in self.symbols:
            s = self.state['articles'].get(sym, {})
            if s.get('held') and s.get('finalized'):
                s['finalized'] = False
                self.save()
            if s.get('finalized') and not s.get('held'):
                try:
                    from editorial_gate import verify_complete
                    verify_complete(self.root/'results'/sym)
                except Exception as exc:
                    s['finalized']=False
                    s['held']={'utc':now(),'reason':str(exc)}
                    self.save()
            rows[sym] = ({'status': 'passed'} if s.get('finalized') and not s.get('held') else
                         {'status': 'held', 'reason': (s.get('held') or {}).get('reason', 'not finished')})
        held = sorted(k for k, v in rows.items() if v['status'] == 'held')
        record = {'utc': now(), 'date': self.date, 'passed': not held, 'articles': rows,
                  'passed_count': len(rows) - len(held), 'held': held, 'jobs_used': self.jobs_used()}
        save_json(self.root/'daily-check.json', record)
        log(step='daily_check', passed=record['passed'], passed_count=record['passed_count'], held=held)
        return record

    def publish(self, repo):
        import subscription_primary_publish as primary
        if (self.root/'dev-publication-receipt.json').exists():
            return load_json(self.root/'dev-publication-receipt.json')
        if not (self.root/'primary-stage.json').exists():
            primary.stage(self.root, Path(repo))
        from subscription_publication import validate_staged_reviews
        activation_attempted=any((self.root/name).exists() for name in
                                 ('primary-activation-attempt.json','primary-activation.json','live-verification.json'))
        try:
            validate_staged_reviews(self.root)
            if not (self.root/'live-verification.json').exists():
                primary.activate(self.root, Path(repo), 'node', PLAYWRIGHT)
                activation_attempted=True
            if not (self.root/'live-landing-visual-checks.json').exists():
                record = smn_visual.landing(self.root, self.date, self.roles, CLIS, self.run_job)
                if not record['passed']:
                    raise Hold('live landing check failed: %s' % record['defects'])
            return primary.finish(self.root, Path(repo))
        except BaseException as exc:
            if activation_attempted or (self.root/'primary-activation-attempt.json').exists():
                try:primary.call(load_json(self.root/'primary-stage.json'),'rollback')
                except Exception as rollback_error:
                    raise Hold('Publication failed (%s); rollback also failed (%s)' % (exc,rollback_error)) from exc
            raise


def mechanical_problems(checks):
    out = []
    if not checks['structure'].get('passed'):
        out.append('Structure check failed: ' + json.dumps(checks['structure'])[:600])
    for sid, row in checks.get('source_words', {}).items():
        if not row.get('passed'):
            out.append('Source %s totals %d derived words (prose %d + chart %d + headings/citation %d) against its '
                       '%d-word cap across all surfaces. Cut prose derived from %s to at most %d words. Keep other '
                       'sources within their caps; do not change the TradeWave study, charts or facts.'
                       % (sid, row['total'], row['prose'], row['chart'], row['headings_and_citation'], row['maximum'],
                          sid, max(20, row['maximum'] - row['chart'] - row['headings_and_citation'] - 40)))
    return out or ['Mechanical checks failed: ' + json.dumps(checks)[:600]]


def review_passed(r):
    from editorial_gate import hard_review_passed
    return hard_review_passed(r)


def editorial_review_problems(ed,sym,stage):
    from editorial_gate import verify_review
    try:
        verify_review(ed.result(sym),ed.job(sym,stage)/'output.json')
        return []
    except Exception as exc:
        return [str(exc)]


def review_problems(r):
    out = ['Failed check %s: %s' % (k, json.dumps(v)[:400]) for k, v in r.get('checks', {}).items()
           if v.get('passed') is not True]
    out += ['%s at %s: %s Suggested: %s' % (i.get('severity'), i.get('location'), i.get('problem'),
            i.get('suggested_change')) for i in r.get('issues', []) if i.get('severity') in {'major', 'blocker'}]
    return out or ['Reviewer did not pass the article: ' + json.dumps(r)[:600]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--date', required=True)
    model_group = ap.add_mutually_exclusive_group()
    model_group.add_argument('--profile', choices=smn_models.PROFILES, default='claude',
                             help='subscription provider profile (default claude)')
    model_group.add_argument('--models', type=Path, help='custom role settings file')
    ap.add_argument('--max-jobs', type=int, default=40, help='all model jobs for the day, retries included')
    ap.add_argument('--publish', action='store_true', help='publish to primary Dev after all checks pass')
    ap.add_argument('--repo', type=Path, default=BLOG.parent, help='clean SMN checkout at origin/main (publish)')
    ap.add_argument('--publish-only', action='store_true',
                    help='publish an edition another host already wrote and checked (Dev shows the prod shadow); '
                         'no capture, research, writing or checks run here')
    ap.add_argument('--prepare-editorial-recovery', metavar='SYMBOL',
                    help='prepare one explicit third repair after a held second review; run no model job')
    a = ap.parse_args()
    a.root.mkdir(parents=True, exist_ok=True)
    if a.prepare_editorial_recovery:
        try:
            day = Day(a.root,a.date,a.models,a.max_jobs,a.profile)
            ledger = day.prepare_editorial_recovery(a.prepare_editorial_recovery)
            log(status='editorial_recovery_prepared',ledger=str(ledger),jobs=day.jobs_used())
            return 0
        except Exception as exc:
            log(status='hold',reason=str(exc)[:500])
            return 2
    if a.publish_only:
        try:
            state = load_json(a.root/'smn-daily-state.json')
            day = Day(a.root, a.date, max_jobs=a.max_jobs, roles=state['roles'])
            day.verify_editorial_recovery()
            day.symbols=list(state['articles'])
            if not day.check()['passed']:
                raise Hold('Selected edition is incomplete; preserve finished articles and recover held subjects')
            ready = [s for s, v in day.state['articles'].items() if v.get('finalized') and not v.get('held')]
            if not ready:
                raise Hold('no finished article to publish')
            receipt = day.publish(a.repo)
            log(status=receipt.get('status'), published=ready)
            return 0
        except Exception as exc:
            save_json(a.root/'HOLD.json', {'utc': now(), 'reason': str(exc), 'step': 'publish-only'})
            log(status='hold', reason=str(exc)[:500])
            return 2
    try:
        day = Day(a.root, a.date, a.models, a.max_jobs, a.profile)
        day.verify_editorial_recovery()
        day.release_transient_holds()
        if not day.capture():
            log(status='waiting_for_production')
            return 0
        day.save()
        day.research()
        day.articles()
        day.visual()
        check = day.check()
        if not a.publish:
            log(status='ready_to_publish', jobs=day.jobs_used(), passed=check['passed'])
            return 0 if check['passed'] else 3
        if not check['passed']:
            raise Hold('Selected edition is incomplete; publication was not attempted')
        receipt = day.publish(a.repo)
        log(status=receipt.get('status'), jobs=day.jobs_used(), passed=check['passed'])
        return 0 if check['passed'] else 3
    except Exception as exc:
        save_json(a.root/'HOLD.json', {'utc': now(), 'reason': str(exc),
                                       'jobs_used': day.jobs_used() if 'day' in locals() else None,
                                       'resume': 'fix the cause, then rerun the same command'})
        log(status='hold', reason=str(exc)[:500])
        return 2


if __name__ == '__main__':
    sys.exit(main())
