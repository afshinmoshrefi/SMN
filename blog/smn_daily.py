"""One SMN Dev edition as a fixed script workflow (no AI agent directs the run).

    capture -> research -> write -> checks -> (repair) -> review -> (repair, re-review)
    -> finalize -> layout -> screenshot check + hero check -> publish -> live check

Models come from smn_models.json (edit one line to switch a step between Claude
and Codex). Every step is resumable from files in the edition root; rerunning the
same command continues where it stopped. Transient failures retry a bounded number
of times; anything else writes HOLD.json with the exact step and exits 2.

    python smn_daily.py --root /var/lib/tradewave/smn-daily/2026-09-25 --date 2026-09-25 [--publish]
"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import smn_models
import smn_research
import smn_visual
import subscription_capture as capture
from subscription_publication import CHECKS
from subscription_writer import load_json, save_json

BLOG = Path(__file__).resolve().parent
CLIS = {'claude': os.environ.get('SMN_CLAUDE', '/root/.local/bin/claude'), 'codex': os.environ.get('SMN_CODEX')}
PLAYWRIGHT = os.environ.setdefault('SMN_PLAYWRIGHT', '/opt/smn-playwright/node_modules/playwright')
os.environ.setdefault('SMN_BROWSER_CHANNEL', 'bundled')
ATTEMPT_FILES = ('state.json', 'diagnostic.log', 'result.json', 'turn-usage.json', 'usage-before.json',
                 'invocation.json', 'output.json', 'events.jsonl', 'usage-after.json')


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
    def __init__(self, root, date, models=None, max_jobs=30):
        self.root = Path(root).resolve()
        self.date = date
        self.models = models
        self.roles = smn_models.load(models)
        self.max_jobs = max_jobs
        self.state_path = self.root/'smn-daily-state.json'
        self.state = load_json(self.state_path) if self.state_path.exists() else {
            'date': date, 'created': now(), 'roles': self.roles, 'articles': {}}

    def save(self):
        save_json(self.state_path, self.state)

    # ---- model jobs -------------------------------------------------
    def jobs_used(self):
        jobs = self.root/'jobs'
        return sum(1 for j in jobs.iterdir() if j.is_dir()) + sum(
            1 for j in jobs.glob('*/failed-attempt-*')) if jobs.exists() else 0

    def run_job(self, job):
        """Run a prepared job; one requeue when the job failed without a receipt."""
        job = Path(job)
        if (job/'receipt.json').exists():
            return load_json(job/'receipt.json')
        if self.jobs_used() > self.max_jobs:
            raise Hold('model-job budget of %d exhausted' % self.max_jobs)
        try:
            return smn_models.run(job, CLIS)
        except Exception as exc:
            if (job/'receipt.json').exists() or list(job.glob('failed-attempt-*')):
                raise Hold('model job %s failed twice: %s' % (job.name, exc))
            archive = job/'failed-attempt-1'
            archive.mkdir()
            for name in ATTEMPT_FILES:
                if (job/name).exists():
                    shutil.move(str(job/name), archive/name)
            save_json(job/'state.json', {'status': 'ready', 'utc': now(), 'requeued_after': str(exc)[:300]})
            log(step='requeue', job=job.name, error=str(exc)[:300])
            time.sleep(60)
            return smn_models.run(job, CLIS)

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
        if target.exists():
            return
        example = load_json(BLOG/'examples/subscription-sources-20260923.json')
        folder = self.root/'research'
        folder.mkdir(exist_ok=True)
        for sym in self.symbols:
            if (folder/(sym + '.json')).exists():
                continue
            other = next(v for k, v in example.items() if k != sym)
            entry, problems = self._research_job(sym, other, 'research')
            if problems:
                entry, problems = self._research_job(sym, other, 'research-two', problems)
            if problems:
                raise Hold('research for %s failed checks twice: %s' % (sym, '; '.join(problems)))
            save_json(folder/(sym + '.json'), smn_research.to_sources(entry))
            log(step='research', symbol=sym, sources=len(entry['sources']))
        save_json(target, {sym: load_json(folder/(sym + '.json')) for sym in self.symbols})

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
        ed = Edition(self.root, self.date, provider='config', claude=CLIS['claude'], models=self.models)
        ed.clis = CLIS
        for sym in self.symbols:
            self._article(ed, sym)

    def _issues(self, name, lines):
        path = self.root/'issues'/name
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return path

    def _article(self, ed, sym):
        s = self.state['articles'].setdefault(sym, {})
        if s.get('finalized'):
            return
        if not ed.job(sym, 'write').exists():
            ed.prepare(sym, 'write')
        self.run_job(ed.job(sym, 'write'))
        if not s.get('mechanical_ok'):
            draft = s.get('draft', 'write')
            checks = ed.receive(sym, draft)
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
        review = load_json(ed.job(sym, stage)/'output.json')
        if not review_passed(review):
            if stage == 'rereview':
                raise Hold('%s failed review twice: %s' % (sym, review_problems(review)))
            issues = self._issues(sym + '-review.txt', review_problems(review))
            if not ed.job(sym, 'repair-two').exists():
                ed.repair(sym, issues, 'repair-two')
            self.run_job(ed.job(sym, 'repair-two'))
            checks = ed.receive(sym, 'repair-two')
            if not checks['passed']:
                raise Hold('%s review repair broke mechanical checks' % sym)
            s['review_stage'] = stage = 'rereview'
            self.save()
            if not ed.job(sym, stage).exists():
                ed.review(sym, stage)
            self.run_job(ed.job(sym, stage))
            review = load_json(ed.job(sym, stage)/'output.json')
            if not review_passed(review):
                raise Hold('%s failed review twice: %s' % (sym, review_problems(review)))
        ed.finalize(sym, stage)
        s['finalized'] = True
        s['review_stage'] = stage
        self.save()
        log(step='article', symbol=sym, review_stage=stage)

    def visual(self):
        missing = [s for s in self.symbols if not (self.root/'results'/s/'layout-checks.json').exists()]
        if missing:
            retry('layout', lambda: subprocess.run(['node', str(BLOG/'subscription_layout.cjs'), str(self.root),
                  *self.symbols], check=True, cwd=BLOG))
        for sym in self.symbols:
            out = self.root/'results'/sym
            if not (out/'hero-check.json').exists():
                smn_visual.hero(self.root, self.date, sym, self.roles, CLIS)
            if (out/'visual-checks.json').exists():
                continue
            record = smn_visual.article(self.root, self.date, sym, self.roles, CLIS)
            if not record['passed']:
                raise Hold('%s screenshot check failed: %s' % (sym, record['defects']))

    def publish(self, repo):
        import subscription_primary_publish as primary
        if (self.root/'dev-publication-receipt.json').exists():
            return load_json(self.root/'dev-publication-receipt.json')
        if not (self.root/'primary-stage.json').exists():
            primary.stage(self.root, Path(repo))
        if not (self.root/'live-verification.json').exists():
            primary.activate(self.root, Path(repo), 'node', PLAYWRIGHT)
        if not (self.root/'live-landing-visual-checks.json').exists():
            record = smn_visual.landing(self.root, self.date, self.roles, CLIS)
            if not record['passed']:
                primary.call(load_json(self.root/'primary-stage.json'), 'rollback')
                raise Hold('live landing check failed; rolled back: %s' % record['defects'])
        return primary.finish(self.root, Path(repo))


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
    return (r.get('passed') is True and set(r.get('checks', {})) == CHECKS and
            all(v.get('passed') is True for v in r['checks'].values()) and
            not any(i.get('severity') in {'major', 'blocker'} for i in r.get('issues', [])))


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
    ap.add_argument('--models', type=Path, help='role settings file (default blog/smn_models.json)')
    ap.add_argument('--max-jobs', type=int, default=30, help='all model jobs for the day, retries included')
    ap.add_argument('--publish', action='store_true', help='publish to primary Dev after all checks pass')
    ap.add_argument('--repo', type=Path, default=BLOG.parent, help='clean SMN checkout at origin/main (publish)')
    a = ap.parse_args()
    a.root.mkdir(parents=True, exist_ok=True)
    day = Day(a.root, a.date, a.models, a.max_jobs)
    try:
        if not day.capture():
            log(status='waiting_for_production')
            return 0
        day.save()
        day.research()
        day.articles()
        day.visual()
        if not a.publish:
            log(status='ready_to_publish', jobs=day.jobs_used())
            return 0
        receipt = day.publish(a.repo)
        log(status=receipt.get('status'), jobs=day.jobs_used())
        return 0
    except Hold as exc:
        save_json(a.root/'HOLD.json', {'utc': now(), 'reason': str(exc), 'jobs_used': day.jobs_used(),
                                       'resume': 'fix the cause, then rerun the same command'})
        log(status='hold', reason=str(exc)[:500])
        return 2


if __name__ == '__main__':
    sys.exit(main())
