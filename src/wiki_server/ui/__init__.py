"""Web console for the Personal Memory Wiki, served at /ui by the same process.

A private, browser-facing console to consult and hand-edit the memory. It is
gated by GitHub login (the same OAuth app as the MCP endpoint) restricted to the
owner, with a signed session cookie. In local development (WIKI_AUTH_DISABLED=1)
the login is bypassed.

Four spaces, one module each:

- ``home``     : Accueil, what needs the user now (todos, questions, birthdays,
                 last night's dream), quick capture and search.
- ``memory``   : Mémoire, the pages by category, people cards, the graph, a
                 page and its editor, the raw file browser.
- ``agenda``   : Temps, what is coming, the month-by-month thread, the year.
- ``settings`` : Réglages, the dream, connections, prompts and models, logs.

Rendering is server-side HTML. Markdown is rendered with HTML disabled, so
content stored in the wiki can never inject markup into the console. Every
action is a POST with a CSRF token, then a redirect back with a signed
one-line confirmation.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from wiki_server.ui.layout import _sign, _verify, page

SESSION_COOKIE = "wiki_ui_session"
STATE_COOKIE = "wiki_ui_state"
SESSION_TTL = 7 * 24 * 3600


class Ctx:
    """What every page needs: who is logged in, CSRF tokens, the confirmation
    banner, and the page renderer."""

    def __init__(self, secret_key: str, auth_disabled: bool, base: str):
        self.secret_key = secret_key
        self.auth_disabled = auth_disabled
        self.base = base

    def login(self, request: Request) -> str | None:
        if self.auth_disabled:
            return "dev"
        payload = _verify(self.secret_key, request.cookies.get(SESSION_COOKIE))
        return payload.get("login") if payload else None

    def csrf(self, login: str) -> str:
        return _sign(self.secret_key, {"k": "csrf", "login": login, "exp": time.time() + SESSION_TTL})

    def csrf_ok(self, login: str, token: str | None) -> bool:
        payload = _verify(self.secret_key, token)
        return bool(payload and payload.get("k") == "csrf" and payload.get("login") == login)

    async def post(self, request: Request):
        """Gate a POST handler: (login, form, None) when authenticated with a
        valid CSRF token, else (None, None, error response)."""
        login = self.login(request)
        if not login:
            return None, None, PlainTextResponse("Non connecté.", status_code=401)
        form = await request.form()
        token = form.get("csrf")
        if not isinstance(token, str) or not self.csrf_ok(login, token):
            return None, None, PlainTextResponse("Jeton de sécurité invalide : recharge la page.", status_code=403)
        return login, form, None

    def done(self, url: str, message: str = "") -> RedirectResponse:
        """Back to ``url`` with a one-line confirmation shown once on top."""
        if message:
            flash = _sign(self.secret_key, {"k": "flash", "m": message[:400], "exp": time.time() + 120})
            url += ("&" if "?" in url else "?") + urlencode({"ok": flash})
        return RedirectResponse(url, status_code=303)

    def flash(self, request: Request) -> str:
        payload = _verify(self.secret_key, request.query_params.get("ok"))
        return str(payload.get("m", "")) if payload and payload.get("k") == "flash" else ""

    def get(self, mcp, path: str):
        """Register a GET page that needs the login: ``fn(request, login)``."""
        def deco(fn):
            async def handler(request: Request) -> Response:
                login = self.login(request)
                if not login:
                    return RedirectResponse("/ui/login")
                return await fn(request, login)
            handler.__name__ = fn.__name__
            mcp.custom_route(path, methods=["GET"])(handler)
            return fn
        return deco

    def action(self, mcp, path: str):
        """Register a POST action (login + CSRF checked): ``fn(request, login, form)``."""
        def deco(fn):
            async def handler(request: Request) -> Response:
                login, form, err = await self.post(request)
                if err:
                    return err
                return await fn(request, login, form)
            handler.__name__ = fn.__name__
            mcp.custom_route(path, methods=["POST"])(handler)
            return fn
        return deco

    def render(self, request: Request, login: str, title: str, body: str, *,
               section: str = "", wide: bool = False, flash: str = "") -> Response:
        return page(title, body, section=section, login=login, wide=wide,
                    flash=flash or self.flash(request))


def register_ui(
    mcp,
    *,
    owner_login: str,
    client_id: str,
    client_secret: str,
    public_url: str,
    secret_key: str,
    auth_disabled: bool,
) -> None:
    """Register all /ui routes on the FastMCP app."""
    if not auth_disabled and (not secret_key or secret_key == "dev-insecure-key"):
        raise RuntimeError(
            "WIKI_JWT_SIGNING_KEY must be set to a secure, private value in "
            "production; it signs the web console session cookies."
        )
    owner = owner_login.strip().lower()
    base = public_url.rstrip("/")
    callback_url = f"{base}/ui/auth/callback"
    ctx = Ctx(secret_key, auth_disabled, base)

    logo = (Path(__file__).parent / "logo.png").read_bytes()

    @mcp.custom_route("/ui/logo.png", methods=["GET"])
    async def ui_logo(request: Request) -> Response:
        return Response(logo, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @mcp.custom_route("/ui/login", methods=["GET"])
    async def ui_login(request: Request) -> Response:
        if auth_disabled or ctx.login(request):
            return RedirectResponse("/ui")
        state = _sign(secret_key, {"k": "state", "n": secrets.token_urlsafe(8), "exp": time.time() + 600})
        params = urlencode({
            "client_id": client_id,
            "redirect_uri": callback_url,
            "scope": "read:user",
            "state": state,
        })
        resp = RedirectResponse(f"https://github.com/login/oauth/authorize?{params}")
        resp.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, secure=True, samesite="lax", path="/ui")
        return resp

    @mcp.custom_route("/ui/auth/callback", methods=["GET"])
    async def ui_callback(request: Request) -> Response:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        state_payload = _verify(secret_key, state) if state else None
        if (
            not code
            or not state
            or state != request.cookies.get(STATE_COOKIE)
            or not state_payload
            or state_payload.get("k") != "state"
        ):
            return PlainTextResponse("Invalid OAuth state.", status_code=400)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                tok = await client.post(
                    "https://github.com/login/oauth/access_token",
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code": code,
                        "redirect_uri": callback_url,
                    },
                )
                tok.raise_for_status()
                access = (tok.json() or {}).get("access_token")
                if not access:
                    return PlainTextResponse("OAuth exchange failed.", status_code=400)
                user = await client.get(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"},
                )
                user.raise_for_status()
                github_login = (user.json() or {}).get("login", "")
        except (httpx.HTTPError, ValueError) as exc:
            return PlainTextResponse(f"GitHub authentication failed: {exc}", status_code=502)
        if github_login.strip().lower() != owner:
            return PlainTextResponse("Access denied: this console is private.", status_code=403)
        session = _sign(secret_key, {"login": github_login, "exp": time.time() + SESSION_TTL})
        resp = RedirectResponse("/ui", status_code=303)
        resp.set_cookie(SESSION_COOKIE, session, max_age=SESSION_TTL, httponly=True, secure=True, samesite="lax", path="/ui")
        resp.delete_cookie(STATE_COOKIE, path="/ui", secure=True, httponly=True, samesite="lax")
        return resp

    @mcp.custom_route("/ui/logout", methods=["GET"])
    async def ui_logout(request: Request) -> Response:
        # Land on a standalone page rather than /ui/login: redirecting into the
        # login flow would silently re-authenticate via the still-active GitHub
        # session, making logout look like a no-op.
        resp = page(
            "Déconnecté",
            "<div class='login-wrap'><div class='card'>"
            "<p>Tu es déconnecté.</p>"
            "<p><a class='btn primary' href='/ui/login'>Se reconnecter</a></p>"
            "</div></div>",
        )
        resp.delete_cookie(SESSION_COOKIE, path="/ui", secure=True, httponly=True, samesite="lax")
        return resp

    from wiki_server.ui import agenda, home, memory, settings

    home.register(mcp, ctx)
    memory.register(mcp, ctx)
    agenda.register(mcp, ctx)
    settings.register(mcp, ctx)
