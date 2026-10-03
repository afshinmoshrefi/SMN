"""Reader-only PKCE and opaque server-side sessions; no administrator credentials."""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlencode, urlsplit, unquote

import requests


class ReaderError(Exception):
    def __init__(self, code, message, status=503):
        super().__init__(message)
        self.code, self.status = code, status


def return_path(value):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return '/'
    parsed = urlsplit(value or '/')
    path = parsed.path
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not path.startswith('/')
            or path.startswith('//') or '\\' in path or unquote(path) != path
            or '\x00' in path or any(p in ('.', '..') for p in path.split('/'))):
        return '/'
    if path != '/' and not path.startswith(('/articles/', '/editions/')):
        return '/'
    return path


def _expiry(value):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError()
        return date.timestamp()
    except (ValueError, AttributeError, TypeError):
        raise ReaderError('invalid_authority', 'Reader access could not be verified.') from None


class ReaderAuth:
    def __init__(self, root, config, transport=None, clock=None):
        self.root = Path(root)
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise ReaderError('not_configured', 'Reader session storage is invalid.')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.config = config
        self.transport = transport or requests.Session()
        self.clock = clock or time.time
        with self.database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY,expires REAL NOT NULL,payload TEXT NOT NULL)')

    @contextmanager
    def database(self):
        if (self.root / 'sessions.sqlite3').is_symlink():
            raise ReaderError('not_configured', 'Reader session storage is invalid.')
        db = sqlite3.connect(str(self.root / 'sessions.sqlite3'), timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def settings(self):
        cfg = self.config
        if not all(cfg.get(k) for k in ('CLIENT_ID', 'CALLBACK_URL', 'AUTHORITY_URL', 'SERVICE_KEY', 'ENV')):
            raise ReaderError('not_configured', 'Reader sign-in is not configured yet.')
        callback, authority = urlsplit(cfg['CALLBACK_URL']), urlsplit(cfg['AUTHORITY_URL'])
        for parsed in (callback, authority):
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ReaderError('not_configured', 'Reader sign-in configuration is invalid.')
        if callback.path != '/member/callback' or authority.path not in ('', '/') or cfg['ENV'] not in ('dev', 'prod'):
            raise ReaderError('not_configured', 'Reader sign-in configuration is invalid.')
        return cfg

    def _save(self, payload, expires):
        sid = secrets.token_urlsafe(48)
        with self.database() as db:
            db.execute('DELETE FROM sessions WHERE expires<=?', (self.clock(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(sid.encode()).hexdigest(), expires, json.dumps(payload)))
        return sid

    def session(self, sid, consume=False):
        if not isinstance(sid, str) or not sid or len(sid) > 200:
            return None
        key = hashlib.sha256(sid.encode()).hexdigest()
        with self.database() as db:
            row = db.execute('SELECT expires,payload FROM sessions WHERE id=?', (key,)).fetchone()
            if consume or row and row[0] <= self.clock():
                db.execute('DELETE FROM sessions WHERE id=?', (key,))
        if not row or row[0] <= self.clock():
            return None
        value = json.loads(row[1])
        if value.get('env') != self.config.get('ENV'):
            return None
        return value

    def begin(self, target='/'):
        cfg = self.settings()
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        sid = self._save({'kind': 'pending', 'state': state, 'verifier': verifier,
                          'return_path': return_path(target), 'env': cfg['ENV']}, self.clock() + 600)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        url = 'https://api.workos.com/user_management/authorize?' + urlencode({
            'client_id': cfg['CLIENT_ID'], 'redirect_uri': cfg['CALLBACK_URL'], 'provider': 'authkit',
            'response_type': 'code', 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})
        return sid, url

    def _request(self, method, path, bearer, payload=None):
        cfg = self.settings()
        try:
            response = self.transport.request(method, cfg['AUTHORITY_URL'].rstrip('/') + path,
                headers={'Authorization': 'Bearer ' + bearer, 'X-SMN-Reader-Key': cfg['SERVICE_KEY']},
                json=payload, timeout=10, allow_redirects=False)
            if response.status_code in (401, 403):
                raise ReaderError('access_required', 'Sign in again to verify your reader access.', 401)
            if response.status_code != 200:
                raise ReaderError('provider_unavailable', 'Reader access is temporarily unavailable. Please retry.')
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (requests.RequestException, ValueError, KeyError, TypeError):
            raise ReaderError('provider_unavailable', 'Reader access is temporarily unavailable. Please retry.') from None

    def complete(self, sid, args):
        pending = self.session(sid, consume=True)
        if (not pending or pending.get('kind') != 'pending' or not args.get('state')
                or not hmac.compare_digest(str(args['state']).encode(), pending['state'].encode())
                or args.get('error') or not args.get('code')):
            raise ReaderError('invalid_login', 'Sign-in expired or was not completed. Please start again.', 400)
        cfg = self.settings()
        try:
            response = self.transport.request('POST', 'https://api.workos.com/user_management/authenticate',
                json={'client_id': cfg['CLIENT_ID'], 'grant_type': 'authorization_code',
                      'code': args['code'], 'code_verifier': pending['verifier']}, timeout=10, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError()
            data = response.json()
            subject, token = data['user']['id'], data['access_token']
            if not isinstance(subject, str) or not subject or not isinstance(token, str) or not token:
                raise ValueError()
            identity = self._request('POST', '/smn-reader/authorize', token)
            if (identity.get('workos_user_id') != subject or not identity.get('workos_session_id')
                    or identity.get('env') != cfg['ENV'] or not identity.get('user_id')
                    or not isinstance(identity.get('reader_authority'), str) or not identity['reader_authority']):
                raise ValueError()
            expires = _expiry(identity['expires_at'])
            if not self.clock() < expires <= self.clock() + 8 * 3600 + 5:
                raise ValueError()
            new_sid = self._save({'kind': 'reader', 'env': cfg['ENV'], 'identity': identity,
                                 'csrf': secrets.token_urlsafe(32)}, expires)
            return new_sid, pending['return_path']
        except (requests.RequestException, ValueError, KeyError, TypeError):
            raise ReaderError('invalid_login', 'Sign-in could not be verified. Please retry.', 403) from None

    def entitlement(self, sid):
        local = self.session(sid)
        if not local or local.get('kind') != 'reader':
            return {'can_read': False, 'reason': 'unauthenticated'}, None
        ident = local['identity']
        result = self._request('GET', '/smn-reader/entitlement', ident['reader_authority'])
        self._bound(result, ident)
        if type(result.get('can_read')) is not bool:
            raise ReaderError('invalid_authority', 'Reader access could not be verified.')
        if result['can_read'] and result.get('access_ends_at') is not None and _expiry(result['access_ends_at']) <= self.clock():
            result = dict(result, can_read=False, reason='expired')
        return result, local

    def _bound(self, result, identity):
        for key in ('user_id', 'workos_user_id', 'workos_session_id', 'env', 'expires_at'):
            if result.get(key) != identity.get(key):
                raise ReaderError('invalid_authority', 'Reader identity did not match this session.')
        if _expiry(result['expires_at']) <= self.clock():
            raise ReaderError('access_required', 'Sign in again to verify your reader access.', 401)

    def account(self, sid):
        entitlement, local = self.entitlement(sid)
        if not local:
            raise ReaderError('unauthenticated', 'Please sign in to view your account.', 401)
        account = self._request('GET', '/smn-reader/account', local['identity']['reader_authority'])
        if not isinstance(account.get('entitlement'), dict) or not isinstance(account.get('identity'), dict):
            raise ReaderError('invalid_authority', 'Reader account could not be verified.')
        self._bound(account['entitlement'], local['identity'])
        if account['identity'].get('user_id') != local['identity']['user_id']:
            raise ReaderError('invalid_authority', 'Reader account did not match this session.')
        return account, local

    def action(self, sid, csrf, path, payload):
        local = self.session(sid)
        if (not local or local.get('kind') != 'reader' or not isinstance(csrf, str)
                or not hmac.compare_digest(csrf.encode(), local['csrf'].encode())):
            raise ReaderError('invalid_request', 'This request is invalid. Please reload.', 403)
        if path not in ('/smn-reader/checkout', '/smn-reader/cancel'):
            raise ReaderError('invalid_request', 'Unsupported reader operation.', 400)
        return self._request('POST', path, local['identity']['reader_authority'], payload)

    def logout(self, sid, csrf):
        local = self.session(sid)
        if (not local or local.get('kind') != 'reader' or not isinstance(csrf, str)
                or not hmac.compare_digest(csrf.encode(), local['csrf'].encode())):
            raise ReaderError('invalid_request', 'This request is invalid. Please reload.', 403)
        self.session(sid, consume=True)
        ident = local['identity']
        self._request('POST', '/smn-reader/logout', ident['reader_authority'])
        callback = urlsplit(self.settings()['CALLBACK_URL'])
        return 'https://api.workos.com/user_management/sessions/logout?' + urlencode({
            'session_id': ident['workos_session_id'], 'return_to': callback.scheme + '://' + callback.netloc + '/member/signed-out'})
