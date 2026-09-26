"""Read-only, receipt-based usage summary for SMN Claude subscription runs."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re


TOKEN_FIELDS = {
    'input_tokens': 'inputTokens',
    'cache_read_tokens': 'cacheReadInputTokens',
    'cache_write_tokens': 'cacheCreationInputTokens',
    'output_tokens': 'outputTokens',
}
USAGE_FIELDS = (*TOKEN_FIELDS, 'reported_api_equivalent_usd')
MAX_JSON_BYTES = 32 * 1024 * 1024


def _json_file(path: Path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_JSON_BYTES:
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _result_usage(path: Path):
    """Use a result only if the smaller turn-usage record is unavailable."""
    result = _json_file(path)
    if result is not None:
        return result
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_JSON_BYTES:
        return None
    try:
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get('type') == 'result':
                    result = event
    except (OSError, UnicodeError):
        return None
    return result


def _number(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        return None
    return value


def _empty_usage():
    return {key: None for key in USAGE_FIELDS}


def _model_usage(raw):
    usage = _empty_usage()
    if not isinstance(raw, dict):
        return usage
    for key, source in TOKEN_FIELDS.items():
        usage[key] = _number(raw.get(source))
    usage['reported_api_equivalent_usd'] = _number(raw.get('costUSD'))
    usage['cost_basis'] = raw.get('costBasis') if raw.get('costBasis') in ('list',) else None
    return usage


def _aggregate(rows):
    total = {'jobs': len(rows), 'completed': sum(r['status'] == 'output_ready_for_smn_validation' for r in rows),
             'failed': sum(r['status'] == 'failed_needs_review' for r in rows),
             'retries': sum(r['retry'] for r in rows)}
    for key in USAGE_FIELDS:
        values = [r[key] for r in rows if r[key] is not None]
        total[key] = round(sum(values), 8) if values else None
    total['missing_fields'] = sorted({key for r in rows for key in r['missing_fields']})
    return total


def _safe_dir(path: Path):
    return path.is_dir() and not path.is_symlink()


def _coordination_usage(root: Path):
    evidence = _json_file(root / 'coordination-usage.json')
    if evidence is None:
        return None
    fields = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
    result = {key: _number(evidence.get(key)) for key in fields}
    for key in ('provider', 'model', 'scope'):
        value = evidence.get(key)
        result[key] = value[:120] if isinstance(value, str) else None
    result['complete'] = evidence.get('complete') if isinstance(evidence.get('complete'), bool) else None
    result['api_equivalent_usd'] = _number(evidence.get('api_equivalent_usd'))
    result['source'] = 'coordination-usage.json'
    return result


def _run_metadata(root: Path):
    raw = _json_file(root / 'run-metadata.json') or {}
    allowed = ('label', 'source_date', 'created_utc', 'started_utc', 'finished_utc',
               'status', 'publication_mode', 'source_commit')
    return {key: raw[key][:120] for key in allowed if isinstance(raw.get(key), str)}


def _job(job_dir: Path):
    manifest = _json_file(job_dir / 'job.json') or {}
    state = _json_file(job_dir / 'state.json') or {}
    receipt = _json_file(job_dir / 'receipt.json') or {}
    turn = _json_file(job_dir / 'turn-usage.json')
    usage_source = 'turn-usage.json' if turn is not None else 'result.json'
    if turn is None:
        turn = _result_usage(job_dir / 'result.json') or {}
        if not turn:
            usage_source = 'receipt.json' if receipt.get('usage') or receipt.get('model_usage') else None
    models = turn.get('modelUsage')
    if not isinstance(models, dict) or not models:
        models = receipt.get('model_usage')
    if not isinstance(models, dict) or not models:
        name = receipt.get('model_requested') or manifest.get('model')
        models = {name: receipt['usage']} if isinstance(name, str) and isinstance(receipt.get('usage'), dict) else {}
    breakdown = {name: _model_usage(raw) for name, raw in models.items() if isinstance(name, str)}
    usage = _empty_usage()
    for key in USAGE_FIELDS:
        values = [m[key] for m in breakdown.values() if m[key] is not None]
        usage[key] = round(sum(values), 8) if values else None
    job_id = manifest.get('job_id') if isinstance(manifest.get('job_id'), str) else job_dir.name
    stage = manifest.get('stage') or receipt.get('stage') or 'unknown'
    status = state.get('status') or receipt.get('status') or 'unknown'
    row = {
        'job_id': job_id, 'article': job_id.split('-', 1)[0] if '-' in job_id else None,
        'stage': stage, 'status': status,
        'retry': stage.startswith(('repair', 'rereview', 'retry')),
        'model_requested': manifest.get('model') or receipt.get('model_requested'),
        'models': breakdown,
        'usage_source': usage_source,
        'billing_source': receipt.get('billing_source'),
        **usage,
        'missing_fields': [key for key in USAGE_FIELDS if usage[key] is None],
    }
    return row


def summarize_run(root: Path) -> dict:
    """Summarize saved jobs without reading prompts, outputs, or account identity."""
    root = Path(root)
    jobs_dir = root / 'jobs'
    jobs = []
    if _safe_dir(root) and _safe_dir(jobs_dir):
        for child in sorted(jobs_dir.iterdir()):
            if _safe_dir(child) and (child / 'job.json').is_file() and not (child / 'job.json').is_symlink():
                jobs.append(_job(child))
    by_model = {}
    by_stage = {}
    for job in jobs:
        by_stage.setdefault(job['stage'], []).append(job)
        for model, usage in job['models'].items():
            by_model.setdefault(model, []).append({'status': job['status'], 'retry': job['retry'], **usage,
                                                     'missing_fields': [k for k in USAGE_FIELDS if usage[k] is None]})
    job_status = ('unavailable' if not _safe_dir(jobs_dir) else
              'empty' if not jobs else
              'incomplete' if any(j['status'] != 'output_ready_for_smn_validation' for j in jobs) else 'complete')
    metadata = _run_metadata(root)
    return {
        'run_id': root.name, 'label': metadata.get('label', root.name),
        'source_date': metadata.get('source_date'),
        'started_utc': metadata.get('started_utc'), 'finished_utc': metadata.get('finished_utc'),
        'publication_mode': metadata.get('publication_mode'),
        'source_commit': metadata.get('source_commit'),
        'status': metadata.get('status', job_status), 'job_status': job_status,
        'jobs': jobs, 'totals': _aggregate(jobs),
        'by_model': {name: _aggregate(rows) for name, rows in sorted(by_model.items())},
        'by_stage': {name: _aggregate(rows) for name, rows in sorted(by_stage.items())},
        'missing_fields': sorted({key for job in jobs for key in job['missing_fields']}),
        'cost_basis': 'Claude CLI modelUsage.costUSD (reported list-price API equivalent, not a charge)',
        'billing': {'source': 'subscription' if jobs and all(j['billing_source'] == 'subscription' for j in jobs)
                    else 'unknown', 'actual_cash_charged_usd': None, 'subscription_allocation_usd': None},
        'overhead': {'batch_tokens': None, 'coordination_usage': _coordination_usage(root),
                     'reason': 'Batch overhead unavailable; coordination usage is separate from article totals'},
        'hero_image': {'incremental_dev_generation_calls': 0, 'basis': 'retained production hero reuse'},
    }


def discover_runs(state_root: Path) -> list[dict]:
    """Find date editions and comparison runs; avoid following directory symlinks."""
    state_root = Path(state_root)
    if not _safe_dir(state_root):
        return []
    candidates = [child for child in state_root.iterdir()
                  if _safe_dir(child) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', child.name)]
    comparisons = state_root / 'comparisons'
    if _safe_dir(comparisons):
        candidates += [child for child in comparisons.iterdir()
                       if _safe_dir(child) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,100}', child.name)]
    runs = []
    for root in sorted(candidates):
        if not _safe_dir(root / 'jobs'):
            continue
        summary = summarize_run(root)
        summary.pop('jobs')
        summary.pop('by_model')
        summary.pop('by_stage')
        summary['kind'] = 'comparison' if root.parent == comparisons else 'daily'
        runs.append(summary)
    return runs
