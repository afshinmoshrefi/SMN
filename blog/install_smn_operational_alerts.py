"""Install the isolated alert timer after exact-release qualification and secret provision."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from install_smn_subscription import digest, run, write

UNIT_DIR = Path('/etc/systemd/system')
SERVICE = 'smn-operational-alerts.service'
TIMER = 'smn-operational-alerts.timer'
SECRET_FILE = Path('/etc/tradewave/secrets.env')
RECEIPT = Path('/var/lib/tradewave/release-state/smn-operational-alerts-activation.json')
ROOT = Path('/var/lib/tradewave/smn-daily/subscription-primary')
HOSTS = {'dev':'192.168.1.180', 'production':'209.182.216.112'}


def unit_texts(repo, target):
    repo = Path(repo).resolve()
    if target not in HOSTS:
        raise ValueError('Explicit alert target required')
    script = repo/'blog/smn_operational_alerts.py'
    if not script.is_file() or any(c in str(repo) for c in '\n\r'):
        raise ValueError('Committed alert observer source is missing or unsafe')
    service = f'''[Unit]
Description=Independent SMN publication and failure observer
After=network-online.target

[Service]
Type=oneshot
User=root
EnvironmentFile={SECRET_FILE}
WorkingDirectory={repo}/blog
ExecStart=/home/flask/venv/bin/python {script} --root {ROOT} --target {target}
TimeoutStartSec=90s
'''
    timer = f'''[Unit]
Description=Check SMN article failures and publication every minute

[Timer]
OnCalendar=*-*-* *:*:00
Persistent=false
Unit={SERVICE}

[Install]
WantedBy=timers.target
'''
    return {SERVICE: service, TIMER: timer}


def _secret_ready(path=SECRET_FILE):
    if path.is_symlink():
        return False
    try:
        info = path.stat()
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) not in {0o600, 0o640}:
            return False
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.startswith('RESEND_API_KEY='):
                value = line.partition('=')[2].strip().strip('"\'')
                return bool(value and value.upper() != 'PLACEHOLDER')
    except OSError:
        pass
    return False


def _preflight(repo, target, dev_proof=None, snapshots=None):
    if target not in HOSTS or HOSTS[target] not in run('hostname', '-I').split():
        raise ValueError('Explicit SMN target host required')
    repo = Path(repo).resolve()
    if repo != Path(__file__).resolve().parent.parent:
        raise ValueError('Installer must run from the exact isolated source checkout')
    commit = run('git', '-C', str(repo), 'rev-parse', 'HEAD')
    if run('git', '-C', str(repo), 'status', '--porcelain'):
        raise ValueError('Clean committed candidate required')
    proof_digest = None
    if target == 'production':
        if not dev_proof or not snapshots:
            raise ValueError('Exact-release Dev proof and current-day snapshots required')
        proof = json.loads(Path(dev_proof).read_text(encoding='utf-8'))
        snap = json.loads(Path(snapshots).read_text(encoding='utf-8'))
        today = datetime.now(timezone.utc).date().isoformat()
        if proof.get('source_commit') != commit or proof.get('status') != 'dev_qualified' or not proof.get('live_verification_sha256'):
            raise ValueError('Exact-release completed Dev qualification required')
        if (snap.get('source_commit') != commit or snap.get('date') != today or
                not snap.get('production_web_snapshot') or not snap.get('production_app_snapshot') or
                not snap.get('approved_by')):
            raise ValueError('Human confirmation of current-day production web and app snapshots required')
        proof_digest = digest(Path(dev_proof))
    return commit, proof_digest


def _receipt_after(paths):
    return {str(path):digest(path) for path in paths}


def rollback(receipt=RECEIPT):
    receipt = Path(receipt)
    state = json.loads(receipt.read_text(encoding='utf-8'))
    if state.get('status') not in {'active', 'installing'}:
        raise ValueError('No active alert installation to roll back')
    owned = {str(UNIT_DIR/SERVICE), str(UNIT_DIR/TIMER)}
    if set(state.get('units', {})) != owned:
        raise ValueError('Activation receipt does not name only owned alert units')
    paths = [Path(name) for name in state['units']]
    for path in paths:
        if path.is_symlink() or digest(path) != state['units'][str(path)]:
            raise ValueError('Alert unit changed after installation; preserve peer edit: '+str(path))
    subprocess.run(['systemctl','disable','--now',TIMER], check=True)
    for path in paths:
        path.unlink(missing_ok=True)
    subprocess.run(['systemctl','daemon-reload'], check=True)
    state['status'] = 'rolled_back'
    write(receipt, json.dumps(state, indent=2))
    return {'status':'rolled_back', 'receipt':str(receipt)}


def install(repo, target, dev_proof=None, snapshots=None):
    if os.geteuid() != 0:
        raise ValueError('Root operator required')
    if not _secret_ready():
        raise ValueError('Root-owned 0600/0640 non-symlink RESEND_API_KEY file required')
    commit, proof_digest = _preflight(repo, target, dev_proof, snapshots)
    units = unit_texts(repo, target)
    paths = [UNIT_DIR/name for name in units]
    if RECEIPT.exists() or any(path.exists() or path.is_symlink() for path in paths):
        raise ValueError('Alert observer already installed; inspect before upgrade')
    written = []
    try:
        for name, content in units.items():
            path = UNIT_DIR/name
            write(path, content)
            written.append(path)
        state = {'status':'installing','target':target,'source_commit':commit,
                 'dev_proof_sha256':proof_digest,
                 'snapshots_sha256':digest(Path(snapshots)) if snapshots else None,
                 'units':_receipt_after(written)}
        write(RECEIPT, json.dumps(state, indent=2))
        subprocess.run(['systemctl','daemon-reload'], check=True)
        subprocess.run(['systemctl','enable','--now',TIMER], check=True)
        subprocess.run(['systemctl','is-active','--quiet',TIMER], check=True)
        state['status'] = 'active'
        write(RECEIPT, json.dumps(state, indent=2))
        return {'status':'active','source_commit':commit,'receipt':str(RECEIPT)}
    except BaseException:
        if RECEIPT.exists():
            rollback(RECEIPT)
        else:
            for path in written:
                if digest(path) == hashlib.sha256(units[path.name].encode()).hexdigest():
                    path.unlink(missing_ok=True)
            subprocess.run(['systemctl','daemon-reload'], check=False)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    activate = sub.add_parser('activate')
    activate.add_argument('--repo', type=Path, required=True)
    activate.add_argument('--target', choices=tuple(HOSTS), required=True)
    activate.add_argument('--dev-proof', type=Path)
    activate.add_argument('--snapshots', type=Path)
    undo = sub.add_parser('rollback')
    undo.add_argument('--receipt', type=Path, default=RECEIPT)
    args = parser.parse_args()
    result = install(args.repo, args.target, args.dev_proof, args.snapshots) if args.action == 'activate' else rollback(args.receipt)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
