"""Run source-bound derivative writing through the existing subscription CLI."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daily_briefing import digest
import promotion_jobs as jobs
from public_derivative import derivative_schema, prepare_prompt, validate_derivative,validate_prepared
import subscription_writer as writer


def generate(root, prepared, settings, codex, *, job_id=None):
    # Recheck custody before any model call, even before prepared/schema creation.
    validate_prepared(prepared)
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


def generate_script(root, prepared, approved_copy, settings, codex, *, job_id):
    """Separate immutable script attempts; source approval belongs to the resolver."""
    from public_derivative import prepare_script_prompt, video_script_schema, validate_video_script
    validate_prepared(prepared)
    validate_derivative(approved_copy, prepared)
    stored = writer.load_json(jobs._path(root, job_id))
    provenance = prepared['provenance']
    expected = {'article_id': provenance['article_id'], 'source_revision': provenance['revision'],
                'source_hash': provenance['article_sha256'], 'payload_sha256': digest(approved_copy)}
    if stored['kind'] != 'article_script' or any(stored['inputs'].get(k) != v for k, v in expected.items()):
        raise ValueError('Script job differs from its exact approved public copy.')
    if approved_copy.get('video'):
        raise ValueError('The approved public copy already contains its reviewed script.')
    if stored['status'] in {'generated', 'reviewed', 'canceled', 'superseded'} or jobs.is_paused(root, 'article_script'):
        return jobs.summary(stored)
    if stored['status'] == 'running' or stored['attempts'] >= 2:
        raise jobs.Conflict('Script attempt is in flight or its two-attempt ceiling was reached.')
    private = jobs._folder(root) / 'artifacts' / job_id
    private.mkdir(parents=True, exist_ok=True)
    attempt = stored['attempts'] + 1
    model_job = private / ('model-job' if attempt == 1 else 'model-job-2')
    prompt = prepare_script_prompt(prepared, approved_copy)
    if attempt == 2 and (private / 'validation.json').is_file():
        prompt += '\nPrevious attempt was held: ' + writer.load_json(private / 'validation.json')['issue']
    if not model_job.exists():
        now = datetime.now(timezone.utc)
        writer.prepare_job(private, model_job.name, prompt, video_script_schema(prepared),
            as_of=now.isoformat(), valid_until=(now + timedelta(hours=20)).isoformat(),
            evidence_sha256=provenance['evidence_sha256'], stage='article-script-write',
            model=settings['model'], effort=settings['effort'])
    job = jobs.update(root, job_id, stored['version'], status='running', stage='writing',
                      generation_status='running', attempts=attempt)
    try:
        receipt = writer.run_job(model_job, codex)
        if receipt.get('status') != 'output_ready_for_smn_validation':
            raise ValueError('Subscription script writer did not return validated output.')
        script = writer.load_json(model_job / 'output.json')
        checks = validate_video_script(script, prepared, approved_copy)
        writer.save_json(private / 'script.json', script)
        writer.save_json(private / 'checks.json', checks)
        artifacts = [{'name': name, 'relative_path': name, 'media_type': 'application/json',
            'sha256': writer.sha256((private / name).read_bytes()), 'review_status': 'pending'}
            for name in ('script.json', 'checks.json')]
        return jobs.update(root, job_id, job['version'], status='generated', stage='review',
                           generation_status='generated', artifacts=artifacts, review_status='pending', holds=[])
    except Exception as exc:
        # Provider bytes remain immutable; a reconciled retry gets a separate model-job-2.
        message = str(exc) if isinstance(exc, ValueError) else 'Inspect the private immutable writer receipt before retry.'
        writer.save_json(private / 'validation.json', {'issue': message})
        jobs.update(root, job_id, job['version'], status='held', stage='writing', generation_status='failed',
                    holds=[message + ' Choose supported engine facts and retain qualifications when source budgets are tight.'])
        raise
