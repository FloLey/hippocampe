"""Telegram bot: the memory reaches the user without a conversation.

A private bot, linked to one chat (the owner's) with a one-time code from the
console. It long-polls Telegram from a daemon thread, so no public webhook is
needed. What it does:

- every Sunday morning, a digest of the week: overdue and upcoming items, each
  todo with a button that closes it, and the number of open questions;
- ``/question`` sends the next open question; the reply is remembered and the
  question closed;
- any other message is captured like ``remember`` (secrets go to the vault);
  if the note is ambiguous (which Bastien?), the bot asks one short question
  and remembers the answer.

The token sits in the encrypted vault (set from the console), the chat link and
polling offset in a small state file on the vault volume: nothing here reaches
git. Messages from any other chat are ignored. Outgoing text is masked for
secrets, as Telegram is not end-to-end encrypted.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import secrets
import threading
import time

import httpx

from wiki_server import capture, questions, temporal, vault
from wiki_server.sensitive import mask_sensitive

STATE_FILE = "telegram.json"
TOKEN_KEY = "telegram_bot_token"
API = "https://api.telegram.org/bot{token}/{method}"
PAIR_TTL = 15 * 60
DIGEST_WEEKDAY, DIGEST_HOUR = 6, 9  # Sunday, 09:00 in the schedule's timezone
_MAX_ASKS = 50
_MAX_TEXT = 4000

_log = logging.getLogger("wiki_server.telegram")
# httpx logs every request URL at INFO, and the URL carries the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
_lock = threading.Lock()

HELP = (
    "Je suis ta mémoire.\n\n"
    "• Écris-moi n'importe quoi : je le retiens.\n"
    "• /semaine : ta semaine (tâches, échéances, questions).\n"
    "• /question : la prochaine question que la mémoire se pose.\n\n"
    "Chaque dimanche matin, je t'envoie le résumé de la semaine."
)


class TelegramError(Exception):
    pass


# --- token and state ----------------------------------------------------------

def token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or vault.config_get(TOKEN_KEY)


def _state_path():
    return vault.vault_root() / STATE_FILE


def _load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _update_state(**changes) -> dict:
    with _lock:
        state = _load_state()
        state.update(changes)
        _save_state(state)
        return state


def status() -> dict:
    """For the console: whether a token is set, which bot, whether linked."""
    state = _load_state()
    return {"token": bool(token()), "bot": state.get("bot_username", ""),
            "linked": bool(state.get("chat_id")), "last_digest": state.get("last_digest", ""),
            "error": state.get("last_error", "")}


def new_pair_code() -> str:
    code = f"{secrets.randbelow(10**6):06d}"
    _update_state(pair_code=code, pair_expires=time.time() + PAIR_TTL)
    return code


def unlink() -> None:
    _update_state(chat_id=None, pair_code=None, asks={}, buttons={})


# --- Telegram API ---------------------------------------------------------------

def _call(method: str, http_timeout: float = 30, tok: str | None = None, **params):
    tok = tok or token()
    if not tok:
        raise TelegramError("no bot token")
    try:
        r = httpx.post(API.format(token=tok, method=method), json=params, timeout=http_timeout)
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Never let the URL (and the token in it) reach a log line.
        raise TelegramError(f"{method}: {type(exc).__name__}") from None
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', 'error')}")
    return data.get("result")


def check_token(tok: str) -> str:
    """The bot's username if ``tok`` is a valid token (raises otherwise)."""
    return _call("getMe", tok=tok.strip()).get("username", "")


def set_token(tok: str) -> str:
    username = check_token(tok)
    vault.config_set(TOKEN_KEY, tok.strip())
    _update_state(bot_username=username, offset=0, last_error="")
    return username


def send(text: str, buttons: list[list[dict]] | None = None, ask: bool = False,
         chat_id: int | None = None) -> int | None:
    """Send ``text`` (masked, trimmed) to the linked chat. ``ask`` opens a reply
    box on the user's side. Returns the message id."""
    chat_id = chat_id or _load_state().get("chat_id")
    if not chat_id:
        return None
    text = mask_sensitive(text)[0]
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT - 1] + "…"
    params: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    elif ask:
        params["reply_markup"] = {"force_reply": True, "input_field_placeholder": "Ta réponse"}
    return (_call("sendMessage", **params) or {}).get("message_id")


def _remember_ask(message_id: int | None, info: dict) -> None:
    if message_id is None:
        return
    with _lock:
        state = _load_state()
        asks = state.get("asks") or {}
        asks[str(message_id)] = info
        state["asks"] = dict(list(asks.items())[-_MAX_ASKS:])
        _save_state(state)


# --- digest ---------------------------------------------------------------------

def _today() -> datetime.date:
    from wiki_server.query import _today_local

    return _today_local()[0]


def _first_line(body: str) -> str:
    return (body.splitlines()[0] if body else "").strip().lstrip("#").strip()


