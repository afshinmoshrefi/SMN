"""Human-operated scheduler cutover; requires exact-release Dev proof and today's snapshots.

No application files are copied over /home/flask. The isolated committed checkout
becomes the runner; prior schedules/configuration are preserved for rollback.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ACTIVATION = Path('/etc/SMN/subscription-primary.json')
CRON = Path('/etc/crontab')
UNITS = Path('/etc/systemd/system')
BASE = Path('/opt/smn-subscription')
SERVICE = 'smn-subscription.service'
TIMER = 'smn-subscription.timer'


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.smn-new')
    temporary.write_text(value, encoding='utf-8')
    temporary.replace(path)


def cron_text(text):
    lines = text.splitlines()
    queues = [i for i, line in enumerate(lines) if not line.lstrip().startswith('#') and 'python daily_article_queue.py' in line]
    quotes = [i for i, line in enumerate(lines) if not line.lstrip().startswith('#') and 'python update_news_quotes.py' in line]
    if len(queues) != 1 or len(quotes) != 1:
        raise ValueError('Expected exactly one daily API queue and quote updater; inspect scheduler drift')
    lines[queues[0]] = '# Subscription primary replaces this queue: '+lines[queues[0]]
    line = lines[quotes[0]]
    needle = 'cd /home/flask/blog && '
    if needle not in line:
        raise ValueError('Quote updater command drifted')
    lines[quotes[0]] = line.replace(needle, needle+'[ ! -d /var/lib/tradewave/release-state/smn-production-activation.lock ] && ', 1)
    return '\n'.join(lines)+'\n'


def activate(args):
    if '209.182.216.112' not in run('hostname', '-I').split():
        raise ValueError('SMN production host required')
    repo = Path(__file__).resolve().parent.parent
    commit = run('git', '-C', str(repo), 'rev-parse', 'HEAD')
    if run('git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Clean committed candidate required')
    if ACTIVATION.exists() or (BASE/'current').exists():
        raise ValueError('Existing activation requires explicit inspected upgrade/rollback')
    proof = json.loads(args.dev_proof.read_text())
    snapshots = json.loads(args.snapshots.read_text())
    today = datetime.now(timezone.utc).date().isoformat()
    if proof.get('source_commit') != commit or proof.get('status') != 'dev_qualified' or not proof.get('live_verification_sha256'):
        raise ValueError('Exact-release completed Dev qualification required')
    if (snapshots.get('source_commit') != commit or snapshots.get('date') != today or
            not snapshots.get('production_web_snapshot') or not snapshots.get('production_app_snapshot') or
            not snapshots.get('approved_by')):
        raise ValueError('Human confirmation of current-day production web and app snapshots required')
    if not args.codex.is_file() or not args.claude.is_file():
        raise ValueError('Qualified subscription CLIs must be installed first')
    from subscription_writer import account_snapshot as codex_auth
    from claude_subscription_writer import account_snapshot as claude_auth
    codex_auth(args.codex, repo)
    claude_auth(args.claude, repo)
    if subprocess.run(['systemctl', 'is-active', '--quiet', 'smn-shadow.service']).returncode == 0:
        raise ValueError('Let the current shadow run finish before cutover')
    candidate_cron = cron_text(CRON.read_text())
    record = Path('/var/lib/tradewave/release-state')/('smn-subscription-'+commit[:12]+'-'+today)
    record.mkdir(parents=True)
    files = [CRON, UNITS/SERVICE, UNITS/TIMER, ACTIVATION]
    before = {str(path): digest(path) for path in files}
    for number, path in enumerate(files):
        if path.exists(): shutil.copy2(path, record/str(number))
    state = {'source_commit': commit, 'before': before, 'paths': [str(p) for p in files],
             'shadow_enabled': subprocess.run(['systemctl','is-enabled','--quiet','smn-shadow.timer']).returncode == 0,
             'shadow_active': subprocess.run(['systemctl','is-active','--quiet','smn-shadow.timer']).returncode == 0,
             'dev_proof_sha256': digest(args.dev_proof), 'snapshots': snapshots, 'status': 'prepared'}
    write(record/'receipt.json', json.dumps(state, indent=2))
    service = f'''[Unit]
Description=SMN ChatGPT reader edition and two Claude comparisons
After=network-online.target

[Service]
Type=oneshot
Environment=HOME=/root
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=SMN_CAPTURE_LOCAL=1
Environment=SMN_CODEX={args.codex}
Environment=SMN_CLAUDE={args.claude}
Environment=SMN_PLAYWRIGHT=/opt/smn-playwright/node_modules/playwright
Environment=SMN_BROWSER_CHANNEL=bundled
Environment=PATH=/opt/smn-shadow/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WorkingDirectory={repo}/blog
ExecStart=/home/flask/venv/bin/python {repo}/blog/smn_subscription_daily.py --root /var/lib/tradewave/smn-daily/subscription-primary --publish
Nice=15
CPUWeight=10
MemoryHigh=450M
MemoryMax=600M
MemorySwapMax=300M
OOMScoreAdjust=800
TimeoutStartSec=6h
'''
    timer = '''[Unit]
Description=SMN subscription edition and resumable retry
[Timer]
OnCalendar=Mon..Fri *-*-* 03:00:00 UTC
OnCalendar=Mon..Fri *-*-* 06:00:00 UTC
Persistent=false
[Install]
WantedBy=timers.target
'''
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        (BASE/'current').symlink_to(repo)
        write(ACTIVATION, json.dumps({'reader_provider':'chatgpt','source_commit':commit,'record':str(record)}, indent=2))
        write(CRON, candidate_cron)
        write(UNITS/SERVICE, service)
        write(UNITS/TIMER, timer)
        subprocess.run(['systemctl','disable','--now','smn-shadow.timer'], check=True)
        subprocess.run(['systemctl','daemon-reload'], check=True)
        subprocess.run(['systemctl','enable','--now',TIMER], check=True)
        subprocess.run(['systemctl','is-active','--quiet',TIMER], check=True)
        state['status'] = 'active'
        state['after'] = {str(path):digest(path) for path in files}
        write(record/'receipt.json', json.dumps(state, indent=2))
        return {'status':'active','source_commit':commit,'record':str(record),
                'rollback':'/home/flask/venv/bin/python '+str(Path(__file__).resolve())+' rollback '+str(record)}
    except BaseException:
        state['after'] = {str(path):digest(path) for path in files}
        write(record/'receipt.json', json.dumps(state, indent=2))
        rollback(record)
        raise


def rollback(record):
    record = Path(record)
    state = json.loads((record/'receipt.json').read_text())
    for name, expected in state['after'].items():
        if digest(Path(name)) != expected:
            raise ValueError('Scheduler changed after cutover; preserve peer edits: '+name)
    subprocess.run(['systemctl','disable','--now',TIMER], check=False)
    for number, name in enumerate(state['paths']):
        path = Path(name)
        if state['before'][name] is None:
            path.unlink(missing_ok=True)
        else:
            shutil.copy2(record/str(number), path)
    if (BASE/'current').is_symlink(): (BASE/'current').unlink()
    subprocess.run(['systemctl','daemon-reload'], check=True)
    if state['shadow_enabled']: subprocess.run(['systemctl','enable','smn-shadow.timer'], check=True)
    if state['shadow_active']: subprocess.run(['systemctl','start','smn-shadow.timer'], check=True)
    state['status'] = 'rolled_back'
    write(record/'receipt.json', json.dumps(state, indent=2))
    return {'status':'rolled_back','record':str(record)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    install = sub.add_parser('activate')
    install.add_argument('--dev-proof', type=Path, required=True)
    install.add_argument('--snapshots', type=Path, required=True)
    install.add_argument('--codex', type=Path, required=True)
    install.add_argument('--claude', type=Path, default=Path('/root/.local/bin/claude'))
    undo = sub.add_parser('rollback')
    undo.add_argument('record', type=Path)
    args = parser.parse_args()
    print(json.dumps(activate(args) if args.action == 'activate' else rollback(args.record)))
