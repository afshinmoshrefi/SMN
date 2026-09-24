import time
import uuid
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard_auth
import pin_store
import pub_dashboard
from tests.test_pub_dashboard import DashboardFixture

CSRF = {"X-SMN-Dashboard": "1"}


def _pem_public(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)


class DashboardAuthTest(DashboardFixture):
    """Login is REQUIRED in these tests (the fixture turns it off; we turn it on)."""

    def setUp(self):
        super().setUp()
        self.tw_key = Ed25519PrivateKey.generate()          # TradeWave's private key
        pub = self.state / "tw.pub"
        self.state.mkdir(parents=True, exist_ok=True)
        pub.write_bytes(_pem_public(self.tw_key))
        env = patch.dict("os.environ", {
            "SMN_DASHBOARD_AUTH": "required", "SMN_DASHBOARD_ENV": "dev",
            "SMN_DASHBOARD_TW_PUBKEY": str(pub), "SMN_DASHBOARD_COOKIE_SECURE": "0",
            "SMN_DASHBOARD_LOGIN_URL": "https://tw.test/smn-dashboard/login"})
        env.start()
        self.addCleanup(env.stop)
        for name, path in (("KEYS_JSON", "api_keys.json"), ("USED_TICKETS_JSON", "used.json"),
                           ("SERVICE_KEY_FILE", "service.key"),
                           ("SESSION_SECRET_FILE", "session.secret")):
            p = patch.object(dashboard_auth, name, self.state / path)
            p.start()
            self.addCleanup(p.stop)
        self.client = pub_dashboard.app.test_client()

    def ticket(self, key=None, **over):
        now = int(time.time())
        claims = {"iss": "tw2-web", "aud": "smn-dashboard", "sub": "42", "name": "Afshin",
                  "email": "a@x", "env": "dev", "is_admin": True, "iat": now,
                  "exp": now + 60, "jti": uuid.uuid4().hex}
        claims.update(over)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or self.tw_key, algorithm="EdDSA")

    def login(self, **over):
        return self.client.get("/auth?ticket=" + self.ticket(**over))

    # ------------------------------------------------------------ no login
    def test_everything_needs_login(self):
        self.assertEqual(self.client.get("/api/articles").status_code, 401)
        self.assertEqual(self.client.post("/api/articles/a1/unpublish", json={}).status_code, 401)
        r = self.client.get("/")
        self.assertEqual((r.status_code, r.headers["Location"]),
                         (302, "https://tw.test/smn-dashboard/login"))

    def test_public_paths_stay_open(self):
        self.assertEqual(self.client.get("/llms.txt").status_code, 200)
        self.assertEqual(self.client.get("/api/health").get_json()["data"], {"status": "ok"})

    # ------------------------------------------------------------ tickets
    def test_good_ticket_logs_in_and_names_the_actor(self):
        r = self.login()
        self.assertEqual(r.status_code, 302)
        who = self.client.get("/api/whoami").get_json()["data"]
        self.assertEqual((who["kind"], who["name"], who["user_id"], who["env"]),
                         ("admin", "Afshin", "42", "dev"))
        r = self.client.put("/api/pins/a1", json={"position": 1},
                            headers={**CSRF, "X-Actor": "someone-else"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["data"]["pin"]["created_by"], "Afshin (tw:42)")

    def test_bad_tickets_are_refused(self):
        other = Ed25519PrivateKey.generate()
        now = int(time.time())
        cases = {
            "prod ticket on dev": self.ticket(env="prod"),
            "not an admin": self.ticket(is_admin=False),
            "expired": self.ticket(iat=now - 300, exp=now - 200),
            "wrong audience": self.ticket(aud="tw2-appserver"),
            "wrong issuer": self.ticket(iss="someone"),
            "signed by another key": self.ticket(key=other),
            "lives too long": self.ticket(exp=now + 3600),
            "no jti": self.ticket(jti=None),
            "HS256 with public key as secret": jwt.encode(
                {"iss": "tw2-web", "aud": "smn-dashboard", "sub": "1", "env": "dev",
                 "is_admin": True, "iat": now, "exp": now + 60, "jti": "x"},
                "not-the-key", algorithm="HS256"),
            "garbage": "abc.def.ghi",
        }
        for label, ticket in cases.items():
            with self.subTest(label):
                r = self.client.get("/auth?ticket=" + ticket)
                self.assertEqual(r.status_code, 403, label)
                self.assertEqual(self.client.get("/api/whoami").status_code, 401, label)

    def test_ticket_cannot_be_replayed(self):
        ticket = self.ticket()
        self.assertEqual(self.client.get("/auth?ticket=" + ticket).status_code, 302)
        fresh = pub_dashboard.app.test_client()
        self.assertEqual(fresh.get("/auth?ticket=" + ticket).status_code, 403)

    def test_session_expires_after_8_hours(self):
        self.login()
        with self.client.session_transaction() as sess:
            ident = dict(sess["identity"])
            ident["login_at"] = pin_store.iso(pin_store.utcnow() - timedelta(hours=9))
            sess["identity"] = ident
        self.assertEqual(self.client.get("/api/articles").status_code, 401)

    def test_writes_need_the_page_header(self):
        self.login()
        r = self.client.post("/api/articles/a1/unpublish", json={})
        self.assertEqual(r.get_json()["error"]["code"], "csrf")
        r = self.client.post("/api/articles/a1/unpublish", json={}, headers=CSRF)
        self.assertEqual(r.status_code, 200)

    def test_logout(self):
        self.login()
        self.client.get("/logout")
        self.assertEqual(self.client.get("/api/articles").status_code, 401)

    def test_prefix_behind_nginx(self):
        r = self.client.get("/auth?ticket=" + self.ticket(),
                            headers={"X-Forwarded-Prefix": "/dashboard"})
        self.assertEqual(r.headers["Location"], "/dashboard/")

    def test_service_key_file_exists_at_startup(self):
        import importlib
        with patch.object(dashboard_auth, "SERVICE_KEY_FILE", self.state / "fresh.key"):
            importlib.reload(pub_dashboard)
            self.assertTrue((self.state / "fresh.key").is_file())
        importlib.reload(pub_dashboard)

    # ------------------------------------------------------------ API keys
    def test_api_key_lifecycle(self):
        self.login()
        key = self.client.post("/api/keys", json={"name": "Codex"}, headers=CSRF).get_json()["data"]
        self.assertEqual(key["name"], "codex")
        self.assertNotIn("hash", key)
        self.assertNotIn(key["key"].split("_", 2)[2], (self.state / "api_keys.json").read_text())

        agent = pub_dashboard.app.test_client()
        auth = {"Authorization": "Bearer " + key["key"], "X-Actor": "pretend-admin"}
        self.assertEqual(agent.get("/api/articles?limit=1", headers=auth).status_code, 200)
        r = agent.put("/api/pins/a2", json={"position": 2}, headers=auth)     # no CSRF needed
        self.assertEqual(r.get_json()["data"]["pin"]["created_by"], "agent:codex")
        self.assertEqual(agent.get("/api/keys", headers=auth).status_code, 403)  # not an admin

        self.client.delete(f"/api/keys/{key['id']}", headers=CSRF)
        self.assertEqual(agent.get("/api/articles", headers=auth).status_code, 401)

    def test_wrong_key_is_refused(self):
        bad = {"Authorization": "Bearer smnd_deadbeef_nope"}
        self.assertEqual(self.client.get("/api/articles", headers=bad).status_code, 401)

    def test_service_key_may_name_the_actor(self):
        svc = {"Authorization": "Bearer " + dashboard_auth.service_key(), "X-Actor": "tw2-user-7"}
        r = self.client.post("/api/articles/a3/unpublish", json={}, headers=svc)
        self.assertEqual(r.status_code, 200)
        actor = self.client.get("/api/audit?slug=a3", headers=svc).get_json()["data"][0]["actor"]
        self.assertEqual(actor, "tw2-user-7")


if __name__ == "__main__":
    unittest.main()
