"""Private QA cookie from a completed reader callback; never mints central authority."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile

from reader_auth import ReaderAuth, ReaderError, _expiry


def private_path(auth, path):
    path = Path(path).absolute()
    root = auth.root.resolve().parent
    if root not in path.resolve().parents or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('QA state must stay in the private reader root without symlinks')
    return path


def export_state(auth, output, origin, workos_user_id=None):
    if auth.config.get('ENV') != 'dev' or origin != 'https://smn-dev.trxstat.com':
        raise ValueError('Reader QA state is Dev-only')
    output = private_path(auth, output)
    if output.exists():
        raise ValueError('Preserve existing QA state; clean it up before export')
    with auth.database() as db:
        rows = db.execute('SELECT id,expires,payload FROM sessions WHERE expires>?', (auth.clock(),)).fetchall()
    sources = []
    for row in rows:
        local = json.loads(row[2])
        identity = local.get('identity', {})
        if (local.get('kind') == 'reader' and local.get('env') == 'dev' and not local.get('qa')
                and identity.get('reader_authority') and identity.get('workos_user_id')
                and identity.get('workos_session_id') and local.get('csrf')
                and (not workos_user_id or identity['workos_user_id'] == workos_user_id)):
            sources.append((row, local))
    if not sources or len({local['identity']['workos_user_id'] for _, local in sources}) != 1:
        raise ValueError('One explicit completed reader identity is required; finish genuine OAuth first')
    row, source = max(sources, key=lambda item:item[0][1])
    identity = source['identity']
    entitlement = auth._request('GET', '/smn-reader/entitlement', identity['reader_authority'])
    auth._bound(entitlement, identity)
    if entitlement.get('can_read') is not True:
        raise ReaderError('access_required', 'The completed reader does not currently have article access.', 403)
    expires = min(row[1], _expiry(identity['expires_at']), auth.clock() + 8 * 3600)
    if expires <= auth.clock():
        raise ValueError('Completed reader session expired')
    sid = auth._save({'kind':'reader', 'env':'dev', 'identity':identity, 'csrf':secrets.token_urlsafe(32),
                     'qa':True, 'qa_source_session_hash':row[0]}, expires)
    state = {'cookies':[{'name':'smn_reader','value':sid,'domain':'smn-dev.trxstat.com','path':'/',
                         'expires':expires,'httpOnly':True,'secure':True,'sameSite':'Lax'}], 'origins':[]}
    temporary = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=output.parent, prefix='.qa-state-')
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(state, handle);handle.flush();os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    except BaseException:
        auth.session(sid, consume=True)
        if temporary and Path(temporary).exists():Path(temporary).unlink()
        raise
    from datetime import datetime, timezone
    return {'status':'exported', 'expires_at':datetime.fromtimestamp(expires, timezone.utc).isoformat(), 'mode':'genuine_reader_authority_revalidated',
            'central_authority_minted':False}


def cleanup_state(auth, path):
    path = private_path(auth, path)
    state = json.loads(path.read_text('utf-8'))
    cookies = state.get('cookies', [])
    if (len(cookies) != 1 or cookies[0].get('name') != 'smn_reader'
            or cookies[0].get('domain') != 'smn-dev.trxstat.com'):
        raise ValueError('Unexpected reader QA cookie state')
    key = hashlib.sha256(cookies[0]['value'].encode()).hexdigest()
    with auth.database() as db:
        row = db.execute('SELECT payload FROM sessions WHERE id=?', (key,)).fetchone()
        if row and json.loads(row[0]).get('qa') is not True:
            raise ValueError('Cleanup may revoke only the newly issued local QA session')
        db.execute('DELETE FROM sessions WHERE id=?', (key,))
    path.unlink()
    return {'status':'cleaned', 'central_authority_revoked':False, 'original_reader_session_preserved':True}


def configured_auth(env_file):
    from membership_pipeline import load_configuration
    load_configuration(env_file)
    names = {'SMN_READER_CLIENT_ID','SMN_READER_CALLBACK_URL','SMN_READER_AUTHORITY_URL',
             'SMN_READER_SHARED_DEV_CALLBACK','SMN_READER_SERVICE_KEY_FILE'}
    values = {}
    for line in Path(env_file).read_text('utf-8').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):continue
        name,value=line.strip().split('=',1)
        if name in names:
            values[name]=value.strip('"\'')
    if not names - {'SMN_READER_SHARED_DEV_CALLBACK'} <= set(values):
        raise ValueError('Explicit reader service configuration is required')
    config = {name.removeprefix('SMN_READER_'):value for name,value in values.items() if name!='SMN_READER_SERVICE_KEY_FILE'}
    config.update(ENV='dev',SERVICE_KEY=Path(values['SMN_READER_SERVICE_KEY_FILE']).read_text('utf-8').strip())
    return ReaderAuth(Path(os.environ['SMN_READER_PRIVATE_ROOT'])/'sessions',config)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['export','cleanup'])
    parser.add_argument('state',type=Path)
    parser.add_argument('--env-file',type=Path,default=Path('/etc/SMN/membership.env'))
    parser.add_argument('--workos-user-id')
    args=parser.parse_args()
    try:
        auth=configured_auth(args.env_file)
        result=(export_state(auth,args.state,'https://smn-dev.trxstat.com',args.workos_user_id)
                if args.action=='export' else cleanup_state(auth,args.state))
        print(json.dumps(result))
    except (ValueError,ReaderError,OSError,KeyError):
        raise SystemExit('Reader QA state action failed; verify completed OAuth, central access and private configuration.')


if __name__=='__main__':main()
