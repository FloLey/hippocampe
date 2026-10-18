"""Daily intake from the connected services, without any model call.

- Strava: every activity goes into a yearly log page
  (``long_term/sources/strava-YYYY.md``, one row each), and each finished week
  becomes one short-term note ("3 sorties, 24 km…") that the dream folds into
  the running pages.
- Google Calendar: the next 30 days of the primary calendar become temporal
  events, kept in step with the calendar (moved, renamed, cancelled).
- Gmail: an email given the label "Mémoire" becomes a short-term note (sender,
  subject, preview). The user decides what is remembered, by labelling it.
- The other way: birthdays and dated todos are written, each with a short
  note, to a "Mémoire" calendar of their own (switches in the console).

Runs once a day from the scheduler (before the dream) and on demand from the
console. What was already taken in is tracked in a state file on the vault
volume, so a re-run never duplicates anything.
"""

from __future__ import annotations

import datetime
import json
import logging
import os

from wiki_server import capture, connections, temporal
from wiki_server.paths import resolve_under_root
from wiki_server.store import apply_changes

SYNC_HOUR = 2  # local time, an hour before the default dream
GMAIL_LABEL = "Mémoire"
CALENDAR_DAYS = 30
_log = logging.getLogger("wiki_server.sync")
STATE_FILE = "sync_state.json"


def _state_path():
    from wiki_server.vault import vault_root

    return vault_root() / STATE_FILE


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


# --- Strava -----------------------------------------------------------------------

_SPORT_FR = {"Run": "course", "TrailRun": "trail", "Ride": "vélo", "VirtualRide": "vélo (home trainer)",
             "Walk": "marche", "Hike": "randonnée", "Swim": "natation", "WeightTraining": "musculation",
             "Workout": "entraînement", "Yoga": "yoga", "Surf": "surf"}
