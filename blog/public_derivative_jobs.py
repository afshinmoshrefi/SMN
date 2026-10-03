"""Run source-bound derivative writing through the existing subscription CLI."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_briefing import digest
import promotion_jobs as jobs
from public_derivative import derivative_schema, prepare_prompt, validate_derivative
import subscription_writer as writer


def generate(root, prepared, settings, codex, *, job_id=None):
    # Recheck custody before any model call, even before prepared/schema creation.
    for name, expected in prepared['input_hashes'].items():
        if writer.sha256(Path(name).read_bytes()) != expected:
            raise ValueError('Retained input changed')
    inputs = {'article_id': prepared['provenance']['article_id'],
        'source_revision': prepared['provenance']['revision'],
        'source_hash': prepared['provenance']['article_sha256'], 'payload_sha256': digest(prepared)}
    if job_id:
        stored=writer.load_json(jobs._path(root,job_id))
        current=stored['inputs']
        if (stored['kind']!='derivative' or any(current.get(k)!=inputs[k] for k in
                ('article_id','source_revision','source_hash')) or
                current.get('payload_sha256',inputs['payload_sha256'])!=inputs['payload_sha256']):
            raise ValueError('Existing derivative job differs from exact source preparation')
        job=jobs.summary(stored)
    else:
        job = jobs.create(root, 'derivative', inputs, 'subscription_writer')
    if job['status'] in {'generated', 'reviewed', 'canceled'} or jobs.is_paused(root, 'derivative'):
        return job
    if job['status'] == 'running':
        raise jobs.Conflict('Model job is already claimed; inspect immutable receipt')
    if job['attempts']>=2:
        raise jobs.Conflict('Two-attempt derivative ceiling reached')
    private = jobs._folder(root) / 'artifacts' / job['id']
    private.mkdir(parents=True, exist_ok=True)
    model_job = private / 'model-job'
    if not model_job.exists():
        now = datetime.now(timezone.utc)
        writer.prepare_job(private, 'model-job', prepare_prompt(prepared), derivative_schema(prepared),
            as_of=now.isoformat(), valid_until=(now + timedelta(hours=20)).isoformat(),
            evidence_sha256=prepared['provenance']['evidence_sha256'], stage='public-derivative-write',
            model=settings['model'], effort=settings['effort'])
    job = jobs.update(root, job['id'], job['version'], status='running', stage='writing',
                      generation_status='running', attempts=job['attempts'] + 1)
    try:
        receipt = writer.run_job(model_job, codex)
        if receipt.get('status') != 'output_ready_for_smn_validation':
            raise ValueError('Subscription writer did not produce validated output')
        copy = writer.load_json(model_job / 'output.json')
        checks = validate_derivative(copy, prepared)
        writer.save_json(private / 'copy.json', copy)
        writer.save_json(private / 'checks.json', checks)
        artifacts = [{'name': name, 'relative_path': name, 'media_type': 'application/json',
            'sha256': writer.sha256((private / name).read_bytes()), 'review_status': 'pending'}
            for name in ('copy.json', 'checks.json')]
        return jobs.update(root, job['id'], job['version'], status='generated', stage='review',
            generation_status='generated', review_status='pending', artifacts=artifacts)
    except Exception:
        jobs.update(root, job['id'], job['version'], status='held', stage='writing',
            generation_status='failed', holds=['Inspect private immutable writer receipt and validation before retry'])
        raise
