"""Daily intake from Strava and Google, with the APIs stubbed out."""

import datetime

import pytest
from cryptography.fernet import Fernet

from wiki_server import connections, sync, temporal


@pytest.fixture
def api(wiki, tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_VAULT_KEY", Fernet.generate_key().decode())
    responses = {}
    calls = []

    def fake_get(provider, url, params=None):
        calls.append((provider, url, params))
        for key, value in responses.items():
            if url.endswith(key):
                return value(params) if callable(value) else value
        return {}

    monkeypatch.setattr(connections, "api_get", fake_get)
    return responses


def _run(day, km, name="Footing", sport="Run", aid=1):
    return {"id": aid, "name": name, "sport_type": sport, "distance": km * 1000, "moving_time": 1800,
            "total_elevation_gain": 40, "average_heartrate": 150.4,
            "start_date_local": f"{day}T08:00:00Z", "start_date": f"{day}T06:00:00Z"}


def test_strava_log_and_week_notes(api, wiki):
    acts = [_run("2026-10-06", 10, aid=1), _run("2026-10-08", 5, aid=2),
            _run("2026-10-14", 21.1, "Semi", aid=3)]
    api["/athlete/activities"] = lambda p: acts if p["page"] == 1 else []
    today = datetime.date(2026, 10, 17)  # a Saturday: the week of the 12th is not over
    assert sync.sync_strava(today) == "3 activité(s), 1 résumé(s) de semaine"
    log = (wiki / "long_term/sources/strava-2026.md").read_text()
    assert "| 2026-10-14 | course | Semi | 21.1 km | 30 min | 40 m | 150 | 3 |" in log
    notes = [p.read_text() for p in (wiki / "short_term/entries").glob("*.md")]
    assert len(notes) == 1 and "semaine du 2026-10-05" in notes[0] and "2 × course, 15.0 km" in notes[0]
    # Re-running adds nothing; the next week is summarized once it is over.
    assert sync.sync_strava(today) == "0 activité(s), 0 résumé(s) de semaine"
    assert sync.sync_strava(datetime.date(2026, 10, 19)) == "0 activité(s), 1 résumé(s) de semaine"
    assert log.count("| 1 |") == 1


def test_calendar_adds_updates_and_cancels(api, wiki):
    ev = {"id": "e1", "summary": "Dentiste", "start": {"dateTime": "2026-10-28T09:30:00+01:00"},
          "location": "Lyon"}
    api["/calendars/primary/events"] = {"items": [ev, {"id": "e2", "summary": "Refusé",
                                                      "start": {"date": "2026-10-29"},
                                                      "attendees": [{"self": True, "responseStatus": "declined"}]}]}
    today = datetime.date(2026, 10, 17)
    assert sync.sync_calendar(today) == "agenda : 1 ajouté(s), 0 modifié(s), 0 annulé(s)"
    items = temporal.list_items(active_only=True)
    assert len(items) == 1 and items[0]["meta"]["due"] == "2026-10-28"
    assert "Dentiste, à 09:30, Lyon (agenda Google)" in items[0]["body"]
    assert sync.sync_calendar(today).startswith("agenda : 0 ajouté(s), 0 modifié(s)")
    api["/calendars/primary/events"] = {"items": [{**ev, "start": {"dateTime": "2026-10-30T10:00:00+01:00"}}]}
    assert sync.sync_calendar(today) == "agenda : 0 ajouté(s), 1 modifié(s), 0 annulé(s)"
    assert temporal.list_items(active_only=True)[0]["meta"]["due"] == "2026-10-30"
    api["/calendars/primary/events"] = {"items": [{**ev, "status": "cancelled"}]}
    assert sync.sync_calendar(today).endswith("1 annulé(s)")
    assert temporal.list_items(active_only=True) == []


def test_gmail_label_becomes_note_once(api, wiki):
    api["/users/me/messages"] = {"messages": [{"id": "m1"}]}
    api["/users/me/messages/m1"] = {"snippet": "Votre colis arrive mardi",
                                    "payload": {"headers": [{"name": "From", "value": "Bpost <x@bpost.be>"},
                                                            {"name": "Subject", "value": "Livraison"}]}}
    today = datetime.date(2026, 10, 17)
    assert sync.sync_gmail(today) == "gmail : 1 email(s) étiqueté(s) « Mémoire »"
    note = next((wiki / "short_term/entries").glob("*.md")).read_text()
    assert "Email de Bpost" in note and "Livraison" in note and "colis arrive mardi" in note
    assert sync.sync_gmail(today).startswith("gmail : 0 email")


def test_run_due_once_a_day_for_connected(api, wiki, monkeypatch):
    ran = []
    monkeypatch.setattr(sync, "run", lambda provider, today=None: ran.append(provider))
    connections.set_fields("strava", refresh_token="r")
    sync.run_due(datetime.datetime(2026, 10, 17, 1, 0))
    assert ran == []  # before SYNC_HOUR
    sync.run_due(datetime.datetime(2026, 10, 17, 2, 5))
    sync.run_due(datetime.datetime(2026, 10, 17, 3, 5))
    assert ran == ["strava"]  # google not connected; strava once


@pytest.fixture
def gcal(wiki, monkeypatch):
    monkeypatch.setenv("WIKI_VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("WIKI_PUBLIC_URL", "https://wiki.example")
    cal = {"calendars": {}, "events": {}, "n": 0}

    def fake(method, provider, url, params=None, json=None):
        cal["n"] += 1
        path = url.split("/calendar/v3/")[1]
        if method == "POST" and path == "calendars":
            cal["calendars"]["c1"] = json
            return {"id": "c1"}
        if method == "GET" and path.startswith("calendars/"):
            if path.split("/")[1] not in cal["calendars"]:
                raise connections.ConnectionError_("Google : Not Found")
            return cal["calendars"][path.split("/")[1]]
        if method == "POST" and path.endswith("/events"):
            eid = f"e{len(cal['events']) + 1}"
            cal["events"][eid] = json
            return {"id": eid}
        eid = path.rsplit("/", 1)[1]
        if method == "PUT":
            cal["events"][eid] = json
        if method == "DELETE":
            cal["events"].pop(eid, None)
        return {}

    monkeypatch.setattr(connections, "api", fake)
    return cal


def test_memoire_calendar_birthdays_and_todos(gcal, wiki, write):
    write("long_term/people/lina.md", "# Lina\n\n- Date de naissance : 28 janvier 1991\n"
                                      "- Idée cadeau : laine mérinos\n- Aime les films Disney.\n")
    write("temporal/2026-10-28-dentiste.md",
          "---\ntype: todo\nstatus: active\ndue: 2026-10-28\n---\n\nAppeler le dentiste\nNuméro sur la carte.\n")
    write("temporal/2026-10-29-rdv.md",
          "---\ntype: event\nstatus: active\ndue: 2026-10-29\n---\n\nRdv (agenda Google)\n")
    today = datetime.date(2026, 10, 17)
    assert sync.push_calendar(today) == "agenda Mémoire : 2 ajouté(s), 0 modifié(s), 0 retiré(s)"
    bday, todo = sorted(gcal["events"].values(), key=lambda e: e["summary"])[::-1]
    assert bday["summary"] == "🎂 Lina" and bday["recurrence"] == ["RRULE:FREQ=YEARLY"]
    assert bday["start"] == {"date": "1991-01-28"}
    assert "Naissance : 1991." in bday["description"] and "laine mérinos" in bday["description"]
    assert "https://wiki.example/ui/page/long_term/people/lina.md" in bday["description"]
    assert todo["summary"] == "☐ Appeler le dentiste" and "Numéro sur la carte." in todo["description"]
    assert sync.push_calendar(today).startswith("agenda Mémoire : 0 ajouté(s), 0 modifié(s), 0 retiré(s)")
    temporal.mark_done("temporal/2026-10-28-dentiste.md")
    assert sync.push_calendar(today).endswith("1 retiré(s)")
    connections.set_fields("google", cal_birthdays="0")
    assert sync.push_calendar(today).endswith("1 retiré(s)") and gcal["events"] == {}


def test_memoire_calendar_recreated_if_deleted(gcal, wiki, write):
    write("long_term/people/lina.md", "# Lina\n\n- Date de naissance : 28 janvier 1991\n")
    today = datetime.date(2026, 10, 17)
    sync.push_calendar(today)
    gcal["calendars"].clear()
    gcal["events"].clear()
    assert sync.push_calendar(today).startswith("agenda Mémoire : 1 ajouté(s)")
    assert "c1" in gcal["calendars"]