_LOG_HEADER = ("| Date | Sport | Nom | Distance | Durée | D+ | FC moy. | id |\n"
               "|---|---|---|---|---|---|---|---|\n")


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds or 0)
    h, m = divmod(seconds // 60, 60)
    return f"{h}h{m:02d}" if h else f"{m} min"


def _strava_row(a: dict) -> str:
    km = (a.get("distance") or 0) / 1000
    hr = a.get("average_heartrate")
    name = str(a.get("name", "")).replace("|", "/")[:60]
    return (f"| {a.get('start_date_local', '')[:10]} | {_SPORT_FR.get(a.get('sport_type') or a.get('type'), a.get('sport_type', ''))} "
            f"| {name} | {km:.1f} km | {_fmt_duration(a.get('moving_time'))} "
            f"| {int(a.get('total_elevation_gain') or 0)} m | {int(hr) if hr else '-'} | {a.get('id')} |")


def _week_note(week_start: datetime.date, acts: list[dict]) -> str:
    by_sport: dict[str, list[dict]] = {}
    for a in acts:
        by_sport.setdefault(_SPORT_FR.get(a.get("sport_type") or a.get("type"), "autre"), []).append(a)
    parts = []
    for sport, items in sorted(by_sport.items(), key=lambda kv: -len(kv[1])):
        km = sum((a.get("distance") or 0) for a in items) / 1000
        secs = sum((a.get("moving_time") or 0) for a in items)
        parts.append(f"{len(items)} × {sport}" + (f", {km:.1f} km" if km >= 0.5 else "")
                     + f", {_fmt_duration(secs)}")
    longest = max(acts, key=lambda a: a.get("distance") or 0)
    end = week_start + datetime.timedelta(days=6)
    return (f"Sport (Strava), semaine du {week_start.isoformat()} au {end.isoformat()} : "
            + " ; ".join(parts) + f". Plus longue sortie : {longest.get('name', '')} "
            f"({(longest.get('distance') or 0) / 1000:.1f} km, {longest.get('start_date_local', '')[:10]}).")


def sync_strava(today: datetime.date) -> str:
    state = _load()
    st = state.setdefault("strava", {})
    since = st.get("since") or int((datetime.datetime.combine(today, datetime.time()) -
                                    datetime.timedelta(days=30)).timestamp())
    acts: list[dict] = []
    page = 1
    while page <= 10:
        batch = connections.api_get("strava", "https://www.strava.com/api/v3/athlete/activities",
                                    {"after": since, "per_page": 100, "page": page})
        if not batch:
            break
        acts.extend(batch)
        page += 1
    seen = set(st.get("ids", []))
    new = [a for a in acts if a.get("id") not in seen]
    writes: dict[str, str] = {}
    for year in sorted({a.get("start_date_local", "")[:4] for a in new if a.get("start_date_local")}):
        rel = f"long_term/sources/strava-{year}.md"
        path = resolve_under_root(rel)
        text = path.read_text(encoding="utf-8") if path.is_file() else (
            f"# Strava {year}\n\nJournal des activités sportives, importé chaque nuit "
            "depuis Strava (une ligne par activité).\n\n" + _LOG_HEADER)
        rows = [_strava_row(a) for a in sorted(new, key=lambda a: a.get("start_date_local", ""))
                if a.get("start_date_local", "").startswith(year)]
        writes[rel] = text.rstrip("\n") + "\n" + "\n".join(rows) + "\n"
    if writes:
        apply_changes(writes, [], f"sync: strava, {len(new)} activité(s)")
    # One note per finished week (Monday to Sunday), once.
    done_weeks = set(st.get("weeks", []))
    this_monday = today - datetime.timedelta(days=today.weekday())
    weeks: dict[str, list[dict]] = {}
    for a in acts:
        try:
            d = datetime.date.fromisoformat(a.get("start_date_local", "")[:10])
        except ValueError:
            continue
        monday = d - datetime.timedelta(days=d.weekday())
        if monday < this_monday:
            weeks.setdefault(monday.isoformat(), []).append(a)
    notes = 0
    for monday, items in sorted(weeks.items()):
        if monday in done_weeks:
            continue
        capture.save(_week_note(datetime.date.fromisoformat(monday), items),
                     summary=f"Sport, semaine du {monday}", tags=["strava", "sport"])
        done_weeks.add(monday)
        notes += 1
    if acts:
        latest = max(datetime.datetime.fromisoformat(a["start_date"].replace("Z", "+00:00")).timestamp()
                     for a in acts if a.get("start_date"))
        st["since"] = int(latest) - 7 * 86400  # re-read a week back: late uploads, edits
    st["ids"] = sorted(seen | {a.get("id") for a in acts if a.get("id")})[-2000:]
    st["weeks"] = sorted(done_weeks)[-60:]
    _save(state)
    return f"{len(new)} activité(s), {notes} résumé(s) de semaine"


# --- Google Calendar --------------------------------------------------------------

def _event_fields(ev: dict) -> tuple[str, str] | None:
    """(due date, text) for an event, or None for one to skip."""
    if ev.get("status") == "cancelled":
        return None
    for att in ev.get("attendees") or []:
        if att.get("self") and att.get("responseStatus") == "declined":
            return None
    start = ev.get("start") or {}
    when = start.get("dateTime") or start.get("date") or ""
    if not when:
        return None
    text = ev.get("summary") or "(sans titre)"
    if start.get("dateTime"):
        text += f", à {when[11:16]}"
    if ev.get("location"):
        text += f", {ev['location']}"
    return when[:10], text + " (agenda Google)"


def sync_calendar(today: datetime.date) -> str:
    state = _load()
    st = state.setdefault("calendar", {})
    tracked: dict[str, dict] = st.get("events", {})
    start = datetime.datetime.combine(today, datetime.time()).astimezone()
    body = connections.api_get(
        "google", "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        {"timeMin": start.isoformat(), "timeMax": (start + datetime.timedelta(days=CALENDAR_DAYS)).isoformat(),
         "singleEvents": "true", "orderBy": "startTime", "maxResults": 250, "showDeleted": "true"})
    writes: dict[str, str] = {}
    added = changed = closed = 0
    taken: set[str] = set()
    for ev in body.get("items", []):
        eid = ev.get("id")
        if not eid:
            continue
        fields = _event_fields(ev)
        known = tracked.get(eid)
        if fields is None:
            if known and resolve_under_root(known["path"]).is_file():
                if temporal.mark_done(known["path"]):
                    closed += 1
                tracked.pop(eid, None)
            continue
        due, text = fields
        if known and known.get("due") == due and known.get("text") == text:
            continue
        if known and resolve_under_root(known["path"]).is_file():
            rel = known["path"]
            changed += 1
            _, content = temporal.build_item(rel.rsplit("/", 1)[-1][:-3], "event", due, text)
        else:
            stem = temporal.item_stem(text, due, None, taken)
            taken.add(stem)
            rel, content = temporal.build_item(stem, "event", due, text)
            added += 1
        writes[rel] = content
        tracked[eid] = {"path": rel, "due": due, "text": text}
    if writes:
        apply_changes(writes, [], f"sync: agenda, {added} ajout(s), {changed} modif(s)")
    # Forget events now in the past, so the state stays small.
    st["events"] = {k: v for k, v in tracked.items() if v.get("due", "") >= today.isoformat()}
    _save(state)
    return f"agenda : {added} ajouté(s), {changed} modifié(s), {closed} annulé(s)"


