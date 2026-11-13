"""Settings page for the hosted connector: add or change API keys in a browser, no terminal.

    https://<connector address>/settings        (python -m enrich mcp --http --public-url https://<connector address>)

Signed in with the ADMIN password (MCP_ADMIN_PASSWORD in .env, created on the first run), not the team's connector
password: everyone who uses the connector knows that one, and they shouldn't all be able to replace the keys.

What it does: one box per key in settings.EDITABLE (LeadMagic, Clay, OpenAI, Prospeo, caps), all optional. A key is never shown
back, only "set" or "not set". A box left empty keeps the key; "Remove" clears it. Saved keys go to .env (mode 600)
and into this running server at once; the API server (`python -m enrich serve`) reads .env when it starts, so
restart it to use them there.

Safety: the page needs HTTPS (the public URL is HTTPS; plain http only on this computer), the sign-in is a random
session cookie (HttpOnly, SameSite=Strict, Secure, 30 minutes), every form carries a per-session token and must come
from this site, wrong passwords wait 1 second and lock that address out for 10 minutes after 5 tries, and values
are checked before they are written (no line breaks, so nothing can add extra lines to .env).
"""
import hmac
import html
import os
import re
import secrets
import time
from urllib.parse import urlparse

import anyio
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from ..core import settings

SESSION_SECONDS = 30 * 60
MAX_TRIES = 5
LOCK_SECONDS = 10 * 60
COOKIE = "lps_admin"
HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
           "X-Content-Type-Options": "nosniff",
           "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"}
STYLE = """body{font-family:system-ui,sans-serif;max-width:640px;margin:6vh auto;padding:0 16px;color:#222}
input,button{font-size:15px;padding:9px;box-sizing:border-box}input[type=password],input[type=text]{width:100%}
label{font-weight:600;display:block;margin-top:18px}small{color:#666;display:block;margin:2px 0 6px}
.set{color:#0a7d32}.unset{color:#999}.err{color:#b00}.ok{color:#0a7d32}button{cursor:pointer;margin-top:18px}
.row{display:flex;gap:8px;align-items:center}.opt{font-weight:400;color:#888}.rm{font-weight:400;font-size:13px;white-space:nowrap}"""


def check(kind: str, value: str) -> str | None:
    """None when the value can be saved, else why not."""
    if len(value) > 2000:
        return "too long"
    if re.search(r"[\x00-\x1f\x7f]", value):
        return "has a line break or a control character"
    if kind.startswith("number"):
        low, high = (float(x) for x in kind.split(":")[1].split("-")) if ":" in kind else (0.0, None)
        try:
            n = float(value)
        except ValueError:
            return "must be a number"
        if n < low or (high is not None and n > high):
            return f"must be from {low:g} to {high:g}" if high is not None else f"must be {low:g} or more"
    elif kind.startswith("choice:"):
        options = kind.split(":", 1)[1].split("|")
        if value.lower() not in options:
            return "must be " + " or ".join(options)
    elif kind == "email":
        if not re.fullmatch(r"[^@\s\"'<>]+@[^@\s\"'<>]+\.[a-z]{2,}", value, re.I):
            return "must be an email address"
    elif kind == "url":
        if not re.fullmatch(r"https://[^\s\"'<>]+", value):
            return "must be an https:// address"
    elif kind == "key" and re.search(r"\s|[\"'=]", value):
        return "must not contain spaces, quotes or ="
    return None


