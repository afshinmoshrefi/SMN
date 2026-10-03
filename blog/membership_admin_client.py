"""Server-held membership administration authority for the publishing dashboard."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

import requests

import dashboard_auth
import pin_store


class MembershipError(Exception):
    def __init__(self, code, message, status=503):
        super().__init__(message)
        self.code, self.status = code, status


def _configuration():
    base = os.environ.get('SMN_MEMBERSHIP_API_BASE', '').rstrip('/')
    key_path = os.environ.get('SMN_MEMBERSHIP_ADMIN_KEY_FILE', '')
    parsed = urlsplit(base)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment
            or not key_path or dashboard_auth.this_env() not in ('dev', 'prod')):
        raise MembershipError('membership_not_configured', 'Membership administration is not configured.')
    try:
        key = Path(key_path).read_text(encoding='utf-8').strip()
    except OSError:
        key = ''
    if not key:
        raise MembershipError('membership_not_configured', 'Membership administration is not configured.')
    return base, key


def configured():
    try:
        _configuration()
        return True
    except MembershipError:
        return False


@contextmanager
def _database():
    path = pin_store.STATE_DIR / 'membership-admin-sessions.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise MembershipError('membership_unavailable', 'Membership session storage is unavailable.')
    connection = None
    try:
        connection = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        connection.row_factory = sqlite3.Row
        connection.execute('''CREATE TABLE IF NOT EXISTS admin_sessions (
            sid_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, workos_session_id TEXT NOT NULL,
            environment TEXT NOT NULL, authority TEXT NOT NULL, csrf_token TEXT NOT NULL,
            expires_at REAL NOT NULL)''')
        connection.execute('DELETE FROM admin_sessions WHERE expires_at <= ?', (time.time(),))
        yield connection
        connection.commit()
    except (sqlite3.Error, OSError):
        if connection:
            connection.rollback()
        raise MembershipError('membership_unavailable', 'Membership session storage is unavailable.') from None
    finally:
        if connection:
            connection.close()


def _expiration(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError('invalid expiration')
        return float(value)
    if not isinstance(value, str):
        raise ValueError('invalid expiration')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('expiration requires timezone')
    return parsed.timestamp()


def _remote(method, path, *, token, body=None, csrf=None):
    base, key = _configuration()
    if path not in {'/smn-admin/authorize', '/smn-admin/settings', '/smn-admin/activate',
                    '/smn-admin/logout'}:
        raise ValueError('unknown membership administration operation')
    headers = {'X-SMN-Admin-Key': key, 'Authorization': 'Bearer ' + token}
    if csrf:
        headers['X-SMN-CSRF'] = csrf
    try:
        response = requests.request(method, base + path, headers=headers, json=body,
                                    timeout=15, allow_redirects=False)
    except requests.RequestException:
        raise MembershipError('membership_unavailable', 'Membership service is temporarily unavailable.') from None
    if response.status_code in (401, 403):
        raise MembershipError('membership_signin_required', 'Sign in again to manage membership.', 403)
    if response.status_code == 409:
        raise MembershipError('membership_conflict', 'Settings changed. Reload before saving again.', 409)
    if response.status_code in (400, 422):
        raise MembershipError('invalid_membership_settings', 'The membership settings are invalid.', 400)
    if response.status_code != 200:
        raise MembershipError('membership_unavailable', 'Membership service could not complete this operation.')
    try:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise MembershipError('membership_unavailable', 'Membership service returned an invalid response.') from None


def bootstrap(workos_token, identity):
    """Return an opaque lookup ID; provider and central authority never enter cookies."""
    if not configured():
        return None
    value = _remote('POST', '/smn-admin/authorize', token=workos_token)
    try:
        if (str(value['user_id']) != str(identity['user_id'])
                or value['workos_user_id'] != identity['workos_user_id']
                or value['workos_session_id'] != identity['workos_session_id']
                or value['env'] != dashboard_auth.this_env()):
            raise ValueError()
        authority, csrf = value['admin_authority'], value['csrf_token']
        if not all(isinstance(item, str) and 16 <= len(item) <= 16384 for item in (authority, csrf)):
            raise ValueError()
        expires = min(_expiration(value['expires_at']), time.time() + 8 * 3600)
        if expires <= time.time():
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise MembershipError('membership_unavailable', 'Membership authority did not match this sign-in.') from None
    sid = secrets.token_urlsafe(32)
    with _database() as connection:
        connection.execute('INSERT INTO admin_sessions VALUES (?,?,?,?,?,?,?)',
            (hashlib.sha256(sid.encode()).hexdigest(), str(identity['user_id']),
             identity['workos_session_id'], value['env'], authority, csrf, expires))
    return sid


def _authority(identity):
    sid = identity.get('membership_admin_sid')
    if identity.get('kind') != 'admin' or not isinstance(sid, str) or not 20 <= len(sid) <= 100:
        raise MembershipError('membership_signin_required', 'Sign in again to manage membership.', 403)
    with _database() as connection:
        row = connection.execute('SELECT * FROM admin_sessions WHERE sid_hash=?',
            (hashlib.sha256(sid.encode()).hexdigest(),)).fetchone()
    if (not row or row['expires_at'] <= time.time()
            or row['user_id'] != str(identity.get('user_id'))
            or row['workos_session_id'] != identity.get('workos_session_id')
            or row['environment'] != dashboard_auth.this_env()):
        raise MembershipError('membership_signin_required', 'Sign in again to manage membership.', 403)
    return dict(row)


def request_settings(identity, method='GET', body=None, activate=False):
    authority = _authority(identity)
    return _remote(method, '/smn-admin/activate' if activate else '/smn-admin/settings',
        token=authority['authority'], csrf=authority['csrf_token'] if method != 'GET' else None,
        body=body)


def logout(identity):
    sid = identity.get('membership_admin_sid')
    if not isinstance(sid, str):
        return
    with _database() as connection:
        connection.execute('DELETE FROM admin_sessions WHERE sid_hash=?',
            (hashlib.sha256(sid.encode()).hexdigest(),))