# --- Gmail ------------------------------------------------------------------------

def sync_gmail(today: datetime.date) -> str:
    state = _load()
    st = state.setdefault("gmail", {})
    seen = set(st.get("ids", []))
    body = connections.api_get("google", "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                               {"q": f'label:"{GMAIL_LABEL}" newer_than:30d', "maxResults": 50})
    new = 0
    for m in body.get("messages", []) or []:
        if m.get("id") in seen:
            continue
        msg = connections.api_get(
            "google", f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}",
            {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        headers = {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers", [])}
        sender = headers.get("from", "?").split("<")[0].strip().strip('"') or headers.get("from", "?")
        subject = headers.get("subject", "(sans objet)")
        capture.save(f"Email de {sender} ({headers.get('date', '')[:16]}), objet « {subject} » : "
                     f"{msg.get('snippet', '')}", summary=f"Email : {subject}"[:90],
                     tags=["gmail"])
        seen.add(m["id"])
        new += 1
    st["ids"] = sorted(seen)[-1000:]
    _save(state)
    return f"gmail : {new} email(s) étiqueté(s) « {GMAIL_LABEL} »"


# --- the "Mémoire" calendar (written by the memory) --------------------------------
# Birthdays (yearly) and dated todos and reminders, each with a short note,
# in a calendar of its own: the app may write only there (calendar.app.created),
# so the user's own calendar is never touched. Each kind can be switched off from
# the console; switching off removes its events.

_CAL_API = "https://www.googleapis.com/calendar/v3"
TODO_DAYS_BACK, TODO_DAYS_AHEAD = 14, 90


def calendar_settings() -> dict[str, bool]:
    return {"birthdays": connections.get("google", "cal_birthdays") != "0",
            "todos": connections.get("google", "cal_todos") != "0"}


def _page_url(rel: str) -> str:
    base = os.environ.get("WIKI_PUBLIC_URL", "").rstrip("/")
    return f"{base}/ui/page/{rel}" if base else ""


def _birthday_events(today: datetime.date) -> dict[str, dict]:
    from wiki_server import people

    out = {}
    for c in people.cards():
        if not c["birthday"] or c["path"].endswith("self/identity.md"):
            continue
        d, m, y = c["birthday"]
        if (d, m) == (29, 2):
            d = 28
        start = datetime.date(y or today.year, m, d)
        note = []
        if y:
            note.append(f"Naissance : {y}.")
        if c["gifts"]:
            note.append("Idées cadeaux : " + " ; ".join(c["gifts"][:4]))
        if c["likes"]:
            note.append("Aime : " + " ; ".join(l[:100] for l in c["likes"][:3]))
        if _page_url(c["path"]):
            note.append(f"Fiche : {_page_url(c['path'])}")
        out[f"bday:{c['path']}"] = {
            "summary": f"🎂 {c['name']}",
            "description": "\n".join(note) or "Anniversaire noté dans ta mémoire.",
            "start": {"date": start.isoformat()},
            "end": {"date": (start + datetime.timedelta(days=1)).isoformat()},
            "recurrence": ["RRULE:FREQ=YEARLY"],
            "transparency": "transparent",
            # The day before at 09:00 (an all-day event starts at midnight).
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 15 * 60}]},
        }
    return out


def _todo_events(today: datetime.date) -> dict[str, dict]:
    out = {}
    for item in temporal.list_items(active_only=True):
        kind = item["meta"].get("type", "todo")
        if kind not in ("todo", "reminder") or "(agenda Google)" in item["body"]:
            continue
        due = temporal._as_date(item["meta"].get("due", ""))
        if not due or not (-TODO_DAYS_BACK <= (due - today).days <= TODO_DAYS_AHEAD):
            continue
        lines = [ln for ln in item["body"].splitlines() if ln.strip()]
        title = (lines[0] if lines else item["path"]).lstrip("#").strip()
        rest = "\n".join(lines[1:])[:600]
        note = (rest + "\n\n" if rest else "") + (
            "Noté dans ta mémoire. Dis « c'est fait » à Claude ou au bot pour le fermer.")
        if _page_url(item["path"]):
            note += f"\n{_page_url(item['path'])}"
        out[f"todo:{item['path']}"] = {
            "summary": ("🔔 " if kind == "reminder" else "☐ ") + title[:120],
            "description": note,
            "start": {"date": due.isoformat()},
            "end": {"date": (due + datetime.timedelta(days=1)).isoformat()},
            "transparency": "transparent",
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 15 * 60}]},
        }
    return out


