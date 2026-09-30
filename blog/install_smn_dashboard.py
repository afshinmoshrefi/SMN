"""Human-operated production dashboard activation/rollback from an isolated clean release.

Requires existing production SSO configuration and separately deployed compatible
blog_queue integration. Never installs application files into /home/flask, copies
secrets, or deletes dashboard state. App secrets initialize locally on first start.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from install_smn_subscription import digest,write,run

ENV=Path('/etc/SMN/dashboard.env');SECRETS=Path('/etc/SMN/secrets.env')
STATE=Path('/var/lib/smn-dashboard');UNITS=Path('/etc/systemd/system')
HTTPS=Path('/etc/nginx/sites-enabled/smn-ssl.conf');HTTP=Path('/etc/nginx/sites-enabled/smn.conf')
SNIPPET=Path('/etc/nginx/snippets/smn_dashboard_prod.conf')
MARKER=Path('/etc/SMN/dashboard-production.json');RECORDS=Path('/var/lib/tradewave/release-state')
PYTHON=Path('/home/flask/venv/bin/python');GUNICORN=Path('/home/flask/venv/bin/gunicorn')
QUEUE=Path('/home/flask/blog/blog_queue.py')
NAMES=('pub_dashboard.service','pub_dashboard_sweep.service','pub_dashboard_sweep.timer')


def host_guard():
    if os.geteuid()!=0 or '209.182.216.112' not in run('hostname','-I').split():
        raise ValueError('Root on the SMN production host required')


def private_path(path,mask):
    if path.is_symlink() or path.stat().st_uid!=0 or path.stat().st_mode & mask:
        raise ValueError('Root-owned private dashboard configuration/state required')


def lock(record,create=True):
    path=RECORDS/'smn-production-activation.lock'
    if create:
        path.mkdir()
        write(path/'owner.json',json.dumps({'task':str(record)}))
    elif not path.exists():return lock(record)
    elif json.loads((path/'owner.json').read_text()).get('task')!=str(record):raise ValueError('Production activation lock belongs to another operation')


def unlock(record):
    path=RECORDS/'smn-production-activation.lock'
    if json.loads((path/'owner.json').read_text()).get('task')!=str(record):raise ValueError('Production activation lock owner changed')
    (path/'owner.json').unlink();path.rmdir()


def stop_unit(name):
    loaded=subprocess.run(['systemctl','show','--property=LoadState','--value',name],capture_output=True,text=True).stdout.strip()
    if loaded not in {'not-found',''}:
        run('systemctl','stop',name)
        if name!=NAMES[1]:run('systemctl','disable',name)


def auth_config(text):
    values={}
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        key,separator,value=line.partition('=')
        if not separator or key in values:raise ValueError('Dashboard environment format/duplicate setting invalid')
        values[key]=value.strip().strip('"\'')
    required={'SMN_DASHBOARD_AUTH':'required','SMN_DASHBOARD_ENV':'prod','SMN_DASHBOARD_PUBLIC':'1',
              'SMN_DASHBOARD_LOGIN_URL':'https://tradewave.ai/smn-dashboard/login'}
    optional={'SMN_DASHBOARD_COOKIE_SECURE':'1','SMN_DASHBOARD_STATE':str(STATE),
              'SMN_NEWS_ROOT':'/var/www/smn','SMN_BLOG_QUEUE_URL':'http://127.0.0.1:7171'}
    if any(values.get(k)!=v for k,v in required.items()) or any(k in values and values[k]!=v for k,v in optional.items()):
        raise ValueError('Production authentication, secure cookies and exact site/state/queue settings required')
    if set(values)-set(required)-set(optional)-{'SMN_DASHBOARD_TW_PUBKEY'}:
        raise ValueError('Unknown dashboard environment settings require inspection')
    key=Path(values.get('SMN_DASHBOARD_TW_PUBKEY',''))
    if not key.is_absolute() or not key.is_file() or key.is_symlink():raise ValueError('Existing public key file required')
    return key


def site_text(text,tls):
    if ('smn-dashboard' in text or text.count('server_name ')!=1 or
            not re.search(r'root\s+/var/www/smn\s*;',text) or
            not re.search(r'listen\s+'+('443\s+ssl' if tls else '80')+r'\s*;',text) or
            'seasonalmarketnews.com' not in text):raise ValueError('Unknown nginx site setup/drift')
    addition=('    include snippets/smn_dashboard_prod.conf;' if tls else
              '    location = /smn-dashboard { return 301 https://seasonalmarketnews.com$request_uri; }\n'
              '    location /smn-dashboard/ { return 301 https://seasonalmarketnews.com$request_uri; }')
    return re.sub(r'(server_name\s+[^;]+;)',lambda m:m[0]+'\n'+addition,text,count=1)


def unit_texts(repo):
    common=f'WorkingDirectory={repo}/blog\nEnvironmentFile={SECRETS}\nEnvironmentFile={ENV}\nEnvironment=SMN_DASHBOARD_STATE={STATE}\nEnvironment=SMN_NEWS_ROOT=/var/www/smn\nEnvironment=SMN_BLOG_QUEUE_URL=http://127.0.0.1:7171\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n'
    return {NAMES[0]:'[Unit]\nDescription=SMN production publishing dashboard\nAfter=network.target redis-server.service\n[Service]\nUser=root\n'+common+
            f'ExecStart={GUNICORN} --workers 2 --timeout 900 --bind 127.0.0.1:7172 pub_dashboard:app\nRestart=on-failure\n[Install]\nWantedBy=multi-user.target\n',
            NAMES[1]:'[Unit]\nDescription=SMN production dashboard schedules and pins\n[Service]\nType=oneshot\nUser=root\n'+common+f'ExecStart={PYTHON} {repo}/blog/pin_sweeper.py\n',
            NAMES[2]:'[Unit]\nDescription=SMN dashboard minute sweeper\n[Timer]\nOnCalendar=*-*-* *:*:00\nPersistent=false\n[Install]\nWantedBy=timers.target\n'}


PROXY='''location = /smn-dashboard { return 301 /smn-dashboard/; }
location /smn-dashboard/ {
    proxy_pass http://127.0.0.1:7172/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Prefix /smn-dashboard;
    proxy_read_timeout 900s;
    add_header Cache-Control "no-store" always;
    add_header X-Frame-Options "DENY" always;
}
'''


def activate(args):
    host_guard();repo=Path(__file__).resolve().parent.parent
    commit=run('git','-C',str(repo),'rev-parse','HEAD')
    if run('git','-C',str(repo),'status','--porcelain') or repo==Path('/home/flask') or Path('/home/flask') in repo.parents:
        raise ValueError('Isolated clean committed release required')
    if os.name!='nt' and not re.fullmatch(r'/[A-Za-z0-9_./-]+',str(repo)):raise ValueError('Safe release path required for systemd')
    proof=json.loads(args.dev_proof.read_text());snap=json.loads(args.snapshots.read_text());today=datetime.now(timezone.utc).date().isoformat()
    if proof.get('source_commit')!=commit or proof.get('status')!='dev_qualified' or not proof.get('live_verification_sha256'):
        raise ValueError('Exact-release completed Dev proof required')
    if snap.get('source_commit')!=commit or snap.get('date')!=today or not all(snap.get(k) for k in ('production_web_snapshot','production_app_snapshot','approved_by')):
        raise ValueError('Approved current-day production snapshots required')
    if MARKER.exists() or SNIPPET.exists() or any((UNITS/n).exists() for n in NAMES):raise ValueError('Existing dashboard setup requires inspected upgrade/rollback')
    for n in NAMES:
        if subprocess.run(['systemctl','show','--property=LoadState','--value',n],capture_output=True,text=True).stdout.strip() not in {'not-found',''}:
            raise ValueError('Existing dashboard unit requires inspection')
    if re.search(r':7172\b',run('ss','-ltn')):raise ValueError('Dashboard port already occupied')
    for p in (ENV,SECRETS,PYTHON,GUNICORN):
        if not p.is_file():raise ValueError('Required production dependency missing: '+str(p))
    private_path(ENV,0o027)
    if STATE.exists():private_path(STATE,0o027)
    for name in ('session.secret','service.key'):
        p=STATE/name
        if p.exists():private_path(p,0o077)
    key=auth_config(ENV.read_text())
    private_path(key,0o022)
    run(str(PYTHON),'-B','-c','from cryptography.hazmat.primitives.serialization import load_pem_public_key; from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey; import sys; assert isinstance(load_pem_public_key(open(sys.argv[1],"rb").read()),Ed25519PublicKey)',str(key))
    queue=QUEUE.read_text()
    if not all(token in queue for token in ('def _dashboard_headers(', 'def _dashboard_article_for_pattern(', 'SMN_DASHBOARD_SERVICE_KEY_FILE')):
        raise ValueError('Deploy qualified blog_queue dashboard service-key/portfolio integration separately first')
    run('systemctl','is-active','--quiet','blog_queue.service');run('nginx','-t','-q')
    for p in (HTTPS,HTTP):
        if p.resolve().name!=p.name or p.resolve().parent not in {p.parent,p.parent.parent/'sites-available'}:
            raise ValueError('Unexpected nginx site target')
    targets={HTTPS.resolve():site_text(HTTPS.read_text(),True),HTTP.resolve():site_text(HTTP.read_text(),False),SNIPPET:PROXY}
    targets.update({UNITS/n:t for n,t in unit_texts(repo).items()});targets[MARKER]=json.dumps({'source_commit':commit,'release':str(repo)})
    record=RECORDS/('smn-dashboard-'+commit[:12]+'-'+today);record.mkdir(parents=True)
    state={'status':'prepared','source_commit':commit,'paths':[str(p) for p in targets],
           'before':{str(p):digest(p) for p in targets},'after':{},'auth_inputs':{str(p):digest(p) for p in (ENV,key)},
           'dev_proof_sha256':digest(args.dev_proof),'snapshots':snap}
    for i,p in enumerate(targets):
        if p.exists():shutil.copy2(p,record/str(i))
    write(record/'receipt.json',json.dumps(state))
    lock(record);mutated=False
    try:
        if (run('git','-C',str(repo),'rev-parse','HEAD')!=commit or run('git','-C',str(repo),'status','--porcelain') or
                any(digest(Path(p))!=h for p,h in state['before'].items()) or
                any(digest(Path(p))!=h for p,h in state['auth_inputs'].items())):raise ValueError('Release/configuration changed before dashboard activation')
        STATE.mkdir(mode=0o750,parents=True,exist_ok=True)
        run('systemd-run','--wait','--pipe','--collect','--unit=smn-dashboard-init-'+commit[:12],
            '-p','WorkingDirectory='+str(repo/'blog'),'-p','EnvironmentFile='+str(SECRETS)+' '+str(ENV),
            '-p','Environment=SMN_DASHBOARD_STATE='+str(STATE),str(PYTHON),'-B','-c',
            'import dashboard_auth; dashboard_auth.session_secret(); dashboard_auth.service_key()')
        mutated=True;state['status']='activating';write(record/'receipt.json',json.dumps(state))
        for p,t in targets.items():write(p,t)
        run('systemd-analyze','verify',*[str(UNITS/n) for n in NAMES])
        run('nginx','-t','-q');run('systemctl','daemon-reload')
        run('systemctl','enable','--now',NAMES[0])
        run('curl','--retry','5','--retry-connrefused','--max-time','10','-fsS','http://127.0.0.1:7172/api/health')
        local='http://127.0.0.1:7172/api/articles';public='https://seasonalmarketnews.com/smn-dashboard/api/articles'
        if run('curl','--max-time','10','-sS','-o','/dev/null','-w','%{http_code}',local)!='401':raise ValueError('Dashboard authentication smoke failed')
        if any(digest(Path(p))!=h for p,h in state['auth_inputs'].items()):raise ValueError('Dashboard authentication configuration changed')
        run('systemctl','reload','nginx')
        if run('curl','--max-time','10','-sS','-o','/dev/null','-w','%{http_code}',public)!='401':raise ValueError('Dashboard authentication smoke failed')
        run('systemctl','enable','--now',NAMES[2]);run('systemctl','is-active','--quiet',NAMES[0])
        state.update(status='active',after={str(p):digest(p) for p in targets});write(record/'receipt.json',json.dumps(state))
        unlock(record)
        return {'status':'active','record':str(record),'source_commit':commit}
    except BaseException:
        if mutated:
            state['after']={str(p):digest(p) for p in targets};write(record/'receipt.json',json.dumps(state));rollback(record)
        else:
            state['status']='aborted';write(record/'receipt.json',json.dumps(state));unlock(record)
        raise


def rollback(record):
    host_guard();record=Path(record).resolve()
    if RECORDS.resolve() not in record.parents:raise ValueError('Dashboard rollback record outside release-state')
    state=json.loads((record/'receipt.json').read_text())
    lock(record,create=False)
    allowed={HTTPS.resolve(),HTTP.resolve(),SNIPPET,MARKER,*[UNITS/n for n in NAMES]}
    if (set(map(Path,state['paths']))!=allowed or set(state['after'])!=set(state['paths']) or
            any(digest(Path(p))!=h for p,h in state['after'].items())):raise ValueError('Dashboard configuration drift; preserve peer edits')
    for i,name in enumerate(state['paths']):
        if state['before'][name] is not None and digest(record/str(i))!=state['before'][name]:raise ValueError('Dashboard rollback backup changed')
    for n in (NAMES[2],NAMES[1],NAMES[0]):stop_unit(n)
    for i,name in enumerate(state['paths']):
        p=Path(name)
        if state['before'][name] is None:p.unlink(missing_ok=True)
        else:
            backup=record/str(i)
            if digest(backup)!=state['before'][name]:raise ValueError('Dashboard rollback backup changed')
            shutil.copy2(backup,p)
    run('systemctl','daemon-reload');run('nginx','-t','-q');run('systemctl','reload','nginx')
    state['status']='rolled_back';write(record/'receipt.json',json.dumps(state))
    unlock(record)
    return {'status':'rolled_back','record':str(record),'dashboard_state_preserved':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    install=sub.add_parser('activate');install.add_argument('--dev-proof',type=Path,required=True);install.add_argument('--snapshots',type=Path,required=True)
    undo=sub.add_parser('rollback');undo.add_argument('record',type=Path);args=parser.parse_args()
    print(json.dumps(activate(args) if args.action=='activate' else rollback(args.record)))
