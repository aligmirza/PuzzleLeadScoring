"""Sign-in for the hosted MCP connector (claude.ai: Settings > Connectors > Add custom connector).

    python -m enrich mcp --http --public-url https://<server address>

Claude registers itself (OAuth dynamic client registration) and sends the person to /login on this server, where
they enter the team's connector password (MCP_CONNECTOR_PASSWORD in .env, created on the first run). Claude then
gets an access token (1 day) and a refresh token (30 days), so each person signs in once. Without a valid token no
tool runs: the tools can spend Prospeo credits and push to Clay. Clients and tokens are kept in
cache/mcp_oauth.json (git-ignored); the short-lived sign-in requests and codes only in memory.
"""
import hmac
import html
import json
import os
import secrets
import time
from pathlib import Path

import anyio
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams,
                                      OAuthAuthorizationServerProvider, ProviderTokenVerifier, RefreshToken,
                                      construct_redirect_uri)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

ACCESS_SECONDS = 24 * 3600
REFRESH_SECONDS = 30 * 24 * 3600
LOGIN_SECONDS = 600
MAX_TRIES = 5


class PasswordOAuth(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, base_url: str, password: str, path: Path):
        self.base, self.password, self.path = base_url.rstrip("/"), password, path
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.pending: dict[str, dict] = {}  # sign-in request id -> client, params, expiry, tries left
        self._load()

    # storage -----------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        now = time.time()
        self.clients = {k: OAuthClientInformationFull.model_validate(v) for k, v in data.get("clients", {}).items()}
        self.access = {k: a for k, v in data.get("access", {}).items() if (a := AccessToken.model_validate(v)).expires_at > now}
        self.refresh = {k: r for k, v in data.get("refresh", {}).items() if (r := RefreshToken.model_validate(v)).expires_at > now}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"clients": {k: v.model_dump(mode="json") for k, v in self.clients.items()},
                "access": {k: v.model_dump(mode="json") for k, v in self.access.items()},
                "refresh": {k: v.model_dump(mode="json") for k, v in self.refresh.items()}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # Windows: permissions work differently
            pass
        tmp.replace(self.path)

    # clients -----------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.clients[client_info.client_id] = client_info
        self._save()

    # sign-in -----------------------------------------------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v["expires"] > now}
        rid = secrets.token_urlsafe(24)
        self.pending[rid] = {"client_id": client.client_id, "params": params, "expires": now + LOGIN_SECONDS,
                             "tries": MAX_TRIES}
        return f"{self.base}/login?req={rid}"

    def check_password(self, rid: str, password: str) -> tuple[str | None, str]:
        """(redirect URL back to Claude, '') on the right password, else (None, why)."""
        p = self.pending.get(rid)
        if not p or p["expires"] < time.time():
            self.pending.pop(rid, None)
            return None, "This sign-in link has expired. Start again from Claude."
        if not hmac.compare_digest(password.encode(), self.password.encode()):
            p["tries"] -= 1
            if p["tries"] <= 0:
                self.pending.pop(rid, None)
                return None, "Too many wrong passwords. Start again from Claude."
            return None, f"Wrong password ({p['tries']} tries left)."
        self.pending.pop(rid)
        params: AuthorizationParams = p["params"]
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(
            code=code, scopes=params.scopes or [], expires_at=time.time() + 300, client_id=p["client_id"],
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly, resource=params.resource)
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state), ""

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        c = self.codes.get(authorization_code)
        return c if c and c.client_id == client.client_id and c.expires_at > time.time() else None

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code) -> OAuthToken:
        self.codes.pop(authorization_code.code, None)
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    # tokens ------------------------------------------------------------
    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        now = int(time.time())
        at, rt = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.access[at] = AccessToken(token=at, client_id=client_id, scopes=scopes, expires_at=now + ACCESS_SECONDS,
                                      resource=resource)
        self.refresh[rt] = RefreshToken(token=rt, client_id=client_id, scopes=scopes,
                                        expires_at=now + REFRESH_SECONDS, resource=resource)
        self._save()
        return OAuthToken(access_token=at, token_type="Bearer", expires_in=ACCESS_SECONDS, refresh_token=rt,
                          scope=" ".join(scopes) or None)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        r = self.refresh.get(refresh_token)
        return r if r and r.client_id == client.client_id and r.expires_at > time.time() else None

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token, scopes: list[str]) -> OAuthToken:
        self.refresh.pop(refresh_token.token, None)
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        a = self.access.get(token)
        return a if a and a.expires_at > time.time() else None

    async def revoke_token(self, token) -> None:
        self.access.pop(token.token, None)
        self.refresh.pop(token.token, None)
        self._save()


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Puzzle Lead Scoring: sign in</title><style>body{{font-family:system-ui,sans-serif;max-width:380px;margin:12vh auto;padding:0 16px;color:#222}}
input,button{{font-size:16px;padding:10px;width:100%;box-sizing:border-box;margin-top:8px}}button{{cursor:pointer}}.err{{color:#b00}}</style></head>
<body><h2>Puzzle Lead Scoring</h2><p>Enter the team's connector password to connect Claude.</p>{error}
<form method="post" action="/login"><input type="hidden" name="req" value="{req}">
<input type="password" name="password" placeholder="Connector password" autofocus required>
<button type="submit">Connect</button></form></body></html>"""


def enable(mcp, public_url: str, password: str, store: Path) -> PasswordOAuth:
    """Turn sign-in on for this MCP server (HTTP only; stdio for Claude Code and Claude Desktop needs none)."""
    provider = PasswordOAuth(public_url, password, store)
    mcp.settings.auth = AuthSettings(
        issuer_url=public_url, resource_server_url=f"{public_url.rstrip('/')}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True))
    # the server takes these at construction; the stdio server is built without them, so set them here
    mcp._auth_server_provider = provider
    mcp._token_verifier = ProviderTokenVerifier(provider)

    @mcp.custom_route("/login", methods=["GET", "POST"])
    async def login(request: Request) -> Response:
        if request.method == "GET":
            req = request.query_params.get("req", "")
            return HTMLResponse(PAGE.format(req=html.escape(req), error=""))
        form = await request.form()
        req, pw = str(form.get("req", "")), str(form.get("password", ""))
        url, why = provider.check_password(req, pw)
        if url:
            return RedirectResponse(url, status_code=302)
        if why.startswith("Wrong"):
            await anyio.sleep(1)  # slow down guessing
            return HTMLResponse(PAGE.format(req=html.escape(req), error=f'<p class="err">{html.escape(why)}</p>'), 401)
        return HTMLResponse(f"<p>{html.escape(why)}</p>", 400)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return Response("ok")

    return provider