def _memoire_calendar(st: dict) -> str:
    """The "Mémoire" calendar's id, created on first use (or again if the user
    deleted it, which also forgets its events)."""
    cid = st.get("id")
    if cid:
        try:
            connections.api("GET", "google", f"{_CAL_API}/calendars/{cid}")
            return cid
        except connections.ConnectionError_:
            st["events"] = {}
    from wiki_server.dream.schedule import read_schedule

    cal = connections.api("POST", "google", f"{_CAL_API}/calendars", json={
        "summary": "Mémoire",
        "description": "Écrit par ta mémoire : anniversaires et tâches datées. "
                       "Réglages : /ui/reglages/connexions.",
        "timeZone": read_schedule()["tz"]})
    st["id"] = cal["id"]
    return cal["id"]


def push_calendar(today: datetime.date) -> str:
    import hashlib

    settings = calendar_settings()
    state = _load()
    st = state.setdefault("memoire_cal", {})
    tracked: dict[str, dict] = st.setdefault("events", {})
    wanted: dict[str, dict] = {}
    if settings["birthdays"]:
        wanted.update(_birthday_events(today))
    if settings["todos"]:
        wanted.update(_todo_events(today))
    if not wanted and not tracked:
        return ("agenda Mémoire : rien à écrire pour l'instant" if any(settings.values())
                else "agenda Mémoire : désactivé")
    cid = _memoire_calendar(st)
    tracked = st.setdefault("events", {})  # reset if the calendar had to be recreated
    added = updated = removed = 0
    for key in [k for k in tracked if k not in wanted]:
        connections.api("DELETE", "google", f"{_CAL_API}/calendars/{cid}/events/{tracked[key]['id']}")
        tracked.pop(key)
        removed += 1
    for key, body in wanted.items():
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
        known = tracked.get(key)
        if known and known.get("hash") == digest:
            continue
        if known:
            connections.api("PUT", "google", f"{_CAL_API}/calendars/{cid}/events/{known['id']}", json=body)
            updated += 1
        else:
            ev = connections.api("POST", "google", f"{_CAL_API}/calendars/{cid}/events", json=body)
            known = {"id": ev["id"]}
            added += 1
        tracked[key] = {"id": known["id"], "hash": digest}
    _save(state)
    return f"agenda Mémoire : {added} ajouté(s), {updated} modifié(s), {removed} retiré(s)"


# --- running it -------------------------------------------------------------------

JOBS = {"strava": [("Strava", sync_strava)],
        "google": [("Agenda", sync_calendar), ("Agenda Mémoire", push_calendar), ("Gmail", sync_gmail)]}


def run(provider: str, today: datetime.date | None = None) -> str:
    """Sync one provider now; returns a one-line summary (errors included)."""
    today = today or datetime.date.today()
    out = []
    for label, job in JOBS[provider]:
        try:
            out.append(job(today))
        except connections.ConnectionError_ as exc:
            out.append(f"{label} : {exc}")
            connections.set_fields(provider, error=str(exc)[:200])
        except Exception as exc:
            _log.warning("sync %s failed: %s", label, type(exc).__name__)
            out.append(f"{label} : erreur ({type(exc).__name__})")
    summary = " · ".join(out)
    connections.set_fields(provider, last_sync=f"{today.isoformat()} : {summary}"[:300])
    return summary


def run_due(now_local: datetime.datetime) -> None:
    """Called on each scheduler tick: sync each connected provider once a day,
    from SYNC_HOUR on."""
    if now_local.hour < SYNC_HOUR:
        return
    state = _load()
    day = now_local.date().isoformat()
    for provider in JOBS:
        if not connections.status(provider)["connected"]:
            continue
        if state.get("last_run", {}).get(provider) == day:
            continue
        run(provider, now_local.date())
        state = _load()
        state.setdefault("last_run", {})[provider] = day
        _save(state)
