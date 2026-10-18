"""Personal Memory Wiki MCP server.

Tools for Claude: ``prime`` (load the grounding context), ``read`` (read any
file), ``search`` (full-text), ``remember`` (capture to short-term memory),
``complete`` (close a temporal item) and ``secret`` (read the encrypted vault).
A public ``/health`` route serves the container healthcheck, and the web console
is mounted from ``ui``.

Authentication (slice 2): when configured, the server is protected by GitHub
OAuth via FastMCP's ``GitHubProvider`` (an OAuth proxy that lets Claude.ai run
the standard OAuth 2.1 + PKCE discovery flow against an upstream GitHub OAuth
app). On top of "any valid GitHub login", an allow-list middleware restricts
access to a single GitHub account, so the wiki stays private to its owner.

Auth is controlled by environment:
- ``WIKI_AUTH_DISABLED=1`` runs the server open (local development only).
- Otherwise ``GH_OAUTH_CLIENT_ID`` and ``GH_OAUTH_CLIENT_SECRET`` are required,
  and the server fails loudly if they are missing (so production can never come
  up silently unauthenticated).

The ``/health`` route stays public regardless, for the container healthcheck.
"""

from __future__ import annotations

import logging
import os

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from wiki_server import capture, journal, query, questions, temporal
from wiki_server.paths import WikiPathError, resolve_under_root
from wiki_server import vault
from wiki_server.ui import register_ui

DEFAULT_PUBLIC_URL = "http://localhost:8765"
DEFAULT_ALLOWED_LOGIN = ""


def _build_auth():
    """Build the auth provider from the environment, or ``None`` when auth is
    explicitly disabled for local development."""
    if os.environ.get("WIKI_AUTH_DISABLED") == "1":
        return None

    # Imported lazily so the open/dev path has no hard dependency on the
    # provider stack.
    from fastmcp.server.auth.providers.github import GitHubProvider

    client_id = os.environ.get("GH_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GH_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Auth is enabled but GH_OAUTH_CLIENT_ID / GH_OAUTH_CLIENT_SECRET are "
            "not set. Set them, or set WIKI_AUTH_DISABLED=1 for local development."
        )

    kwargs = {
        "client_id": client_id,
        "client_secret": client_secret,
        "base_url": os.environ.get("WIKI_PUBLIC_URL", DEFAULT_PUBLIC_URL),
        "required_scopes": ["user"],
    }
    # A stable signing key keeps issued tokens valid across restarts/redeploys,
    # so the user is not forced to re-authorize on every deploy.
    signing_key = os.environ.get("WIKI_JWT_SIGNING_KEY")
    if signing_key:
        kwargs["jwt_signing_key"] = signing_key

    return GitHubProvider(**kwargs)


class AllowedUserMiddleware(Middleware):
    """Restrict every tool call to a single GitHub login.

    GitHub OAuth on its own lets *any* GitHub account through. This narrows it
    to the wiki owner by checking the ``login`` claim that the GitHub token
    verifier attaches to the access token.
    """

    def __init__(self, allowed_login: str):
        normalized = (allowed_login or "").strip().lower()
        if not normalized:
            # Fail closed: an empty allow-list, combined with an empty/missing
            # login claim, could otherwise let an unintended caller through.
            raise ValueError(
                "WIKI_ALLOWED_GITHUB_LOGIN must be a non-empty GitHub login."
            )
        self.allowed_login = normalized

    async def on_call_tool(self, context, call_next):
        token = get_access_token()
        if token is None:
            raise ToolError("Access denied: unauthenticated request.")
        claims = getattr(token, "claims", {}) or {}
        login = claims.get("login")
        # Deny on a missing/empty login too, never just on inequality.
        if not isinstance(login, str) or login.strip().lower() != self.allowed_login:
            raise ToolError("Access denied: this wiki is private to its owner.")
        return await call_next(context)


_auth = _build_auth()
mcp = FastMCP(name="personal-memory-wiki", auth=_auth)

if _auth is not None:
    _allowed_login = os.environ.get("WIKI_ALLOWED_GITHUB_LOGIN", DEFAULT_ALLOWED_LOGIN)
    mcp.add_middleware(AllowedUserMiddleware(_allowed_login))

# Browser console at /ui, gated by GitHub login restricted to the owner (or open
# in local dev when WIKI_AUTH_DISABLED=1). Reuses the same GitHub OAuth app.
register_ui(
    mcp,
    owner_login=os.environ.get("WIKI_ALLOWED_GITHUB_LOGIN", DEFAULT_ALLOWED_LOGIN),
    client_id=os.environ.get("GH_OAUTH_CLIENT_ID", ""),
    client_secret=os.environ.get("GH_OAUTH_CLIENT_SECRET", ""),
    public_url=os.environ.get("WIKI_PUBLIC_URL", DEFAULT_PUBLIC_URL),
    secret_key=os.environ.get("WIKI_JWT_SIGNING_KEY", "dev-insecure-key"),
    auth_disabled=os.environ.get("WIKI_AUTH_DISABLED") == "1",
)


_READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}


@mcp.tool(annotations=_READ_ONLY)
def prime() -> str:
    """Call this FIRST, at the very start of every conversation, before anything
    else. It returns who the user is (the self pages, long reference sections
    reduced to their names), one short line per long-term page, the recent
    notes, the active dated items and the open questions. It is the short
    version: use recall(topic) to load the detail on whatever comes up.
    """
    journal.log("prime", "")
    return query.build_prime()


@mcp.tool(annotations=_READ_ONLY)
def recall(topic: str) -> str:
    """Everything the memory holds on one topic, in one call: the best matching
    pages in full (with the pages linking to them), the active dated items, the
    recent notes not yet consolidated, the open questions on it, and other pages
    that mention it. Call it before answering about a person, project, place or
    subject, e.g. ``recall("Nina")``, ``recall("voyage Islande")``,
    ``recall("course à pied")``. Prefer it to chains of search and read.
    """
    from wiki_server.recall import recall as _recall

    if not topic.strip():
        raise ToolError("Empty topic.")
    journal.log("recall", topic.strip())
    return _recall(topic)


@mcp.tool(annotations=_READ_ONLY)
def read(path: str) -> str:
    """Read any file in the memory by its path: a page, an index, or a short-term
    entry. Examples: ``long_term/index.md``, ``long_term/self/identity.md``,
    ``short_term/index.md``, ``short_term/entries/2026-06-15-rdv-dentiste.md``.

    Tolerant of paths: a bare path like ``self/identity.md`` (as written in index
    links) is accepted. If nothing is found it returns suggestions, never a bare
    "does not exist", so never conclude a file is missing from a single attempt.
    """
    path = path.strip().strip("'\"")
    if not path:
        return "Error: empty path."
    # Try the path as given, then with the long_term/ prefix (index links are
    # written relative to long_term/, not the wiki root).
    candidates = [path]
    if not path.startswith(("long_term/", "short_term/")):
        candidates.append(f"long_term/{path}")
    for candidate in candidates:
        try:
            target = resolve_under_root(candidate)
        except WikiPathError:
            continue
        if target.is_file():
            journal.log("read", candidate.rsplit("/", 1)[-1].removesuffix(".md"))
            try:
                return target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return f"Error reading {candidate}: {exc}"
    # Not found: suggest by filename, else list what exists. Never claim absence.
    pages = query.list_pages()
    matches = query.find_pages_by_name(path, pages)
    if matches:
        return f"No file at {path!r}. Did you mean: {', '.join(matches)} ?"
    listing = ", ".join(pages[:20]) if pages else "(none)"
    return f"No file at {path!r}. Existing pages: {listing}"


@mcp.tool(annotations=_READ_ONLY)
def search(query_text: str, max_results: int = 30) -> str:
    """Keyword search across the whole memory. Returns matching lines as
    ``path:line: text`` (line ``0`` is a page's tags), best pages first and at
    most a few lines per page. Use it to find where something is mentioned, then
    open the file with read.

    Each whitespace-separated keyword is matched independently, so word order
    and phrasing do not matter; accents and case are ignored, frontmatter tags
    are searched alongside page text, and typos still hit thanks to fuzzy
    matching. Prefer a few distinctive words (a name, a place, a project) over a
    full sentence.
    """
    journal.log("search", query_text.strip())
    return query.search_wiki(query_text, max_results=max_results)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                       "openWorldHint": False})
