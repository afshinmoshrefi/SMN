"""Private Claude Code subscription handoff with the same job contract as subscription_writer.

One prepared job (prompt.txt + schema.json) becomes one headless `claude -p` turn
with no tools, no MCP servers, no user/project settings and a fixed system prompt.
The child uses the saved claude.ai login only: API-key and alternate-provider
variables are removed, and the run is refused unless `claude auth status` reports
a claude.ai (subscription) login. The exact model and effort are recorded in the
receipt; any other model in the usage record fails the job. No paid API fallback.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

from subscription_writer import (load_json, save_json, sha256, utc_now,
                                 validate_schema)

PROVIDER = 'anthropic'
MODEL = 'claude-opus-5-5'           # article writing and repair
REVIEW_MODEL = 'claude-sonnet-5'    # independent review
LIGHT_MODEL = 'claude-haiku-4-5-20251001'  # screenshot inspection
MODELS = {MODEL, REVIEW_MODEL, LIGHT_MODEL}
EFFORTS = {'low', 'medium', 'high', 'xhigh', 'max'}
SYSTEM = ('You are a professional financial writer and editor for Seasonal Market News. '
          'Answer only with the requested structured output. Use only the supplied evidence.')
BLOCKED = {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
           'ANTHROPIC_BEDROCK_BASE_URL', 'ANTHROPIC_VERTEX_BASE_URL',
           'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
           'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL',
           'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT'}


def child_environment(source=None):
    """Remove API billing and provider overrides in this child only; keep the saved login."""
    env = dict(os.environ if source is None else source)
    for key in list(env):
        if key.upper() in BLOCKED or key.upper().endswith('_API_KEY'):
            del env[key]
    return env


def account_snapshot(claude, cwd, timeout=45):
    """Read login metadata only (no model turn). Never records email or org IDs."""
    out = subprocess.run([str(claude), 'auth', 'status', '--json'], cwd=str(cwd),
                         env=child_environment(), capture_output=True, text=True,
                         timeout=timeout)
    try:
        status = json.loads(out.stdout)
    except json.JSONDecodeError:
        raise RuntimeError('Claude auth status unreadable; no API fallback')
    if not status.get('loggedIn') or status.get('authMethod') != 'claude.ai':
        raise RuntimeError('A saved claude.ai subscription login is required; no API fallback')
    if status.get('apiProvider') != 'firstParty':
        raise RuntimeError('Alternate Claude provider configured; no substitution')
    version = subprocess.run([str(claude), '--version'], capture_output=True, text=True,
                             env=child_environment(), timeout=timeout).stdout.strip()
    return {'utc': utc_now(), 'auth_type': 'claude.ai', 'billing_source': 'subscription',
            'plan_type': status.get('subscriptionType'), 'cli_version': version,
            'probe_generated_model_turns': 0}


def prepare_job(root, job_id, prompt, schema, *, as_of, valid_until,
                evidence_sha256, stage='write', effort='medium', model=MODEL):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', job_id):
        raise ValueError('Invalid job ID')
    if model not in MODELS:
        raise ValueError('Unsupported Claude model')
    if effort not in EFFORTS:
        raise ValueError('Unsupported explicit effort')
    job = Path(root).resolve() / job_id
    job.mkdir(parents=True, exist_ok=False)
    (job / 'prompt.txt').write_text(prompt, encoding='utf-8')
    save_json(job / 'schema.json', schema)
    manifest = {'version': 1, 'job_id': job_id, 'stage': stage,
                'created_utc': utc_now(), 'as_of': as_of,
                'valid_until': valid_until, 'provider': PROVIDER, 'model': model,
                'effort': effort, 'evidence_sha256': evidence_sha256,
                'publish': False,
                'input_hashes': {name: sha256((job/name).read_bytes())
                    for name in ['prompt.txt', 'schema.json']}}
    save_json(job / 'job.json', manifest)
    save_json(job / 'state.json', {'status': 'ready', 'utc': utc_now()})
    return job


def verify_job(job):
    from datetime import datetime, timezone
    job = Path(job).resolve()
    manifest = load_json(job / 'job.json')
    if (manifest.get('publish') is not False or manifest.get('provider') != PROVIDER
            or manifest.get('model') not in MODELS):
        raise ValueError('Only the private Claude subscription handoff is enabled')
    if set(manifest.get('input_hashes', {})) != {'prompt.txt', 'schema.json'}:
        raise ValueError('Unexpected job inputs')
    for name, expected in manifest['input_hashes'].items():
        if (job / name).is_symlink() or sha256((job/name).read_bytes()) != expected:
            raise ValueError('Prepared input changed after ready: ' + name)
    expiry = datetime.fromisoformat(manifest['valid_until'].replace('Z', '+00:00'))
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError('Assignment expired; refresh its source evidence before retrying')
    return manifest


def command(claude, job, manifest):
    return [str(claude), '-p', '--model', manifest['model'], '--effort', manifest['effort'],
            '--output-format', 'json', '--json-schema', (job/'schema.json').read_text(encoding='utf-8'),
            '--tools', '', '--strict-mcp-config', '--setting-sources', '',
            '--no-session-persistence', '--system-prompt', SYSTEM]


def run_job(job, claude, *, timeout=1800):
    job = Path(job).resolve()
    manifest = verify_job(job)
    if (job / 'receipt.json').exists():
        return load_json(job / 'receipt.json')
    if load_json(job / 'state.json').get('status') != 'ready':
        raise RuntimeError('Job is not ready; preserve it for explicit recovery')
    lock = job / '.claim'
    lock.mkdir(exist_ok=False)
    started = time.monotonic()
    running = False
    try:
        if (job / 'receipt.json').exists():
            return load_json(job / 'receipt.json')
        if load_json(job / 'state.json').get('status') != 'ready':
            raise RuntimeError('Job is not ready; preserve it for explicit recovery')
        manifest = verify_job(job)
        save_json(lock / 'owner.json', {'pid': os.getpid(), 'host': socket.gethostname(),
                                        'utc': utc_now()})
        save_json(job / 'state.json', {'status': 'running', 'utc': utc_now()})
        running = True
        before = account_snapshot(claude, job)
        save_json(job / 'usage-before.json', before)
        cmd = command(claude, job, manifest)
        # The schema is inlined in argv; record its hash, not a second copy.
        shown = [a if a != cmd[cmd.index('--json-schema')+1] else '<schema.json>' for a in cmd]
        save_json(job / 'invocation.json', {'argv': shown,
            'auth': 'saved claude.ai subscription login; API/provider overrides removed from child environment',
            'timeout_seconds': timeout})
        with (job/'diagnostic.log').open('w', encoding='utf-8') as stderr:
            proc = subprocess.run(cmd, cwd=job, env=child_environment(),
                                  input=(job/'prompt.txt').read_text(encoding='utf-8'),
                                  stdout=subprocess.PIPE, stderr=stderr, text=True,
                                  encoding='utf-8', timeout=timeout)
        (job/'result.json').write_text(proc.stdout, encoding='utf-8')
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError('Claude returned no JSON result; inspect saved diagnostics')
        usage = result.get('modelUsage') or {}
        save_json(job/'turn-usage.json', {'usage': result.get('usage'), 'modelUsage': usage,
                  'duration_ms': result.get('duration_ms'), 'num_turns': result.get('num_turns')})
        if proc.returncode != 0 or result.get('is_error'):
            raise RuntimeError('Claude job failed: ' + str(result.get('result'))[:300])
        if set(usage) != {manifest['model']}:
            raise RuntimeError('Unexpected model in usage record; no model substitution: ' + ','.join(usage))
        output = result.get('structured_output')
        if output is None:
            raise RuntimeError('Claude returned no structured output')
        save_json(job/'output.json', output)
        validate_schema(output, load_json(job/'schema.json'))
        receipt = {'job_id': manifest['job_id'], 'stage': manifest['stage'],
            'status': 'output_ready_for_smn_validation', 'started_utc': before['utc'],
            'finished_utc': utc_now(), 'seconds': round(time.monotonic()-started, 3),
            'provider': PROVIDER, 'model_requested': manifest['model'],
            'model_used': sorted(usage), 'effort_requested': manifest['effort'],
            'auth_type': before['auth_type'], 'billing_source': before['billing_source'],
            'plan_type': before['plan_type'], 'cli_version': before['cli_version'],
            'usage': {k: v for k, v in usage[manifest['model']].items() if k != 'costUSD'},
            'tool_calls': [], 'api_fallback': False,
            'new_external_provider_calls': 0,
            'output_sha256': sha256((job/'output.json').read_bytes()),
            'input_hashes': manifest['input_hashes'],
            'evidence_sha256': manifest['evidence_sha256'], 'publish': False}
        save_json(job/'receipt.json', receipt)
        save_json(job/'state.json', {'status': 'output_ready_for_smn_validation', 'utc': utc_now()})
        return receipt
    except Exception as exc:
        if running:
            save_json(job/'state.json', {'status': 'failed_needs_review', 'utc': utc_now(),
                       'reason': str(exc)[:500], 'automatic_retry': False, 'publish': False})
        raise
    finally:
        (lock/'owner.json').unlink(missing_ok=True)
        lock.rmdir()