def _fmt_day(d: datetime.date) -> str:
    days = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
    return f"{days[d.weekday()]} {d.day:02d}/{d.month:02d}"


def digest(today: datetime.date | None = None) -> tuple[str, list[str]]:
    """The week's summary and the paths of the items it offers to close."""
    today = today or _today()
    horizon = today + datetime.timedelta(days=7)
    overdue, upcoming, events = [], [], []
    for item in temporal.list_items(active_only=True):
        show, late = temporal.surface_state(item["meta"], today)
        if not show:
            continue
        due = temporal._as_date(item["meta"].get("due", ""))
        kind = item["meta"].get("type", "todo")
        title = _first_line(item["body"]) or item["path"]
        if late:
            overdue.append((due, title, item["path"]))
        elif due and due <= horizon:
            (events if kind == "event" else upcoming).append((due, title, item["path"]))
    lines, closable = [f"🗓 Ta semaine ({_fmt_day(today)} → {_fmt_day(horizon)})"], []

    def block(header: str, rows, numbered: bool) -> None:
        if not rows:
            return
        lines.append(f"\n{header}")
        for due, title, path in sorted(rows, key=lambda r: (r[0] or today, r[1])):
            when = f" ({_fmt_day(due)})" if due else ""
            if numbered:
                closable.append(path)
                lines.append(f"{len(closable)}. {title}{when}")
            else:
                lines.append(f"• {title}{when}")

    block("⚠️ En retard", overdue, True)
    block("✅ À faire cette semaine", upcoming, True)
    block("📍 Événements", events, False)
    try:
        from wiki_server import people

        bdays = people.upcoming_birthdays(today, 14)
    except Exception:
        bdays = []
    if bdays:
        lines.append("\n🎂 Anniversaires")
        lines.extend(f"• {b}" for b in bdays)
    if not (overdue or upcoming or events or bdays):
        lines.append("\nRien d'urgent cette semaine.")
    n_q = len(questions.list_questions(open_only=True))
    if n_q:
        lines.append(f"\n❓ {n_q} question{'s' if n_q > 1 else ''} ouverte{'s' if n_q > 1 else ''} : /question")
    if closable:
        lines.append("\nTouche un numéro quand c'est fait.")
    return "\n".join(lines), closable


def send_digest() -> bool:
    text, closable = digest()
    keys = {secrets.token_hex(4): path for path in closable}
    rows = [[{"text": f"✅ {i}", "callback_data": f"done:{k}"}
             for i, k in enumerate(keys, 1)][j:j + 5] for j in range(0, len(keys), 5)]
    sent = send(text, buttons=[r for r in rows if r] or None)
    if sent is None:
        return False
    with _lock:
        state = _load_state()
        state["buttons"] = {**(state.get("buttons") or {}), **keys}
        state["buttons"] = dict(list(state["buttons"].items())[-100:])
        state["last_digest"] = _today().isoformat()
        _save_state(state)
    return True


def _digest_due(now_local: datetime.datetime, last: str) -> bool:
    return (now_local.weekday() == DIGEST_WEEKDAY and now_local.hour >= DIGEST_HOUR
            and last != now_local.date().isoformat())


# --- incoming messages ----------------------------------------------------------

def send_next_question(chat_id: int | None = None) -> bool:
    open_q = questions.list_questions(open_only=True)
    if not open_q:
        send("Aucune question ouverte. 🎉", chat_id=chat_id)
        return False
    q = open_q[0]
    text = q["body"] or q["path"]
    more = len(open_q) - 1
    tail = f"\n\n(Réponds à ce message. Encore {more} après celle-ci.)" if more else "\n\n(Réponds à ce message.)"
    mid = send(f"❓ {text}{tail}", ask=True, chat_id=chat_id)
    _remember_ask(mid, {"kind": "question", "path": q["path"], "text": text})
    return True


def _page_label(path: str) -> str:
    return path.rsplit("/", 1)[-1][:-3].replace("-", " ").title()


def _clarify_prompt(text: str) -> str:
    """One short question about what the note leaves open, or ""."""
    shared, relations = capture.ambiguities(text)
    if shared:
        token_, pages = sorted(shared.items())[0]
        return f"Quel {token_.title()} : {' ou '.join(_page_label(p) for p in pages)} ?"
    if relations:
        return f"« {relations[0]} », c'est qui ?"
    return ""


def _capture(text: str) -> tuple[str, str]:
    saved = capture.save(text)
    reply = "Noté ✅"
    if saved["flagged"]:
        reply += (" Secret mis au coffre chiffré." if saved["vaulted"]
                  else " Ça ressemblait à un secret : masqué, pas enregistré.")
    return reply, saved["name"]


