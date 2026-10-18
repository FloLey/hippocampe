"""The conversation journal, built from tool calls."""

import datetime
import os

os.environ.setdefault("WIKI_AUTH_DISABLED", "1")

from wiki_server import journal, server  # noqa: E402
from wiki_server.recall import recall  # noqa: E402


def _at(monkeypatch, day, hm):
    h, m = map(int, hm.split(":"))
    when = datetime.datetime.fromisoformat(day).replace(hour=h, minute=m)
    monkeypatch.setattr(journal, "_now_local", lambda: when)


def _tool(name):
    t = getattr(server, name)
    return getattr(t, "fn", t)


def test_calls_group_into_conversations_and_compile(wiki, write, monkeypatch):
    write("long_term/places/islande.md", "# Islande\n\nVoyage.\n")
    _at(monkeypatch, "2026-10-17", "10:00")
    _tool("prime")()
    _tool("recall")("Islande")
    _at(monkeypatch, "2026-10-17", "10:20")
    _tool("remember")("Visite de Skogafoss", summary="Visite du temple Skogafoss")
    _at(monkeypatch, "2026-10-17", "18:00")  # > 45 min later: a new conversation
    _tool("search")("levain")
    lines = journal.day_lines("2026-10-17")
    assert lines == ["- 10:00–10:20 · sujets : Islande · noté : Visite du temple Skogafoss",
                     "- 18:00 · sujets : levain"]
    assert journal.compile_day("2026-10-17")
    assert not journal.compile_day("2026-10-17")  # idempotent
    month = (wiki / "journal/2026-10.md").read_text()
    assert "## 2026-10-17" in month and "Skogafoss" in month


def test_prime_shows_recent_and_recall_past(wiki, write, monkeypatch):
    write("long_term/places/islande.md", "# Islande\n\nVoyage.\n")
    write("long_term/index.md", "# LTM\n")
    _at(monkeypatch, "2026-10-16", "09:00")
    _tool("recall")("Islande")
    journal.compile_day("2026-10-16")
    _at(monkeypatch, "2026-10-17", "08:00")
    _tool("search")("Nina")
    out = _tool("prime")()
    assert "recent conversations" in out
    assert out.index("2026-10-17 08:00") < out.index("2026-10-16 09:00")
    assert "Past conversations on this topic" in recall("Islande")


def test_one_year_ago(wiki, write, monkeypatch):
    write("temporal/2025-10-17-x.md", "---\ntype: event\nstatus: done\ndue: 2025-10-17\n---\n\nConcert à Forest\n")
    assert journal.one_year_ago(datetime.date(2026, 10, 17)) == ["event (2025-10-17) · Concert à Forest"]
    assert journal.one_year_ago(datetime.date(2026, 10, 25)) == []


def test_secrets_never_logged(wiki, monkeypatch):
    _at(monkeypatch, "2026-10-17", "10:00")
    journal.log("search", "RN 90.02.03-123.45")
    assert "90.02.03" not in str(journal._read_day("2026-10-17"))
