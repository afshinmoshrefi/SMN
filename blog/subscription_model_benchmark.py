"""Private fixed-input model experiment. Never invokes a publisher or paid API."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import shutil
import traceback

import engine_edition_workflow as ew
import subscription_writer as sw

SYMBOLS = ('MRK', 'XLK', 'AZO', 'SPY', 'QQQ', 'GC')
FILES = ('article.schema.json', 'bundle.json', 'chart-manifest.json', 'chart-words.json',
         'commission.json', 'hero-asset.json', 'seasonal-manifest.json', 'source.json', 'writer-evidence.json')


def prepare(baseline, root, model, date):
    if root.exists():
        raise ValueError('Fresh experiment directory required; never overwrite a trial')
    root.mkdir(parents=True)
    shutil.copy2(baseline/'sources.json', root/'sources.json')
    pairs = []
    for sym in SYMBOLS:
        old = baseline/'results'/sym
        out = root/'results'/sym
        out.mkdir(parents=True)
        for name in FILES:
            shutil.copy2(old/name, out/name)
        for name in ('assets', 'evidence'):
            shutil.copytree(old/name, out/name)
        commission = sw.load_json(out/'commission.json')
        commission['account_writer'] = 'ChatGPT subscription; ' + model + ' xhigh'
        commission['experiment_only'] = True
        sw.save_json(out/'commission.json', commission)
        oldjob = baseline/'jobs'/(sym+'-'+date.replace('-', '')+'-write')
        manifest = sw.load_json(oldjob/'job.json')
        job = sw.prepare_job(root/'jobs', oldjob.name,
            (oldjob/'prompt.txt').read_text(encoding='utf-8'), sw.load_json(oldjob/'schema.json'),
            as_of=manifest['as_of'], valid_until=(datetime.now(timezone.utc)+timedelta(hours=20)).isoformat(),
            evidence_sha256=manifest['evidence_sha256'], model=model, effort='xhigh')
        new = sw.load_json(job/'job.json')
        if new['input_hashes'] != manifest['input_hashes']:
            raise ValueError('Benchmark prompt/schema bytes differ from baseline')
        pairs.append({'symbol': sym, 'baseline_job': str(oldjob), 'input_hashes': new['input_hashes'],
                      'evidence_sha256': manifest['evidence_sha256']})
    sw.save_json(root/'experiment.json', {'model': model, 'effort': 'xhigh', 'date': date,
        'baseline': str(baseline), 'identical_writer_inputs': pairs, 'publish': False,
        'reviewer_model': model, 'writer_jobs': 6, 'reviewer_jobs_max': 6,
        'as_of_replay': True, 'new_source_research': False, 'new_hero_generation': False})


def cohort(root, model, codex, date):
    edition = ew.Edition(root, date, codex)
    statuses = []
    for sym in SYMBOLS:
        stage = 'write'
        row = {'symbol': sym, 'model': model}
        try:
            receipt = sw.run_job(edition.job(sym, 'write'), codex)
            row['writer_usage'] = receipt['usage']
            checks = edition.receive(sym)
            row['mechanical_passed'] = checks['passed']
            if not checks['structure']['passed']:
                row['status'] = 'structure_failed'
            else:
                stage = 'review'
                # Same existing review prompt and schema; explicitly set candidate model
                # on the newly created job BEFORE running it, never alter a receipt.
                edition.review(sym)
                reviewjob = edition.job(sym, 'review')
                manifest = sw.load_json(reviewjob/'job.json')
                manifest['model'] = model
                sw.save_json(reviewjob/'job.json', manifest)
                receipt = sw.run_job(reviewjob, codex)
                row['reviewer_usage'] = receipt['usage']
                review = sw.load_json(reviewjob/'output.json')
                row['editorial_passed'] = review.get('passed')
                if checks['passed'] and review.get('passed'):
                    stage = 'finalize'
                    edition.finalize(sym)
                    row['status'] = 'finalized_private'
                else:
                    row['status'] = 'held_for_benchmark_review'
        except Exception as exc:
            row.update(status='failed', stage=stage, error=type(exc).__name__+': '+str(exc))
            (root/(sym+'-error.txt')).write_text(traceback.format_exc(), encoding='utf-8')
        statuses.append(row)
        sw.save_json(root/'benchmark-status.json', statuses)
        print('BENCHMARK '+model+' '+sym+' '+row['status'], flush=True)
    return statuses


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--baseline', type=Path, required=True)
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--codex', type=Path, required=True)
    ap.add_argument('--date', default='2026-09-22')
    ap.add_argument('--prepare-only', action='store_true')
    args = ap.parse_args()
    roots = [(args.root/'sol', 'gpt-6-sol'), (args.root/'luna', 'gpt-6-luna')]
    for root, model in roots:
        prepare(args.baseline.resolve(), root.resolve(), model, args.date)
    if not args.prepare_only:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(cohort, root.resolve(), model, args.codex, args.date) for root, model in roots]
            for job in as_completed(jobs):
                job.result()
