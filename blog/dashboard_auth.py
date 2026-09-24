#!/usr/bin/env python3
"""
dashboard_auth.py — who may use the SMN publishing dashboard.

Three ways in, all checked on every request (see pub_dashboard.guard):

1. TradeWave admin login (people, in a browser)
   TradeWave's web tier sends an admin to  /auth?ticket=<JWT>.  The ticket is
   signed by TradeWave with an Ed25519 PRIVATE key; this box holds only the
   PUBLIC key, so it can check a ticket but never make one.  Checked:
     alg EdDSA, aud "smn-dashboard", iss "tw2-web", env == this box's env
     (dev tickets never open prod), is_admin true, lifetime <= 120 s, and a
     one-time jti.  A good ticket starts an 8-hour cookie session.
2. Named API keys (agents: Codex, Astra, scripts)
   Header  Authorization: Bearer smnd_<id>_<secret>.  Created by an admin in
   the dashboard, shown once, stored only as a SHA-256 hash, revocable.
3. Service key (blog_queue on this box, for the TradeWave portfolio icon)
   Auto-created at $SMN_DASHBOARD_STATE/service.key (root only).  Only this
   key may name another actor with X-Actor.

Settings (environment, e.g. /etc/SMN/dashboard.env):
  SMN_DASHBOARD_AUTH        required (default) | off   (off = no login; tests)
  SMN_DASHBOARD_ENV         dev | prod                 (must match the ticket)
  SMN_DASHBOARD_TW_PUBKEY   path to TradeWave's Ed25519 public key (PEM)
  SMN_DASHBOARD_LOGIN_URL   TradeWave route that issues a ticket
  SMN_DASHBOARD_COOKIE_SECURE  1 (default) | 0 for plain-http testing
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, List, Optional

import article_index
from pin_store import STATE_DIR, iso, utcnow

TICKET_AUDIENCE = "smn-dashboard"
TICKET_ISSUER = "tw2-web"
TICKET_MAX_AGE = 120            # seconds a ticket may live
SESSION_HOURS = 8

KEYS_JSON = STATE_DIR / "api_keys.json"
USED_TICKETS_JSON = STATE_DIR / "used_tickets.json"
SERVICE_KEY_FILE = STATE_DIR / "service.key"
SESSION_SECRET_FILE = STATE_DIR / "session.secret"


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
def auth_required() -> bool:
    return os.environ.get("SMN_DASHBOARD_AUTH", "required").strip().lower() != "off"


def this_env() -> str:
    return os.environ.get("SMN_DASHBOARD_ENV", "").strip().lower()


def login_url() -> str:
    return os.environ.get("SMN_DASHBOARD_LOGIN_URL", "").strip()


def cookie_secure() -> bool:
    return os.environ.get("SMN_DASHBOARD_COOKIE_SECURE", "1").strip() != "0"


def _secret_file(path, nbytes: int = 32) -> str:
    """Read a secret file, creating it (root-only) on first use."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = path.read_text().strip()
        if value:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(nbytes)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")
    return value


def session_secret() -> str:
    return _secret_file(SESSION_SECRET_FILE, 48)


def service_key() -> str:
    return _secret_file(SERVICE_KEY_FILE, 32)


# --------------------------------------------------------------------------- #
# TradeWave admin tickets
# --------------------------------------------------------------------------- #
class TicketError(Exception):
    pass


def _public_key():
    path = os.environ.get("SMN_DASHBOARD_TW_PUBKEY", "").strip()
    if not path:
        raise TicketError("SMN_DASHBOARD_TW_PUBKEY is not set on this server")
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        raise TicketError(f"cannot read TradeWave public key: {exc}")


def _remember_ticket(jti: str, exp: int) -> None:
    """Refuse a ticket that was already used (replay)."""
    now = int(time.time())
    try:
        used = json.loads(USED_TICKETS_JSON.read_text("utf-8"))
    except Exception:
        used = {}
    used = {k: v for k, v in used.items() if int(v) > now}      # forget expired
    if jti in used:
        raise TicketError("this login link was already used; open it again from TradeWave")
    used[jti] = exp
    article_index._atomic_write_json(USED_TICKETS_JSON, used)