def remember(
    content: str,
    summary: str = "",
    tags: list[str] | None = None,
    due: str | None = None,
    type: str | None = None,
) -> str:
    """Capture something into short-term memory. The nightly daemon sorts it
    later (into a long-term page, or into temporal/ if it is dated or actionable).

    Use it whenever the user mentions something worth keeping: a fact, a decision,
    a preference, a project, but also a task, a reminder, or an event. For dated or
    actionable items, set ``due`` (a date like 2026-06-15) and ``type`` (one of:
    todo, reminder, event). ``summary`` is an optional one-line label and ``tags``
    an optional list of short tags.

    Write self-contained notes: name people and things as the memory knows them
    (the person's name rather than "my partner"), give absolute dates
    (2026-10-15 rather than "yesterday"), and say whether something is decided,
    planned or only considered. Write only what the user said or clearly
    showed: no guessed preference ("likes mild food" from one dish), and when
    the user shares something (a link, a post, a project), say who made it; it
    is not theirs unless they say so. If one of these is unclear and the conversation
    does not settle it (which Bastien? done or only planned?), ask the user first,
    one short question, then remember the clear version: the user is here now,
    the nightly consolidation is not. The reply lists the existing pages the
    note seems to be about and flags what is still ambiguous; when it does, ask
    and remember the answer.
    To close an existing temporal item, use complete instead. Passwords, keys,
    codes and document numbers are moved automatically to the encrypted vault
    (see secret) and never written to the memory itself.
    """
    if not content.strip():
        raise ToolError("Nothing to remember: content is empty.")
    # Secrets never touch the wiki: each one goes to the encrypted vault and the
    # note keeps a [SECRET: name] reference (masked outright when no vault is
    # configured), before anything is written or committed.
    saved = capture.save(content, summary=summary, tags=tags, due=due, kind=type)
    journal.log("remember", summary.strip() or saved["name"].split("-", 3)[-1].replace("-", " "))
    reply = f"Remembered as short_term/entries/{saved['name']}.md ({saved['created']})."
    note = capture.feedback(f"{summary}\n{content}")
    if note:
        reply += " " + note
    flagged = saved["flagged"]
    if flagged and saved["vaulted"]:
        reply += (f" Secret(s) moved to the encrypted vault ({', '.join(flagged)}): "
                  f"{', '.join(saved['refs'])}. "
                  "The note only keeps a reference; secret(name) returns the value.")
    elif flagged:
        reply += (f" Masked before saving, as it looked like a secret: {', '.join(flagged)}. "
                  "No vault is configured, so tell the user it was not stored.")
    return reply


@mcp.tool(annotations=_READ_ONLY)
def secret(name: str) -> str:
    """Return a secret from the encrypted vault, by the name listed in prime or
    found in a ``[SECRET: name]`` reference. Call it only when the user asks for
    that value, and do not repeat the value beyond what they asked for. Secrets
    are stored automatically: remember moves anything that looks like one into
    the vault.
    """
    if not vault.enabled():
        return "No vault is configured on this server."
    entry = vault.get(name.strip().strip("[]").removeprefix("SECRET:").strip())
    if entry is None:
        names = ", ".join(e["name"] for e in vault.catalog()) or "(none)"
        return f"No secret named {name!r}. Known: {names}"
    return f"{entry['value']}  ({entry.get('kind', '')}, stored {entry.get('created', '?')})"


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False,
                       "idempotentHint": True, "openWorldHint": False})
def complete(path: str) -> str:
    """Close a temporal item (todo, reminder or event) once it is done, cancelled
    or no longer relevant, or an open question once the user has answered it
    (save the answer with remember first), by the path shown in prime, e.g.
    ``temporal/2026-12-31-renouveler-le-passeport.md`` or
    ``questions/2026-10-18-nina-et-copine.md``. It leaves the active list but
    stays in the history. Use remember instead for a new or corrected item.
    """
    if questions.resolve_question(path):
        questions.mark_answered(path)
        journal.log("complete", "question")
        return f"Closed {questions.resolve_question(path)}."
    rel = temporal.resolve_item(path)
    if rel is None:
        active = ", ".join(i["path"] for i in temporal.list_items(active_only=True)) or "(none)"
        return f"No temporal item at {path!r}. Active items: {active}"
    temporal.mark_done(rel)
    journal.log("complete", rel.rsplit("/", 1)[-1].removesuffix(".md").split("-", 3)[-1].replace("-", " "))
    return f"Closed {rel}."


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Liveness probe used by the Docker healthcheck and deploy ``--wait``.
    Stays public (unauthenticated) on purpose."""
    return JSONResponse({"ok": True})


def main() -> None:
    import logging
    import threading

    from wiki_server.dream import run_scheduler
    from wiki_server.logbuffer import setup_logging

    setup_logging()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8765"))
    # The nightly dream scheduler runs in a daemon thread alongside the server;
    # it does nothing until a nightly mode is enabled from the console.
    threading.Thread(target=run_scheduler, daemon=True, name="dream-scheduler").start()
    # The Telegram bot idles until a token is set from /ui/reglages/connexions.
    from wiki_server.telegram import run_bot

    threading.Thread(target=run_bot, daemon=True, name="telegram-bot").start()
    logging.getLogger("wiki_server").info("server up; nightly scheduler thread started")
    # Streamable HTTP transport at /mcp, the URL Claude.ai connects to.
    mcp.run(transport="http", host=host, port=port, path="/mcp")


if __name__ == "__main__":
    main()
