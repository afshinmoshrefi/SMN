import base64
import hashlib
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

import dashboard_workos
import pub_dashboard
from tests.test_dashboard_auth import DashboardAuthTest, CSRF


class DashboardWorkOSTest(DashboardAuthTest):
    """Direct-login tests using the required-auth fixture and ticket signer."""

    def setUp(self):
        super().setUp()
        self.workos_started = False
        self.workos_env = patch.dict("os.environ", {
            "SMN_DASHBOARD_LOGIN_PROVIDER": "workos",
            "SMN_WORKOS_CLIENT_ID": "client_test",
            "SMN_WORKOS_CALLBACK_URL": "https://smn.test/smn-dashboard/auth/callback",
            "SMN_WORKOS_AUTHORIZATION_URL": "https://tw.test/api/smn/authorize",
        })

    def start_login(self):
        if not self.workos_started:
            self.workos_env.start()
            self.addCleanup(self.workos_env.stop)
            self.workos_started = True
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 302)
        params = parse_qs(urlsplit(response.headers["Location"]).query)
        with self.client.session_transaction() as sess:
            pending = dict(sess["workos_login"])
        return params, pending

    def signed_admin_ticket(self, subject="workos-user-1", session_id="session-1", **claims):
        return self.ticket(workos_user_id=subject, workos_session_id=session_id, **claims)

    def successful_exchange(self, subject="workos-user-1", session_id="session-1", **claims):
        ticket = self.signed_admin_ticket(subject, session_id, **claims)
        token_response = Mock(status_code=200)
        token_response.json.return_value = {
            "user": {"id": subject}, "access_token": "access-secret-token"}
        auth_response = Mock(status_code=200)
        auth_response.json.return_value = {"ticket": ticket}
        return [token_response, auth_response]

    def callback(self, state, **params):
        return self.client.get("/auth/callback", query_string={"state": state, **params})

    def test_successful_route_exchange_sets_admin_session_without_tokens(self):
        params, pending = self.start_login()
        responses = self.successful_exchange()
        with patch.object(dashboard_workos.requests, "post", side_effect=responses) as post:
            response = self.callback(pending["state"], code="auth-code")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        self.assertEqual(post.call_count, 2)
        token_request = post.call_args_list[0].kwargs["json"]
        self.assertEqual(token_request["code_verifier"], pending["verifier"])
        self.assertNotIn("client_secret", token_request)
        self.assertEqual(post.call_args_list[1].kwargs["headers"]["Authorization"],
                         "Bearer access-secret-token")
        who = self.client.get("/api/whoami").get_json()["data"]
        self.assertEqual((who["kind"], who["user_id"]), ("admin", "42"))
        with self.client.session_transaction() as sess:
            self.assertEqual(sess["identity"]["workos_user_id"], "workos-user-1")
            self.assertEqual(sess["identity"]["workos_session_id"], "session-1")
            self.assertNotIn("access_token", sess)
            self.assertNotIn("refresh_token", sess)
            self.assertNotIn("access-secret-token", repr(dict(sess)))
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(pending["verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["code_challenge"], [expected_challenge])

    def test_missing_invalid_expired_and_cross_environment_state_fail_closed(self):
        for case in ("missing", "invalid", "expired", "cross-env"):
            with self.subTest(case=case):
                self.client = pub_dashboard.app.test_client()
                _, pending = self.start_login()
                state = pending["state"]
                if case == "missing":
                    state = ""
                elif case == "invalid":
                    state = "wrong-state"
                elif case == "expired":
                    with patch.object(dashboard_workos.time, "time", return_value=pending["started"] + 601):
                        response = self.callback(state, code="auth-code")
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(self.client.get("/api/whoami").status_code, 401)
                    continue
                elif case == "cross-env":
                    with self.client.session_transaction() as sess:
                        pending = dict(sess["workos_login"])
                        pending["env"] = "prod"
                        sess["workos_login"] = pending
                with patch.object(dashboard_workos.requests, "post") as post:
                    response = self.callback(state, code="auth-code")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(post.call_count, 0)
                self.assertEqual(self.client.get("/api/whoami").status_code, 401)

    def test_callback_replay_does_not_authenticate(self):
        _, pending = self.start_login()
        with patch.object(dashboard_workos.requests, "post", side_effect=self.successful_exchange()) as post:
            self.assertEqual(self.callback(pending["state"], code="once").status_code, 302)
            self.assertEqual(self.callback(pending["state"], code="once").status_code, 403)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(self.client.get("/api/whoami").status_code, 401)

    def test_wrong_signed_subject_and_non_admin_are_denied(self):
        token_response = Mock(status_code=200)
        token_response.json.return_value = {
            "user": {"id": "workos-user-1"}, "access_token": "access-secret-token"}
        wrong_subject = Mock(status_code=200)
        wrong_subject.json.return_value = {
            "ticket": self.signed_admin_ticket(subject="different-user")}
        with self.subTest(label="wrong signed subject"):
            _, pending = self.start_login()
            with patch.object(dashboard_workos.requests, "post",
                              side_effect=[token_response, wrong_subject]):
                response = self.callback(pending["state"], code="auth-code")
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.client.get("/api/whoami").status_code, 401)

        with self.subTest(label="non-admin"):
            _, pending = self.start_login()
            denied = Mock(status_code=403)
            with patch.object(dashboard_workos.requests, "post",
                              side_effect=[token_response, denied]) as post:
                response = self.callback(pending["state"], code="auth-code")
            self.assertEqual(response.status_code, 403)
            self.assertIn("administrator access", response.get_data(as_text=True))
            self.assertEqual(post.call_count, 2)
            self.assertEqual(self.client.get("/api/whoami").status_code, 401)

    def test_network_failure_fails_closed(self):
        import requests
        _, pending = self.start_login()
        with patch.object(dashboard_workos.requests, "post", side_effect=requests.Timeout("private detail")):
            response = self.callback(pending["state"], code="auth-code")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("private detail", response.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/whoami").status_code, 401)

    def test_logout_redirects_to_workos_and_clears_local_session(self):
        _, pending = self.start_login()
        with patch.object(dashboard_workos.requests, "post", side_effect=self.successful_exchange()):
            self.callback(pending["state"], code="auth-code")
        with patch.object(dashboard_workos.requests, "post") as post:
            response = self.client.post("/logout", headers=CSRF)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.headers["Location"]).scheme, "https")
        self.assertEqual(urlsplit(response.headers["Location"]).netloc, "api.workos.com")
        params = parse_qs(urlsplit(response.headers["Location"]).query)
        self.assertEqual(params["session_id"], ["session-1"])
        self.assertEqual(params["return_to"], ["https://smn.test/smn-dashboard/signed-out"])
        post.assert_not_called()
        self.assertEqual(self.client.get("/api/whoami").status_code, 401)
        signed_out = self.client.get("/signed-out")
        self.assertEqual(signed_out.status_code, 200)
        self.assertIn("logged out", signed_out.get_data(as_text=True))

    def test_logout_with_invalid_workos_config_still_clears_local_session(self):
        _, pending = self.start_login()
        with patch.object(dashboard_workos.requests, "post", side_effect=self.successful_exchange()):
            self.callback(pending["state"], code="auth-code")
        with patch.dict("os.environ", {"SMN_WORKOS_CALLBACK_URL": "http://bad.test/callback"}):
            response = self.client.post("/logout", headers=CSRF)
        self.assertEqual(response.status_code, 200)
        self.assertIn("sign-out is unavailable", response.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/whoami").status_code, 401)

    def test_callback_redirect_honors_proxy_prefix(self):
        _, pending = self.start_login()
        with patch.object(dashboard_workos.requests, "post", side_effect=self.successful_exchange()):
            response = self.client.get("/auth/callback", query_string={
                "state": pending["state"], "code": "auth-code"},
                headers={"X-Forwarded-Prefix": "/smn-dashboard"})
        self.assertEqual(response.headers["Location"], "/smn-dashboard/")