def verify_ticket(ticket: str) -> Dict[str, Any]:
    """Check a TradeWave admin ticket.  Returns the identity for the session."""
    import jwt
    if not this_env():
        raise TicketError("SMN_DASHBOARD_ENV is not set on this server")
    try:
        claims = jwt.decode(ticket, _public_key(), algorithms=["EdDSA"],
                            audience=TICKET_AUDIENCE, issuer=TICKET_ISSUER,
                            options={"require": ["exp", "iat", "jti", "sub"]},
                            leeway=10)
    except jwt.ExpiredSignatureError:
        raise TicketError("the login link expired; open the dashboard again from TradeWave")
    except jwt.InvalidTokenError as exc:
        raise TicketError(f"invalid login ticket: {exc}")
    if int(claims["exp"]) - int(claims["iat"]) > TICKET_MAX_AGE:
        raise TicketError("login ticket lives too long")
    if str(claims.get("env", "")).lower() != this_env():
        raise TicketError(f"this is the {this_env()} dashboard; the ticket is for "
                          f"{claims.get('env') or 'no'} environment")
    if claims.get("is_admin") is not True:
        raise TicketError("only TradeWave admins may use the dashboard")
    _remember_ticket(str(claims["jti"]), int(claims["exp"]))
    return {"kind": "admin", "user_id": str(claims["sub"]),
            "name": str(claims.get("name") or claims.get("email") or claims["sub"]),
            "email": str(claims.get("email") or ""), "env": this_env(),
            "login_at": iso(utcnow())}


# --------------------------------------------------------------------------- #
# API keys
# --------------------------------------------------------------------------- #
def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _load_keys() -> List[Dict[str, Any]]:
    try:
        data = json.loads(KEYS_JSON.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_keys(keys: List[Dict[str, Any]]) -> None:
    article_index._atomic_write_json(KEYS_JSON, keys)
    try:
        os.chmod(KEYS_JSON, 0o600)
    except OSError:
        pass


def create_key(name: str, created_by: str) -> Dict[str, Any]:
    name = "".join(c for c in (name or "").strip().lower() if c.isalnum() or c in "-_")[:40]
    if not name:
        raise ValueError("a key needs a name, e.g. codex")
    keys = _load_keys()
    if any(k["name"] == name and not k.get("revoked_at") for k in keys):
        raise ValueError(f"an active key named {name!r} already exists; revoke it first")
    key_id = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    record = {"id": key_id, "name": name, "hash": _hash(secret),
              "created_at": iso(utcnow()), "created_by": created_by,
              "last_used_at": None, "revoked_at": None}
    keys.append(record)
    _save_keys(keys)
    public = {k: v for k, v in record.items() if k != "hash"}
    public["key"] = f"smnd_{key_id}_{secret}"          # shown once, never stored
    return public


def list_keys() -> List[Dict[str, Any]]:
    return [{k: v for k, v in r.items() if k != "hash"} for r in _load_keys()]


def revoke_key(key_id: str, by: str) -> bool:
    keys = _load_keys()
    for record in keys:
        if record["id"] == key_id and not record.get("revoked_at"):
            record["revoked_at"] = iso(utcnow())
            record["revoked_by"] = by
            _save_keys(keys)
            return True
    return False


def check_bearer(header: str) -> Optional[Dict[str, Any]]:
    """Identity for an Authorization: Bearer header, or None."""
    if not header or not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    if hmac.compare_digest(token, service_key()):
        return {"kind": "service", "user_id": "service", "name": "blog_queue"}
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != "smnd":
        return None
    keys = _load_keys()
    for record in keys:
        if record["id"] == parts[1] and not record.get("revoked_at"):
            if hmac.compare_digest(record["hash"], _hash(parts[2])):
                # Record use at most once a minute, to keep writes cheap.
                last = record.get("last_used_at") or ""
                if last[:16] != iso(utcnow())[:16]:
                    record["last_used_at"] = iso(utcnow())
                    _save_keys(keys)
                return {"kind": "agent", "user_id": f"key:{record['id']}",
                        "name": f"agent:{record['name']}"}
    return None
