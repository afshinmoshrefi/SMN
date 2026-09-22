"""One SMN Dev edition, start to finish, with no orchestrating agent.

Replaces the Astra heartbeat. Each step is an existing idempotent stage, so a
rerun resumes where the last run stopped. Models are called only inside
research (Terra + web search), writing and repair (Astra), and review (Terra).
A failure writes HOLD.json and exits 2 for a person to inspect.

    python lean_daily.py --root ROOT --date YYYY-MM-DD --codex CODEX --repo REPO [--publish]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import lean_research
import subscription_capture as capture
from subscription_daily import DailyController
from subscription_writer import load_json, save_json

HERE = Path(__file__).resolve().parent


def step(name, fn):
    print(json.dumps({'step': name, 'utc': datetime.now(timezone.utc).isoformat()}), flush=True)
    return fn()


def usage(root):
    """Tokens per model and stage for every job that ran (failed ones too), plus the account meter."""
    rows, meters = {}, []
    for turns in sorted(Path(root).glob('jobs/*/turn-usage.json')):
        job = load_json(turns.parent / 'job.json')
        key = job['model'] + ' ' + job['effort'] + ' ' + job['stage']
        row = rows.setdefault(key, {'jobs': 0, 'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0})
        row['jobs'] += 1
        for turn in load_json(turns):
            for k in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
                row[k] += turn.get(k, 0)
        for name in ('usage-before.json', 'usage-after.json'):
            snap = turns.parent / name
            if snap.exists():
                meters.append(load_json(snap))
    meters.sort(key=lambda m: m['utc'])
    return {'by_model_and_stage': rows,
            'account_meter_first': meters[0]['rate_limits'] if meters else None,
            'account_meter_last': meters[-1]['rate_limits'] if meters else None}


def run(root, date, codex, repo, publish=False, max_new_model_jobs=24, node='node'):
    root = Path(root).resolve()
    receipt = root / 'dev-publication-receipt.json'
    if receipt.exists():
        return {'date': date, 'status': 'already_published'}
    got = step('capture_production', lambda: capture.production(root, date))
    if got['status'] == 'waiting_for_production':
        return got
    step('capture_engine', lambda: capture.engine(root, date, 'engine'))
    expiry = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()
    step('research', lambda: lean_research.research(root, date, codex, expiry))
    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    result = step('write_review', lambda: DailyController(root, date, codex, max_new_model_jobs, commit).run())
    if result['status'] != 'awaiting_visual_review':
        raise RuntimeError('controller stopped: ' + json.dumps(result))
    step('layout', lambda: subprocess.run([node, str(HERE / 'subscription_layout.cjs'), str(root), *result['symbols']],
                                          check=True))
    save_json(root / 'usage-summary.json', usage(root))
    if not publish:
        return {'date': date, 'status': 'ready_to_publish', 'symbols': result['symbols']}
    publisher = [sys.executable, str(HERE / 'subscription_dev_publish.py')]
    for action in ('stage', 'activate', 'finish'):
        extra = ['--node', node] if action == 'activate' else []
        if action == 'activate' and os.environ.get('SMN_PLAYWRIGHT'):
            extra += ['--playwright', os.environ['SMN_PLAYWRIGHT']]
        step(action, lambda: subprocess.run([*publisher, action, '--root', str(root), '--repo', str(repo), *extra], check=True))
    return {'date': date, 'status': 'live_verified', 'symbols': result['symbols']}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--date', required=True)
    ap.add_argument('--codex', type=Path, required=True)
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--publish', action='store_true', help='stage, activate and verify on SMN Dev')
    ap.add_argument('--max-new-model-jobs', type=int, default=24)
    ap.add_argument('--node', default='node')
    a = ap.parse_args()
    try:
        out = run(a.root, a.date, a.codex, a.repo, a.publish, a.max_new_model_jobs, a.node)
    except Exception as exc:
        a.root.mkdir(parents=True, exist_ok=True)
        hold = {'date': a.date, 'status': 'held', 'error': type(exc).__name__, 'detail': str(exc)[:2000],
                'utc': datetime.now(timezone.utc).isoformat()}
        save_json(a.root / 'HOLD.json', hold)
        print(json.dumps(hold))
        sys.exit(2)
    print(json.dumps(out))


if __name__ == '__main__':
    main()