def handle_update(update: dict) -> None:
    state = _load_state()
    cb = update.get("callback_query")
    if cb:
        chat = ((cb.get("message") or {}).get("chat") or {}).get("id")
        if not chat or chat != state.get("chat_id"):
            return
        data = cb.get("data") or ""
        path = (state.get("buttons") or {}).get(data.removeprefix("done:"))
        note = "Déjà fermé ou introuvable."
        if data.startswith("done:") and path and temporal.mark_done(path):
            note = "Fermé ✅"
            send(f"✅ Fermé : {_page_label(path)}")
        _call("answerCallbackQuery", callback_query_id=cb.get("id"), text=note)
        return

    msg = update.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if chat and msg.get("photo") and chat == state.get("chat_id"):
        _handle_photo(msg)
        return
    if not chat or not text:
        return

    if not state.get("chat_id"):
        code, expires = state.get("pair_code"), state.get("pair_expires", 0)
        if code and time.time() < expires and code in text:
            _update_state(chat_id=chat, pair_code=None)
            send("C'est relié. 🔗\n\n" + HELP, chat_id=chat)
        return  # strangers get no answer at all
    if chat != state.get("chat_id"):
        return

    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if command in ("/start", "/aide", "/help"):
        send(HELP)
        return
    if command in ("/semaine", "/week"):
        send_digest()
        return
    if command in ("/question", "/questions"):
        send_next_question()
        return

    reply_to = str((msg.get("reply_to_message") or {}).get("message_id", ""))
    ask = (state.get("asks") or {}).get(reply_to)
    if ask and ask.get("kind") == "question":
        capture.save(f"Réponse à la question « {ask['text']} » : {text}")
        questions.mark_answered(ask["path"])
        left = len(questions.list_questions(open_only=True))
        send("Merci, noté ✅" + (f" Encore {left} : /question" if left else ""))
        return
    if ask and ask.get("kind") == "clarify":
        capture.save(f"Précision sur la note {ask['note']} ({ask['text']}) : {text}")
        send("Merci, noté ✅")
        return

    reply, name = _capture(text)
    doubt = _clarify_prompt(text)
    if doubt:
        mid = send(f"{reply}\n\n{doubt}", ask=True)
        _remember_ask(mid, {"kind": "clarify", "note": name, "text": doubt})
    else:
        send(reply)


def _download(file_id: str) -> bytes:
    path = (_call("getFile", file_id=file_id) or {}).get("file_path", "")
    if not path:
        raise TelegramError("getFile: no path")
    try:
        r = httpx.get(f"https://api.telegram.org/file/bot{token()}/{path}", timeout=60)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise TelegramError(f"download: {type(exc).__name__}") from None
    return r.content


def _handle_photo(msg: dict) -> None:
    """A photo with a caption naming a page is attached to that page."""
    from wiki_server import media
    from wiki_server.query import rank_pages

    caption = (msg.get("caption") or "").strip()
    pages = [rel for rel, _ in rank_pages(caption) if rel.startswith("long_term/")
             and "/self/" not in rel and not rel.endswith("index.md")] if caption else []
    if not pages:
        send("Ajoute une légende qui dit de quoi parle la photo (par ex. « Poterie : "
             "bol terminé »), pour que je la range sur la bonne page.")
        return
    biggest = max(msg["photo"], key=lambda p: p.get("file_size") or p.get("width", 0))
    try:
        rel = media.add_photo(pages[0], _download(biggest["file_id"]), caption)
    except media.MediaError as exc:
        send(f"Photo non enregistrée : {exc}")
        return
    send(f"Photo ajoutée à « {_page_label(pages[0])} » 📷")
    _log.info("telegram photo stored at %s", rel)


# --- loop -----------------------------------------------------------------------

def _now_local() -> datetime.datetime:
    from wiki_server.dream.schedule import _now_local, read_schedule

    return _now_local(read_schedule()["tz"])


def _poll_once() -> None:
    state = _load_state()
    updates = _call("getUpdates", http_timeout=70, offset=state.get("offset", 0),
                    timeout=50, allowed_updates=["message", "callback_query"])
    for update in updates or []:
        _update_state(offset=update["update_id"] + 1)
        try:
            handle_update(update)
        except Exception as exc:
            _log.warning("telegram update failed: %s", type(exc).__name__)


def run_bot() -> None:
    """Daemon loop: poll for messages, send the Sunday digest. Idle (checking
    once a minute) until a token is set; never raises out of the loop."""
    backoff = 5
    while True:
        if not token():
            time.sleep(60)
            continue
        try:
            state = _load_state()
            if state.get("chat_id") and _digest_due(_now_local(), state.get("last_digest", "")):
                send_digest()
            _poll_once()
            backoff = 5
            if state.get("last_error"):
                _update_state(last_error="")
        except Exception as exc:
            _log.warning("telegram bot: %s", str(exc)[:200])
            _update_state(last_error=str(exc)[:200])
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
