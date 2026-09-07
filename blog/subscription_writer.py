"""Private, explicit Codex subscription handoff. No live SMN imports or publisher.

The caller prepares and validates the evidence. This adapter persists one task,
uses the official CLI with its saved ChatGPT login, and returns structured output.
It never reads credentials, switches accounts, or falls back to a paid API.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import threading
import time
import uuid


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with tmp.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def child_environment(source=None):
    """Remove API billing overrides in this child only; keep normal CLI login."""
    env = dict(os.environ if source is None else source)
    blocked = {'CODEX_API_KEY', 'OPENAI_API_KEY', 'CODEX_ACCESS_TOKEN',
               'OPENAI_BASE_URL', 'OPENAI_API_BASE', 'OPENAI_ORG_ID',
               'OPENAI_ORGANIZATION', 'OPENAI_PROJECT_ID', 'CODEX_THREAD_ID',
               'CODEX_SESSION_ID', 'OPENAI_FEDERATION_RULE_ID',
               'OPENAI_IDENTITY_TOKEN_FILE', 'OPENAI_WORKLOAD_IDENTITY_CONTEXT'}
    for key in list(env):
        if key.upper() in blocked or key.upper().endswith('_API_KEY'):
            del env[key]
    return env


def _startup():
    # No visible helper window on Windows.
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def codex_defaults():
    return ['--ignore-user-config', '-c', 'model_provider="openai"',
            '-c', 'forced_login_method="chatgpt"',
            '-c', 'apps._default.enabled=false', '-c', 'agents.enabled=false',
            '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
            '-c', 'web_search="disabled"']


def account_snapshot(codex, cwd, timeout=45):
    """Read only documented account/model RPCs; no model turn, no token values."""
    # --ignore-user-config is an exec flag, not an app-server flag. This
    # connection reads account metadata only and never creates a model turn.
    proc = subprocess.Popen([str(codex), 'app-server', '-c', 'model_provider="openai"'],
        cwd=str(cwd), env=child_environment(), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        encoding='utf-8', **_startup())
    messages = queue.Queue()
    def reader():
        for line in proc.stdout:
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                pass
        messages.put(None)
    threading.Thread(target=reader, daemon=True).start()
    def send(value):
        proc.stdin.write(json.dumps(value) + '\n')
        proc.stdin.flush()
    def request(method, ident, params=None):
        send({'method': method, 'id': ident, 'params': params or {}})
        until = time.monotonic() + timeout
        while True:
            msg = messages.get(timeout=max(.1, until-time.monotonic()))
            if msg is None:
                raise RuntimeError('Codex account probe ended before its response')
            if msg.get('id') != ident:
                continue
            if 'error' in msg:
                # Auth errors can carry account-specific text; record only code.
                raise RuntimeError(f'Codex {method} error code {msg["error"].get("code")}')
            return msg['result']
    try:
        request('initialize', 1, {'clientInfo': {'name': 'smn_private_writer',
                    'title': 'SMN Private Writer', 'version': '0.1.0'}})
        send({'method': 'initialized', 'params': {}})
        account = request('account/read', 2, {'refreshToken': False}).get('account') or {}
        if account.get('type') != 'chatgpt':
            raise RuntimeError('A saved ChatGPT login is required; no API fallback')
        limits = request('account/rateLimits/read', 3)
        models = request('model/list', 4, {'includeHidden': False})
        astra = [{k: m.get(k) for k in ('id','model','supportedReasoningEfforts',
                  'defaultReasoningEffort','defaultServiceTier')}
                 for m in models.get('data', []) if m.get('model') == 'gpt-6-astra'
                 or m.get('id') == 'gpt-6-astra']
        # Keep usage evidence without account IDs, email, or earned-reset IDs.
        limits = {k: limits[k] for k in ('rateLimits','rateLimitsByLimitId') if k in limits}
        return {'utc': utc_now(), 'auth_type': account.get('type'),
                'plan_type': account.get('planType'), 'rate_limits': limits,
                'astra_catalog': astra, 'probe_generated_model_turns': 0}
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def prepare_job(root, job_id, prompt, schema, *, as_of, valid_until,
                evidence_sha256, stage='write', effort='xhigh'):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', job_id):
        raise ValueError('Invalid job ID')
    if effort not in {'low', 'medium', 'high', 'xhigh', 'max'}:
        raise ValueError('Unsupported explicit effort')
    job = Path(root).resolve() / job_id
    job.mkdir(parents=True, exist_ok=False)
    (job / 'prompt.txt').write_text(prompt, encoding='utf-8')
    save_json(job / 'schema.json', schema)
    manifest = {'version': 1, 'job_id': job_id, 'stage': stage,
                'created_utc': utc_now(), 'as_of': as_of,
                'valid_until': valid_until, 'model': 'gpt-6-astra',
                'effort': effort, 'evidence_sha256': evidence_sha256,
                'publish': False,
                'input_hashes': {name: sha256((job/name).read_bytes())
                    for name in ['prompt.txt', 'schema.json']}}
    save_json(job / 'job.json', manifest)
    save_json(job / 'state.json', {'status': 'ready', 'utc': utc_now()})
    return job


def verify_job(job):
    job = Path(job).resolve()
    manifest = load_json(job / 'job.json')
    if manifest.get('publish') is not False or manifest.get('model') != 'gpt-6-astra':
        raise ValueError('Only the private Astra handoff is enabled')
    if set(manifest.get('input_hashes', {})) != {'prompt.txt', 'schema.json'}:
        raise ValueError('Unexpected job inputs')
    for name, expected in manifest['input_hashes'].items():
        if (job / name).is_symlink() or sha256((job/name).read_bytes()) != expected:
            raise ValueError('Prepared input changed after ready: ' + name)
    expiry = datetime.fromisoformat(manifest['valid_until'].replace('Z', '+00:00'))
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError('Assignment expired; refresh its source evidence before retrying')
    return manifest


def validate_schema(value, schema, where='$'):
    """Validate the deliberately small output-schema subset used by this bridge."""
    types = schema.get('type')
    types = types if isinstance(types, list) else [types]
    predicates = {'object': lambda x: isinstance(x, dict),
                  'array': lambda x: isinstance(x, list),
                  'string': lambda x: isinstance(x, str),
                  'boolean': lambda x: isinstance(x, bool),
                  'null': lambda x: x is None,
                  'number': lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
                  'integer': lambda x: isinstance(x, int) and not isinstance(x, bool)}
    if not any(t in predicates and predicates[t](value) for t in types):
        raise ValueError(where + ': wrong type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(where + ': value outside enum')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        if set(schema.get('required', [])) - value.keys():
            raise ValueError(where + ': missing required fields')
        if schema.get('additionalProperties') is False and value.keys() - props.keys():
            raise ValueError(where + ': extra fields')
        for k, v in value.items():
            if k in props:
                validate_schema(v, props[k], where + '.' + k)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', 10**9):
            raise ValueError(where + ': invalid item count')
        for i, v in enumerate(value):
            validate_schema(v, schema['items'], where + f'[{i}]')
    if isinstance(value, str) and len(value) < schema.get('minLength', 0):
        raise ValueError(where + ': too short')


def run_job(job, codex, *, timeout=900):
    job = Path(job).resolve()
    manifest = verify_job(job)
    # One completed receipt is immutable. A second scheduler cannot rerun it.
    if (job / 'receipt.json').exists():
        return load_json(job / 'receipt.json')
    state = load_json(job / 'state.json')
    if state.get('status') != 'ready':
        raise RuntimeError('Job is not ready; preserve it for explicit recovery')
    lock = job / '.claim'
    lock.mkdir(exist_ok=False)
    started = time.monotonic()
    running = False
    try:
        # A competing worker may have completed between our first state read
        # and acquiring the claim. Recheck while exclusively owning the job.
        if (job / 'receipt.json').exists():
            return load_json(job / 'receipt.json')
        if load_json(job / 'state.json').get('status') != 'ready':
            raise RuntimeError('Job is not ready; preserve it for explicit recovery')
        manifest = verify_job(job)
        save_json(lock / 'owner.json', {'pid': os.getpid(), 'host': socket.gethostname(),
                                      'utc': utc_now()})
        save_json(job / 'state.json', {'status': 'running', 'utc': utc_now()})
        running = True
        before = account_snapshot(codex, job)
        save_json(job / 'usage-before.json', before)
        if not before['astra_catalog']:
            raise RuntimeError('Astra is not listed for this account; no model substitution')
        efforts = {e['reasoningEffort'] for m in before['astra_catalog']
                   for e in m.get('supportedReasoningEfforts', [])}
        if manifest['effort'] not in efforts:
            raise RuntimeError('Requested Astra effort is unavailable; no effort substitution')
        cmd = [str(codex), 'exec', *codex_defaults(), '--ephemeral',
               '--skip-git-repo-check', '--sandbox', 'read-only',
               '-c', 'model_reasoning_effort="' + manifest['effort'] + '"',
               '--model', manifest['model'], '--json', '--color', 'never',
               '--output-schema', str(job/'schema.json'),
               '--output-last-message', str(job/'output.json'),
               '--cd', str(job), '-']
        save_json(job / 'invocation.json', {'argv': cmd,
            'auth': 'saved ChatGPT login, API overrides removed from child environment',
            'timeout_seconds': timeout})
        with (job/'events.jsonl').open('w', encoding='utf-8') as stdout, \
             (job/'diagnostic.log').open('w', encoding='utf-8') as stderr:
            proc = subprocess.Popen(cmd, cwd=job, env=child_environment(),
                stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                text=True, encoding='utf-8', **_startup())
            try:
                proc.communicate((job/'prompt.txt').read_text(encoding='utf-8'), timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise RuntimeError('Codex timed out; partial evidence retained, no automatic retry')
        events = []
        for line in (job/'events.jsonl').read_text(encoding='utf-8').splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        completed = [e for e in events if e.get('type') == 'turn.completed']
        # Record usage even when the schema/output gate fails afterward.
        usages = [e.get('usage', {}) for e in completed]
        save_json(job/'turn-usage.json', usages)
        after = account_snapshot(codex, job)
        save_json(job/'usage-after.json', after)
        if proc.returncode != 0 or len(completed) != 1:
            raise RuntimeError('Codex did not complete exactly one turn; inspect saved diagnostics')
        tool_items = [e.get('item', {}).get('type') for e in events
                      if e.get('type') == 'item.completed' and
                      e.get('item', {}).get('type') in
                      {'command_execution','mcp_tool_call','web_search','collab_tool_call','file_change'}]
        if tool_items:
            raise RuntimeError('Unexpected tools in a prepared-evidence writing job')
        output = load_json(job/'output.json')
        validate_schema(output, load_json(job/'schema.json'))
        receipt = {'job_id': manifest['job_id'], 'stage': manifest['stage'],
            'status': 'output_ready_for_smn_validation', 'started_utc': before['utc'],
            'finished_utc': utc_now(), 'seconds': round(time.monotonic()-started, 3),
            'model_requested': manifest['model'], 'effort_requested': manifest['effort'],
            'auth_type': before['auth_type'], 'plan_type': before['plan_type'],
            'usage': usages[0], 'tool_calls': tool_items, 'api_fallback': False,
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
                       'reason': str(exc), 'automatic_retry': False, 'publish': False})
        raise
    finally:
        # Remove only this process's two known lock artifacts, never recursively.
        (lock/'owner.json').unlink(missing_ok=True)
        lock.rmdir()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['probe','run'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--codex', type=Path, required=True)
    args = parser.parse_args()
    result = (account_snapshot(args.codex,args.directory) if args.action == 'probe'
              else run_job(args.directory,args.codex))
    print(json.dumps(result,ensure_ascii=False))
