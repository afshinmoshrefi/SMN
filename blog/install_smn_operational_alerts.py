"""Install the separate SMN alert observer after release qualification and key provision."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

UNIT_DIR = Path('/etc/systemd/system')
SERVICE = 'smn-operational-alerts.service'
TIMER = 'smn-operational-alerts.timer'
SECRET_FILE = Path('/etc/tradewave/secrets.env')
RECEIPT = Path('/var/lib/tradewave/release-state/smn-operational-alerts-activation.json')
ROOT = Path('/var/lib/tradewave/smn-daily/subscription-primary')


def unit_texts(repo, target):
    repo = Path(repo).resolve()
    if target not in {'dev', 'production'}:
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


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _key_configured(path=SECRET_FILE):
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return False
    for line in lines:
        if line.startswith('RESEND_API_KEY='):
            value = line.partition('=')[2].strip().strip('"\'')
            return bool(value and value.upper() != 'PLACEHOLDER')
    return False


def install(repo, target):
    if os.geteuid() != 0:
        raise ValueError('Root operator required for systemd installation')
    if not _key_configured():
        raise ValueError('Provision RESEND_API_KEY privately in /etc/tradewave/secrets.env before activation')
    repo = Path(repo).resolve()
    if subprocess.check_output(['git','-C',str(repo),'status','--porcelain','--untracked-files=no'], text=True).strip():
        raise ValueError('Clean committed observer source required')
    units = unit_texts(repo, target)
    if RECEIPT.exists() or any((UNIT_DIR/name).exists() for name in units):
        raise ValueError('Alert observer already installed; inspect existing activation before upgrade')
    written = []
    try:
        for name, content in units.items():
            unit_path = UNIT_DIR/name
            unit_path.write_text(content, encoding='utf-8')
            written.append(unit_path)
        subprocess.run(['systemctl','daemon-reload'], check=True)
        subprocess.run(['systemctl','enable','--now',TIMER], check=True)
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps({'repo':str(repo), 'target':target,
                                       'units':{name:_sha(content) for name,content in units.items()},
                                       'timer':TIMER}, indent=2), encoding='utf-8')
    except BaseException:
        subprocess.run(['systemctl','disable','--now',TIMER], check=False)
        for path in written:
            path.unlink(missing_ok=True)
        subprocess.run(['systemctl','daemon-reload'], check=False)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--target', choices=('dev','production'), required=True)
    parser.add_argument('--install', action='store_true', help='Write and enable units after external release approval')
    args = parser.parse_args()
    if args.install:
        install(args.repo, args.target)
    else:
        print(json.dumps(unit_texts(args.repo, args.target), indent=2))


if __name__ == '__main__':
    main()