class Page:
    def __init__(self, public_url: str, password: str):
        self.url = public_url.rstrip("/")
        self.origin = "{0.scheme}://{0.netloc}".format(urlparse(self.url))
        self.secure = self.url.startswith("https://")
        self.password = password
        self.sessions: dict[str, dict] = {}   # cookie token -> {"expires", "csrf"}
        self.fails: dict[str, list[float]] = {}  # client address -> times of wrong passwords

    # sign-in ---------------------------------------------------------------
    def _client(self, request: Request) -> str:
        return request.client.host if request.client else "?"

    def locked(self, who: str) -> bool:
        now = time.time()
        self.fails[who] = [t for t in self.fails.get(who, []) if now - t < LOCK_SECONDS]
        return len(self.fails[who]) >= MAX_TRIES

    def session(self, request: Request) -> dict | None:
        token = request.cookies.get(COOKIE, "")
        s = self.sessions.get(token)
        if not s or s["expires"] < time.time():
            self.sessions.pop(token, None)
            return None
        return s

    def same_site(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        return origin is None or origin == self.origin

    def allowed_transport(self, request: Request) -> bool:
        """HTTPS, or plain http only on this computer (local testing)."""
        return self.secure or (request.client and request.client.host in ("127.0.0.1", "::1", "testclient"))

    # pages -----------------------------------------------------------------
    def _page(self, body: str, status: int = 200, cookie: tuple[str, int] | None = None) -> Response:
        r = HTMLResponse(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
                         f'content="width=device-width,initial-scale=1"><title>Puzzle Lead Scoring: settings</title>'
                         f'<style>{STYLE}</style></head><body><h2>Puzzle Lead Scoring: API keys</h2>{body}</body></html>',
                         status, headers=HEADERS)
        if cookie:
            r.set_cookie(COOKIE, cookie[0], max_age=cookie[1], path="/settings", httponly=True, samesite="strict",
                         secure=self.secure)
        return r

    def login_form(self, error: str = "", status: int = 200) -> Response:
        err = f'<p class="err">{html.escape(error)}</p>' if error else ""
        return self._page(f'<p>Sign in with the admin password (MCP_ADMIN_PASSWORD in the server\'s .env).</p>{err}'
                          f'<form method="post" action="/settings/login"><input type="password" name="password" '
                          f'placeholder="Admin password" autofocus required><button type="submit">Sign in</button></form>',
                          status)

    def form(self, s: dict, note: str = "", errors: dict | None = None) -> Response:
        errors = errors or {}
        rows = []
        for var, title, desc, secret, kind, example in settings.EDITABLE:
            now = os.environ.get(var, "").strip()
            state = ('<span class="set">set</span>' if now else '<span class="unset">not set</span>') if secret else \
                (f'<span class="set">{html.escape(now)}</span>' if now else '<span class="unset">not set</span>')
            err = f'<small class="err">{html.escape(errors[var])}</small>' if var in errors else ""
            rows.append(f'<label for="{var}">{html.escape(title)} <span class="opt">(optional)</span>: {state}</label>'
                        f'<small>{html.escape(desc)}</small>{err}'
                        f'<div class="row"><input id="{var}" name="{var}" type="{"password" if secret else "text"}" '
                        f'autocomplete="off" placeholder="{html.escape(("Optional. Paste a new value to replace it" if now else f"Optional, e.g. {example}"))}">'
                        f'<label class="rm"><input type="checkbox" name="remove_{var}"> Remove</label></div>')
        msg = f'<p class="{"err" if errors else "ok"}">{html.escape(note)}</p>' if note else ""
        return self._page(
            f'{msg}<p>Every box is optional: without a key that step is skipped. Keys are never shown here, only whether '
            f'they are set. A box left empty keeps what is there.</p>'
            f'<form method="post" action="/settings" autocomplete="off"><input type="hidden" name="csrf" value="{s["csrf"]}">'
            f'{"".join(rows)}<button type="submit">Save</button></form>'
            f'<form method="post" action="/settings/logout"><input type="hidden" name="csrf" value="{s["csrf"]}">'
            f'<button type="submit">Sign out</button></form>'
            f'<p><small>Saved keys work in this connector at once. Restart the API server (python -m enrich serve) '
            f'to use them there.</small></p>', 400 if errors else 200)

    # handlers --------------------------------------------------------------
    async def get(self, request: Request) -> Response:
        if not self.allowed_transport(request):
            return self._page("<p class='err'>Open this page over HTTPS.</p>", 403)
        s = self.session(request)
        if not s:
            return self.login_form()
        saved = request.query_params.get("saved")
        return self.form(s, f"Saved: {saved}." if saved else "")

    async def login(self, request: Request) -> Response:
        if not self.allowed_transport(request) or not self.same_site(request):
            return self._page("<p class='err'>Not allowed.</p>", 403)
        who = self._client(request)
        if self.locked(who):
            return self.login_form("Too many wrong passwords. Try again in 10 minutes.", 429)
        form = await request.form()
        if not hmac.compare_digest(str(form.get("password", "")).encode(), self.password.encode()):
            self.fails.setdefault(who, []).append(time.time())
            await anyio.sleep(1)  # slow down guessing
            return self.login_form("Wrong password.", 401)
        self.fails.pop(who, None)
        now = time.time()
        self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {"expires": now + SESSION_SECONDS, "csrf": secrets.token_urlsafe(24)}
        r = RedirectResponse("/settings", 303, headers=HEADERS)
        r.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, path="/settings", httponly=True, samesite="strict",
                     secure=self.secure)
        return r

    async def _signed_in(self, request: Request) -> tuple[dict | None, dict]:
        s = self.session(request)
        form = await request.form()
        if not s or not self.same_site(request) or not hmac.compare_digest(str(form.get("csrf", "")), s["csrf"]):
            return None, form
        return s, form

    async def save(self, request: Request) -> Response:
        if not self.allowed_transport(request):
            return self._page("<p class='err'>Open this page over HTTPS.</p>", 403)
        s, form = await self._signed_in(request)
        if not s:
            return self.login_form("Your session ended. Sign in again.", 401)
        changes, errors = {}, {}
        for var, title, _desc, _secret, kind, _example in settings.EDITABLE:
            value = str(form.get(var, "")).strip()
            if kind.startswith("choice:"):
                value = value.lower()
            if form.get(f"remove_{var}"):
                changes[var] = None
            elif value:
                why = check(kind, value)
                if why:
                    errors[var] = f"{title} {why}; nothing was saved."
                else:
                    changes[var] = value
        if errors:
            return self.form(s, "Nothing was saved: fix the marked boxes.", errors)
        for var, value in changes.items():
            settings.unset_env(var) if value is None else settings.set_env(var, value)
        names = ", ".join(t for v, t, *_ in settings.EDITABLE if v in changes) or "nothing changed"
        return RedirectResponse(f"/settings?saved={names}", 303, headers=HEADERS)

    async def logout(self, request: Request) -> Response:
        s, _ = await self._signed_in(request)
        if s:
            self.sessions = {k: v for k, v in self.sessions.items() if v is not s}
        r = RedirectResponse("/settings", 303, headers=HEADERS)
        r.delete_cookie(COOKIE, path="/settings")
        return r


def enable(mcp, public_url: str, password: str) -> Page:
    """Add /settings to the hosted connector (HTTP only)."""
    page = Page(public_url, password)
    mcp.custom_route("/settings", methods=["GET"])(page.get)
    mcp.custom_route("/settings", methods=["POST"])(page.save)
    mcp.custom_route("/settings/login", methods=["POST"])(page.login)
    mcp.custom_route("/settings/logout", methods=["POST"])(page.logout)
    return page
