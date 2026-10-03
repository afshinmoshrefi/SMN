"""Private durable generation jobs. Dispatch is separately disabled by default."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time
import uuid

from daily_briefing import digest
from subscription_writer import load_json, save_json, sha256, utc_now

KINDS = {'derivative', 'article_video', 'daily_briefing', 'daily_avatar', 'social_export', 'substack_export'}
INPUTS = {'article_id', 'briefing_id', 'source_revision', 'source_hash', 'script', 'chart_id',
          'channel', 'variant', 'payload_sha256', 'media_job_id'}


class Conflict(ValueError):
    pass


def _folder(root):
    path = Path(root).resolve() / 'promotion-jobs'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(root, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{24}', identifier):
        raise ValueError('Invalid job ID')
    return _folder(root) / (identifier + '.json')


@contextmanager
def locked(root):
    path = _folder(root) / '.lock'
    deadline = time.monotonic() + 5
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise Conflict('Job store is locked; inspect owner before recovery')
            time.sleep(.05)
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        path.unlink()


def summary(job):
    return {key: job.get(key) for key in ('id', 'kind', 'stage', 'status', 'version', 'source_revision',
        'source_hash', 'review_status', 'generation_status', 'dispatch_status', 'holds', 'artifacts',
        'updated_at', 'attempts', 'actor', 'error')}


def get_job(root, identifier):
    return summary(load_json(_path(root, identifier)))


def list_jobs(root):
    return sorted((summary(load_json(p)) for p in _folder(root).glob('*.json')
                   if re.fullmatch(r'[a-f0-9]{24}', p.stem)),
                  key=lambda job: job['updated_at'], reverse=True)


def create(root, kind, inputs, actor):
    if kind not in KINDS or not isinstance(inputs, dict) or set(inputs) - INPUTS:
        raise ValueError('Unsupported job kind/input; filesystem paths are server-owned')
    if not actor or not inputs.get('source_revision') or not re.fullmatch(r'[a-f0-9]{64}', str(inputs.get('source_hash', ''))):
        raise ValueError('Actor, exact source revision and SHA256 required')
    if not (inputs.get('article_id') or inputs.get('briefing_id')):
        raise ValueError('Canonical article or briefing identity required')
    identifier = digest({'kind': kind, 'inputs': inputs})[:24]
    path = _path(root, identifier)
    with locked(root):
        if path.exists():
            existing = load_json(path)
            if existing['inputs'] != inputs or existing['kind'] != kind:
                raise Conflict('Existing job identity differs from requested inputs')
            return summary(existing)
        job = {'id': identifier, 'kind': kind, 'inputs': inputs, 'stage': 'draft', 'status': 'draft',
            'version': 1, 'source_revision': inputs['source_revision'], 'source_hash': inputs['source_hash'],
            'review_status': 'pending', 'generation_status': 'not_started', 'dispatch_status': 'disabled',
            'holds': [], 'artifacts': [], 'attempts': 0, 'actor': actor, 'updated_at': utc_now()}
        save_json(path, job)
        return summary(job)


def update(root, identifier, expected_version, **fields):
    """Server-side worker checkpoint, never exposed directly as an HTTP mutation."""
    with locked(root):
        path = _path(root, identifier); job = load_json(path)
        if job['version'] != expected_version:
            raise Conflict('Job changed; reload before retry')
        job.update(fields, version=job['version'] + 1, updated_at=utc_now())
        save_json(path, job)
        return summary(job)


def transition(root, identifier, action, expected_version, actor, data=None):
    data = data or {}
    with locked(root):
        path = _path(root, identifier); job = load_json(path)
        if job['version'] != expected_version:
            raise Conflict('Job changed; reload before mutation')
        if not actor:
            raise ValueError('Named actor required')
        if action == 'retry':
            if job['status'] not in {'held', 'failed'} or job['generation_status'] == 'unknown_outcome' or job['attempts'] >= 2:
                raise Conflict('Retry requires reconciled failure and remaining attempt budget')
            job.update(status='draft', stage='draft', holds=[], error=None)
        elif action == 'cancel':
            if job['status'] == 'running':
                raise Conflict('Cannot cancel an in-flight provider call; reconcile its result first')
            job.update(status='canceled', stage='canceled', dispatch_status='disabled')
        elif action == 'review':
            if job['status'] != 'generated' or data.get('payload_sha256') != digest(job['inputs']):
                raise Conflict('Review requires generated current revision and exact input binding')
            decision = data.get('decision')
            if decision not in {'approved', 'rejected'}:
                raise ValueError('Explicit review decision required')
            for artifact in job['artifacts']:
                get_artifact(root,identifier,artifact['name'])
            job.update(status='reviewed' if decision == 'approved' else 'held', review_status=decision,
                       review={'actor': actor, 'at': utc_now(), 'payload_sha256': data['payload_sha256'],
                               'artifact_hashes': {a['name']: a['sha256'] for a in job['artifacts']}})
        elif action == 'edit':
            if job['status'] == 'running' or not isinstance(data.get('script'), str) or not data['script'].strip():
                raise Conflict('Edit requires idle job and nonempty script')
            inputs = dict(job['inputs'], script=data['script'])
            new_id = digest({'kind': job['kind'], 'inputs': inputs})[:24]
            if new_id == identifier:
                return summary(job)
            new_path = _path(root, new_id)
            if new_path.exists():
                revised = load_json(new_path)
                if revised['inputs'] != inputs:
                    raise Conflict('Edited job identity collision')
            else:
                revised = dict(job, id=new_id, inputs=inputs, version=1, actor=actor,
                    status='draft', stage='draft', review_status='pending', generation_status='not_started',
                    artifacts=[], attempts=0, holds=[], error=None, updated_at=utc_now())
                revised.pop('review', None)
                revised.pop('source_review', None)
                save_json(new_path, revised)
            job.update(status='superseded', stage='superseded', superseded_by=new_id,
                       version=job['version'] + 1, actor=actor, updated_at=utc_now())
            save_json(path, job)
            return summary(revised)
        else:
            raise ValueError('Unsupported action')
        job.update(actor=actor, version=job['version'] + 1, updated_at=utc_now())
        save_json(path, job)
        return summary(job)


def pause(root, scope, enabled, actor):
    if scope not in KINDS | {'all'} or type(enabled) is not bool or not actor:
        raise ValueError('Valid scope, boolean and actor required')
    with locked(root):
        path = _folder(root) / 'controls.json'
        controls = load_json(path) if path.exists() else {}
        controls[scope] = {'paused': enabled, 'actor': actor, 'updated_at': utc_now()}
        save_json(path, controls)
    return controls


def is_paused(root, kind):
    path = _folder(root) / 'controls.json'
    controls = load_json(path) if path.exists() else {}
    return any(controls.get(k, {}).get('paused') for k in ('all', kind))


def get_artifact(root, identifier, name):
    """Allowlisted, hash-verified private file lookup for authenticated dashboard."""
    job = load_json(_path(root, identifier))
    item = next((a for a in job['artifacts'] if a['name'] == name), None)
    if not item:
        raise ValueError('Artifact is not allowlisted')
    raw_private = _folder(root) / 'artifacts' / identifier
    if any(p.is_symlink() for p in (raw_private,*raw_private.parents)):
        raise ValueError('Private artifact directory contains a symlink')
    private = raw_private.resolve()
    raw = private / item['relative_path']
    path = raw.resolve()
    if private not in path.parents or any(p.is_symlink() for p in (raw, *raw.parents)) or sha256(path.read_bytes()) != item['sha256']:
        raise ValueError('Artifact path or hash changed')
    return path, item['media_type']
