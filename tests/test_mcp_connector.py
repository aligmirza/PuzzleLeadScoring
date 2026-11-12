"""Hosted MCP connector: the full claude.ai sign-in (dynamic registration, /login with the team password, PKCE
code exchange, refresh) and that no tool runs without a token."""
import base64
import hashlib
import secrets
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

BASE = "http://localhost"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "MCP-Protocol-Version": "2025-06-18"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


class HostedConnector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mcp.server.transport_security import TransportSecuritySettings
        from enrich.integrations import mcp_oauth
        from enrich.integrations.mcp_server import mcp
        cls.mcp = mcp
        cls.saved = (mcp.settings.auth, mcp._auth_server_provider, mcp._token_verifier)
        cls.tmp = Path(tempfile.mkdtemp())
        cls.provider = mcp_oauth.enable(mcp, BASE, "right-password", cls.tmp / "oauth.json")
        app = mcp.streamable_http_app(transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        cls.client = TestClient(app, base_url=BASE)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        m = cls.mcp
        m.settings.auth, m._auth_server_provider, m._token_verifier = cls.saved

    def _sign_in(self, password="right-password"):
        c = self.client
        reg = c.post("/register", json={"redirect_uris": [REDIRECT], "client_name": "Claude",
                                        "token_endpoint_auth_method": "none",
                                        "grant_types": ["authorization_code", "refresh_token"],
                                        "response_types": ["code"]})
        self.assertEqual(reg.status_code, 201, reg.text)
        cid = reg.json()["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        auth = c.get("/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT,
                                           "code_challenge": challenge, "code_challenge_method": "S256",
                                           "state": "xyz"}, follow_redirects=False)
        self.assertIn(auth.status_code, (302, 307), auth.text)
        login = urlparse(auth.headers["location"])
        self.assertEqual(login.path, "/login")
        req = parse_qs(login.query)["req"][0]
        self.assertIn("Connector password", c.get(f"/login?req={req}").text)
        done = c.post("/login", data={"req": req, "password": password}, follow_redirects=False)
        return cid, verifier, done

    def test_sign_in_then_call_a_tool(self):
        cid, verifier, done = self._sign_in()
        self.assertEqual(done.status_code, 302)
        back = urlparse(done.headers["location"])
        self.assertTrue(done.headers["location"].startswith(REDIRECT))
        q = parse_qs(back.query)
        self.assertEqual(q["state"], ["xyz"])
        tok = self.client.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0],
                                               "redirect_uri": REDIRECT, "client_id": cid, "code_verifier": verifier})
        self.assertEqual(tok.status_code, 200, tok.text)
        access, refresh = tok.json()["access_token"], tok.json()["refresh_token"]
        ok = self.client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": f"Bearer {access}"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn("puzzle-lead-scoring", ok.text)
        again = self.client.post("/token", data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue((self.tmp / "oauth.json").exists())  # survives a restart

    def test_no_token_no_tools(self):
        self.assertEqual(self.client.post("/mcp", json=INIT, headers=MCP_HEADERS).status_code, 401)
        bad = self.client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": "Bearer made-up"})
        self.assertEqual(bad.status_code, 401)

    def test_wrong_password_is_refused(self):
        _, _, done = self._sign_in(password="guess")
        self.assertEqual(done.status_code, 401)
        self.assertIn("Wrong password", done.text)
