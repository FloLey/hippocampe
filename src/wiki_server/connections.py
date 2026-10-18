"""OAuth connections to the user's services (Strava, Google), set up from the
console at /ui/reglages/connexions.

The user creates an API app with the provider (the console shows the steps and
the redirect URL to paste), pastes its client id and secret, then presses
"Connecter": the provider asks for consent and redirects back with a code,
exchanged here for tokens. Everything (client secret, refresh token) is kept in
the encrypted vault's connection settings, never in git.

Access tokens are refreshed on demand. Strava rotates its refresh token on each
refresh; the new one is stored every time.
"""

from __future__ import annotations

import time
from urllib.parse import urlencode

import httpx

from wiki_server import vault

PROVIDERS = {
    "strava": {
        "label": "Strava",
        "auth_url": "https://www.strava.com/oauth/authorize",
        "token_url": "https://www.strava.com/oauth/token",
        "scope": "read,activity:read_all",
        "auth_params": {"approval_prompt": "auto"},
    },
    "google": {
        "label": "Google",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        # calendar.app.created: write only to calendars this app created (the
        # "Mémoire" calendar), never to the user's own.
        "scope": ("https://www.googleapis.com/auth/calendar.readonly "
                  "https://www.googleapis.com/auth/calendar.app.created "
                  "https://www.googleapis.com/auth/gmail.readonly"),
        # offline + consent: Google only returns a refresh token this way.
        "auth_params": {"access_type": "offline", "prompt": "consent"},
    },
}


class ConnectionError_(Exception):
    """A provider refused or could not be reached (message is safe to show)."""


def _key(provider: str, field: str) -> str:
    return f"{provider}_{field}"


def get(provider: str, field: str) -> str:
    return vault.config_get(_key(provider, field))


def set_fields(provider: str, **fields: str) -> None:
    for field, value in fields.items():
        vault.config_set(_key(provider, field), value or "")


def status(provider: str) -> dict:
    return {"configured": bool(get(provider, "client_id") and get(provider, "client_secret")),
            "connected": bool(get(provider, "refresh_token")),
            "account": get(provider, "account"), "error": get(provider, "error"),
            "last_sync": get(provider, "last_sync")}


def disconnect(provider: str) -> None:
    set_fields(provider, refresh_token="", access_token="", expires_at="", account="", error="")


def forget(provider: str) -> None:
    disconnect(provider)
    set_fields(provider, client_id="", client_secret="")


def redirect_uri(base_url: str, provider: str) -> str:
    return f"{base_url.rstrip('/')}/ui/connections/{provider}/callback"


def authorize_url(provider: str, base_url: str, state: str) -> str:
    p = PROVIDERS[provider]
    params = {"client_id": get(provider, "client_id"), "redirect_uri": redirect_uri(base_url, provider),
              "response_type": "code", "scope": p["scope"], "state": state, **p["auth_params"]}
    return f"{p['auth_url']}?{urlencode(params)}"


def _token_request(provider: str, data: dict) -> dict:
    p = PROVIDERS[provider]
    data = {"client_id": get(provider, "client_id"),
            "client_secret": get(provider, "client_secret"), **data}
    try:
        r = httpx.post(p["token_url"], data=data, timeout=30)
        body = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectionError_(f"{p['label']} injoignable ({type(exc).__name__})") from None
    if r.status_code != 200 or "access_token" not in body:
        msg = body.get("error_description") or body.get("message") or body.get("error") or r.status_code
        raise ConnectionError_(f"{p['label']} a refusé : {msg}")
    return body


def _store_tokens(provider: str, body: dict) -> None:
    expires = body.get("expires_at") or (time.time() + float(body.get("expires_in", 3600)))
    fields = {"access_token": body["access_token"], "expires_at": str(int(expires)), "error": ""}
    if body.get("refresh_token"):
        fields["refresh_token"] = body["refresh_token"]
    set_fields(provider, **fields)


def exchange_code(provider: str, code: str, base_url: str) -> None:
    body = _token_request(provider, {"code": code, "grant_type": "authorization_code",
                                     "redirect_uri": redirect_uri(base_url, provider)})
    _store_tokens(provider, body)
    athlete = body.get("athlete") or {}
    if athlete:
        set_fields(provider, account=f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip())


def access_token(provider: str) -> str:
    """A valid access token, refreshed if it expires within a minute."""
    refresh = get(provider, "refresh_token")
    if not refresh:
        raise ConnectionError_(f"{PROVIDERS[provider]['label']} n'est pas connecté.")
    token, expires = get(provider, "access_token"), get(provider, "expires_at")
    if token and expires and float(expires) > time.time() + 60:
        return token
    try:
        body = _token_request(provider, {"grant_type": "refresh_token", "refresh_token": refresh})
    except ConnectionError_ as exc:
        set_fields(provider, error=str(exc)[:200])
        raise
    _store_tokens(provider, body)
    return body["access_token"]


def api(method: str, provider: str, url: str, params: dict | None = None,
        json: dict | None = None) -> dict | list:
    label = PROVIDERS[provider]["label"]
    try:
        r = httpx.request(method, url, params=params, json=json, timeout=30,
                          headers={"Authorization": f"Bearer {access_token(provider)}"})
        body = r.json() if r.content else {}
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectionError_(f"{label} injoignable ({type(exc).__name__})") from None
    if r.status_code == 404 and method == "DELETE":
        return {}  # already gone
    if r.status_code >= 300:
        msg = body.get("message") if isinstance(body, dict) else ""
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            msg = body["error"].get("message", msg)
        raise ConnectionError_(f"{label} : {msg or r.status_code}")
    return body


def api_get(provider: str, url: str, params: dict | None = None) -> dict | list:
    return api("GET", provider, url, params=params)
