"""Dev-only reader service/proxy preparation. Root coordinates the short activation lock."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


ENV = Path('/etc/SMN/membership.env')
SITE = Path('/etc/nginx/sites-available/smn.conf')
SNIPPET = Path('/etc/nginx/snippets/smn_reader.conf')
UNIT = Path('/etc/systemd/system/smn-reader.service')
DASH = Path('/etc/systemd/system/pub_dashboard.service.d/60-membership.conf')
QUEUE = Path('/etc/systemd/system/blog_queue.service.d/zz-membership.conf')
PROCESSOR = Path('/etc/systemd/system/article_processor.service.d/zz-membership.conf')
SWEEP = Path('/etc/systemd/system/pub_dashboard_sweep.service.d/60-membership.conf')
PROMOTION = Path('/etc/systemd/system/smn-promotion.service')
PROMOTION_TIMER = Path('/etc/systemd/system/smn-promotion.timer')
BRIEFING = Path('/etc/systemd/system/smn-market-briefing.service')
BRIEFING_TIMER = Path('/etc/systemd/system/smn-market-briefing.timer')
PYTHON = '/home/flask/venv-smn-membership-20261003/bin/python'


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.membership-new')
    temporary.write_text(content, encoding='utf-8')
    temporary.chmod(mode)
    temporary.replace(path)


def guard():
    if os.geteuid() != 0 or '192.168.1.180' not in run('hostname', '-I').split():
        raise ValueError('SMN Dev host required')


def configuration():
    return '\n'.join([
        'SMN_READER_PRIVATE_ROOT=/var/lib/smn/reader',
        'SMN_READER_ENV=dev',
        'SMN_PUBLIC_ORIGIN=https://smn-dev.trxstat.com',
        'SMN_READER_CLIENT_ID=client_01M3Z7YJKDZ9DKVK9MNYEP6X85',
        'SMN_READER_CALLBACK_URL=https://smn-dev.trxstat.com/smn-dashboard/auth/callback',
        'SMN_READER_SHARED_DEV_CALLBACK=1',
        'SMN_READER_AUTHORITY_URL=https://tw2-dev.trxstat.com',
        'SMN_READER_SERVICE_KEY_FILE=/etc/SMN/reader-service.key',
        'SMN_MEMBERSHIP_API_BASE=https://tw2-dev.trxstat.com',
        'SMN_MEMBERSHIP_ADMIN_KEY_FILE=/etc/SMN/membership-admin-service.key',
        'SMN_NEWS_ROOT=/var/www/smn',
        'SMN_DASHBOARD_STATE=/var/lib/smn-dashboard',
        'SMN_MEMBERSHIP_PYTHON=' + PYTHON,
        'PYTHONDONTWRITEBYTECODE=1', ''])


def nginx():
    proxy = '''
    proxy_pass http://127.0.0.1:7173;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_cache off;
    access_log off;
    proxy_intercept_errors off;
    add_header Cache-Control "private, no-store" always;
    add_header CDN-Cache-Control "no-store" always;
    add_header Cloudflare-CDN-Cache-Control "no-store" always;
    add_header Vary "Cookie" always;
    add_header X-Content-Type-Options "nosniff" always;
'''
    routes = '\n'.join('location ' + path + ' {' + proxy + '}\n' for path in
        ('^~ /member/', '^~ /articles/', '^~ /editions/', '^~ /datasets/', '^~ /briefings/', '= /posts.json'))
    return routes + '''
location = /smn-dashboard/auth/callback {
    access_log off;
    proxy_pass http://127.0.0.1:7172/auth/callback;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Prefix /smn-dashboard;
    proxy_cache off;
    add_header Cache-Control "private, no-store" always;
    add_header Referrer-Policy "no-referrer" always;
}
'''


def targets(repo):
    reader = f'''[Unit]
Description=SMN verified reader access
After=network.target
[Service]
User=root
WorkingDirectory={repo}/blog
EnvironmentFile={ENV}
ExecStart={PYTHON} -m gunicorn --workers 2 --timeout 60 --bind 127.0.0.1:7173 "reader_app:create_app()"
Restart=on-failure
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/smn/reader
[Install]
WantedBy=multi-user.target
'''
    dashboard = (f'[Service]\nWorkingDirectory={repo}/blog\nEnvironmentFile={ENV}\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n'
                 f'ExecStart=\nExecStart={PYTHON} -m gunicorn --workers 2 --timeout 900 --bind 127.0.0.1:7172 --bind 192.168.1.180:7172 pub_dashboard:app\n')
    original = SITE.read_text('utf-8')
    if 'server_name smn-dev.trxstat.com localhost;' not in original:
        raise ValueError('SMN Dev nginx site drifted')
    if 'include snippets/smn_reader.conf;' not in original:
        original = original.replace('include snippets/smn_dashboard.conf;',
                                    'include snippets/smn_dashboard.conf;\n    include snippets/smn_reader.conf;')
    common = f'[Service]\nWorkingDirectory={repo}/blog\nEnvironmentFile={ENV}\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nExecStart=\n'
    queue = common + f'ExecStart={PYTHON} -m gunicorn --workers 2 --bind unix:/home/flask/blog/blog_queue.sock -m 0 wsgi:app\n'
    processor = common + f'ExecStart={PYTHON} {repo}/blog/article_processor.py\n'
    sweep = common + f'ExecStart={PYTHON} {repo}/blog/pin_sweeper.py\n'
    job = ('[Unit]\nDescription=SMN private promotion jobs\nAfter=network.target\n[Service]\nType=oneshot\nUser=root\n'
           f'WorkingDirectory={repo}/blog\nEnvironmentFile={ENV}\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n'
           f'ExecStart={PYTHON} {repo}/blog/promotion_daemon.py\nUMask=0077\nTimeoutStartSec=1800\nNoNewPrivileges=true\n')
    timer = ('[Unit]\nDescription=Run pending SMN private promotion work\n[Timer]\nOnBootSec=2min\n'
             'OnUnitInactiveSec=1min\nPersistent=false\n[Install]\nWantedBy=timers.target\n')
    daily = job.replace('SMN private promotion jobs', 'SMN daily market headline discovery').replace(
        f'{repo}/blog/promotion_daemon.py', f'{repo}/blog/briefing_daily.py --output-root /var/lib/smn/reader/briefing-runs '
        '--codex /usr/local/bin/codex --model gpt-5.6-sol --effort medium')
    daily_timer = ('[Unit]\nDescription=Prepare the daily SMN market briefing\n[Timer]\n'
                   'OnCalendar=Mon..Fri *-*-* 06:00:00 America/New_York\nPersistent=false\n'
                   '[Install]\nWantedBy=timers.target\n')
    return {ENV: configuration(), UNIT: reader, DASH: dashboard, SITE: original, SNIPPET: nginx(),
            QUEUE: queue, PROCESSOR: processor, SWEEP: sweep, PROMOTION: job,
            PROMOTION_TIMER: timer, BRIEFING: daily, BRIEFING_TIMER: daily_timer}


def prepare(record):
    guard()
    repo = Path(__file__).resolve().parent.parent
    if not str(repo).startswith('/opt/smn-worktrees/'):
        raise ValueError('Isolated SMN checkout required')
    commit = run('sudo', '-u', 'flask', 'git', '-C', str(repo), 'rev-parse', 'HEAD')
    if run('sudo', '-u', 'flask', 'git', '-C', str(repo), 'status', '--porcelain'):
        raise ValueError('Candidate must be clean')
    for key in ('reader-service.key', 'membership-admin-service.key'):
        p = Path('/etc/SMN') / key
        if not p.is_file() or p.is_symlink() or p.stat().st_mode & 0o007:
            raise ValueError('Private service key is missing or has unsafe permissions')
    record = Path(record)
    record.mkdir(mode=0o700, parents=True, exist_ok=False)
    values = targets(repo)
    before = {}
    for index, (path, content) in enumerate(values.items()):
        before[str(path)] = {'file': str(index), 'exists': path.exists(),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
            'mode': path.stat().st_mode & 0o777 if path.exists() else None}
        if path.exists():
            (record / (str(index) + '.before')).write_bytes(path.read_bytes())
        (record / (str(index) + '.after')).write_text(content, encoding='utf-8')
    state = {'status': 'prepared', 'repo': str(repo), 'commit': commit, 'before': before,
             'public_full_restore_allowed': False}
    write(record / 'receipt.json', json.dumps(state, indent=2), 0o600)
    return state


def activate_reader(record):
    guard()
    record = Path(record)
    state = json.loads((record / 'receipt.json').read_text())
    if state['status'] != 'prepared':
        raise ValueError('Prepared reader configuration required')
    Path('/var/lib/smn/reader').mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in (ENV, UNIT):
        index = state['before'][str(path)]['file']
        write(path, (record / (index + '.after')).read_text(), 0o600 if path == ENV else 0o644)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'smn-reader.service')
    for attempt in range(15):
        probe = subprocess.run(['curl', '-fsS', '--max-time', '2', 'http://127.0.0.1:7173/member/health'], capture_output=True, text=True)
        if probe.returncode == 0:
            break
        time.sleep(1)
    else:
        raise ValueError('Reader process did not become healthy')
    state['status'] = 'reader_running_registry_disabled'
    write(record / 'receipt.json', json.dumps(state, indent=2), 0o600)
    return state


def gate(record):
    guard()
    record = Path(record)
    state = json.loads((record / 'receipt.json').read_text())
    if state['status'] != 'reader_running_registry_disabled':
        raise ValueError('Reader must be healthy before installing the access gate')
    for path in (SITE, SNIPPET, DASH):
        before = state['before'][str(path)]
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if actual != before['sha256']:
            raise ValueError('Configuration changed since preparation: ' + str(path))
        write(path, (record / (before['file'] + '.after')).read_text())
    try:
        run('nginx', '-t')
    except Exception:
        for path in (SITE, SNIPPET, DASH):
            before = state['before'][str(path)]
            if before['exists']:
                path.write_bytes((record / (before['file'] + '.before')).read_bytes())
                path.chmod(before['mode'])
            else:
                path.unlink(missing_ok=True)
        raise
    run('systemctl', 'daemon-reload')
    run('systemctl', 'reload', 'nginx')
    run('systemctl', 'restart', 'pub_dashboard.service')
    state['status'] = 'access_gate_active_pending_corpus'
    write(record / 'receipt.json', json.dumps(state, indent=2), 0o600)
    return state


def workers(record):
    guard()
    record = Path(record)
    state = json.loads((record / 'receipt.json').read_text())
    migration = json.loads(Path('/var/lib/smn/reader/migration.json').read_text())
    if state['status'] != 'access_gate_active_pending_corpus' or migration['status'] != 'active':
        raise ValueError('The complete private corpus must be active before enabling publishers')
    for path in (QUEUE, PROCESSOR, SWEEP, PROMOTION, PROMOTION_TIMER, BRIEFING, BRIEFING_TIMER):
        before = state['before'][str(path)]
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if actual != before['sha256']:
            raise ValueError('Worker configuration changed since preparation')
        write(path, (record / (before['file'] + '.after')).read_text())
    run('systemctl', 'daemon-reload')
    run('systemctl', 'restart', 'blog_queue.service', 'article_processor.service')
    verify_workers(state['repo'])
    run('systemctl', 'start', 'pub_dashboard_sweep.timer')
    run('systemctl', 'enable', '--now', 'smn-promotion.timer', 'smn-market-briefing.timer')
    state['status'] = 'active'
    write(record / 'receipt.json', json.dumps(state, indent=2), 0o600)
    return state


def verify_workers(repo):
    """Check effective launch commands and stable processes, including later drop-ins."""
    repo = Path(repo).resolve()
    previous = None
    for attempt in range(3):
        current = {}
        for name in ('blog_queue.service', 'article_processor.service'):
            assert run('systemctl', 'is-active', name) == 'active', name
            command = run('systemctl', 'show', '-p', 'ExecStart', '--value', name)
            if PYTHON not in command or (name == 'article_processor.service' and str(repo / 'blog/article_processor.py') not in command):
                raise ValueError('Effective worker launch is overridden: ' + name)
            pid = run('systemctl', 'show', '-p', 'MainPID', '--value', name)
            if pid == '0' or Path('/proc/' + pid + '/cwd').resolve() != repo / 'blog':
                raise ValueError('Worker is not running the candidate checkout: ' + name)
            args = Path('/proc/' + pid + '/cmdline').read_bytes().replace(b'\0', b' ').decode()
            if PYTHON not in args:
                raise ValueError('Worker process uses another runtime: ' + name)
            current[name] = pid
        if previous is not None and current != previous:
            raise ValueError('Worker restarted during activation verification')
        previous = current
        if attempt < 2:
            time.sleep(2)
    return current


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'reader', 'gate', 'workers'])
    parser.add_argument('record')
    args = parser.parse_args()
    result = {'prepare': prepare, 'reader': activate_reader, 'gate': gate, 'workers': workers}[args.action](args.record)
    print(json.dumps({k: result[k] for k in ('status', 'repo', 'commit')}))
