"""Direct AuthKit login; TradeWave remains the administrator authority.

Successful login starts the dashboard's existing eight-hour local session.
WorkOS access/refresh tokens are never persisted in browser cookies or logs.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlencode, urlsplit

import requests

import dashboard_auth


class LoginError(Exception):
    pass


def enabled():
    return os.environ.get("SMN_DASHBOARD_LOGIN_PROVIDER", "tradewave") == "workos"


def settings():
    values = {name: os.environ.get("SMN_WORKOS_" + name, "").strip()
              for name in ("CLIENT_ID", "API_KEY", "CALLBACK_URL", "AUTHORIZATION_URL")}
    if not all(values.values()) or dashboard_auth.this_env() not in ("dev", "prod"):
        raise LoginError("SMN sign-in is not configured yet.")
    for name in ("CALLBACK_URL", "AUTHORIZATION_URL"):
        parsed = urlsplit(values[name])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LoginError("SMN sign-in configuration is invalid.")
    return values


def begin(session):
    cfg = settings()
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    session["workos_login"] = {"state": state, "verifier": verifier,
                               "started": time.time(), "env": dashboard_auth.this_env()}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return "https://api.workos.com/user_management/authorize?" + urlencode({
        "client_id": cfg["CLIENT_ID"], "redirect_uri": cfg["CALLBACK_URL"],
        "provider": "authkit", "response_type": "code", "screen_hint": "sign-in",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def complete(session, args):
    pending = session.pop("workos_login", None)
    if not isinstance(pending, dict):
        raise LoginError("Start sign-in again from the SMN dashboard.")
    state = args.get("state", "")
    age = time.time() - pending.get("started", 0)
    if (not state or not hmac.compare_digest(state, pending.get("state", ""))
            or not 0 <= age <= 600 or pending.get("env") != dashboard_auth.this_env()):
        raise LoginError("This sign-in request expired or is invalid. Please try again.")
    if args.get("error") or not args.get("code"):
        raise LoginError("Sign-in was not completed. Please try again.")
    cfg = settings()
    try:
        response = requests.post("https://api.workos.com/user_management/authenticate", json={
            "client_id": cfg["CLIENT_ID"], "client_secret": cfg["API_KEY"],
            "grant_type": "authorization_code", "code": args["code"],
            "code_verifier": pending["verifier"]}, timeout=10, allow_redirects=False)
        if response.status_code != 200:
            raise LoginError("Sign-in could not be completed. Please try again.")
        result = response.json()
        subject = result["user"]["id"]
        token = result["access_token"]
        if not isinstance(subject, str) or not subject or not isinstance(token, str) or not token:
            raise LoginError("Sign-in returned an invalid identity.")
        # HTTPS server-to-server exchange. The authority validates WorkOS's JWT
        # and looks up the same super_admin role used by the old browser bridge.
        authorization = requests.post(cfg["AUTHORIZATION_URL"],
            headers={"Authorization": "Bearer " + token}, timeout=10, allow_redirects=False)
        if authorization.status_code == 403:
            raise LoginError("Your account does not have SMN administrator access.")
        if authorization.status_code != 200:
            raise LoginError("Administrator access could not be verified. Please try again.")
        identity = dashboard_auth.verify_ticket(authorization.json()["ticket"])
        if identity.get("workos_user_id") != subject or not identity.get("workos_session_id"):
            raise LoginError("The administrator identity did not match this sign-in.")
        return identity
    except (requests.RequestException, ValueError, KeyError, TypeError, dashboard_auth.TicketError):
        # Never expose provider responses, tokens, or private configuration.
        raise LoginError("Sign-in could not be verified. Please try again.") from None


def revoke(identity):
    sid = (identity or {}).get("workos_session_id")
    if not sid:
        return True
    try:
        response = requests.post("https://api.workos.com/user_management/sessions/revoke",
            headers={"Authorization": "Bearer " + settings()["API_KEY"]},
            json={"session_id": sid}, timeout=10, allow_redirects=False)
        return response.status_code in (200, 204, 404)
    except (requests.RequestException, LoginError):
        return False
