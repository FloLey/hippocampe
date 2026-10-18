"""Timeline and year in review, built from the memory."""

import datetime

from wiki_server import timeline


def test_months_gather_facts_items_and_sport(wiki, write):
    write("long_term/people/bebe.md", "# Bébé\n\n- Né le 2 juin 2026.\n- Visite le 2026-06-13.\n")
    write("temporal/2026-06-20-concert.md",
          "---\ntype: event\nstatus: active\ndue: 2026-06-20\n---\n\nConcert à Forest\n")
    write("temporal/2026-05-03-impots.md",
          "---\ntype: todo\nstatus: done\ndue: 2026-05-03\n---\n\nDéclaration d'impôts\n")
    write("long_term/sources/strava-2026.md",
          "| Date | Sport | Nom | Distance |\n|---|---|---|---|\n| 2026-06-05 | course | Footing | 10.0 km | 50 min |\n")
    ms = timeline.months(12, datetime.date(2026, 10, 17))
    assert [m["month"] for m in ms] == ["2026-06", "2026-05"]
    june = ms[0]
    assert [(i["date"], i["kind"]) for i in june["items"]] == [
        ("2026-06-02", "noté"), ("2026-06-13", "noté"), ("2026-06-20", "événement")]
    assert june["sport"] == {"n": 1, "km": 10.0}
    assert ms[1]["items"][0]["kind"] == "fait"


def test_wrapped_counts_the_year(wiki, write):
    write("temporal/2026-06-20-concert.md",
          "---\ntype: event\nstatus: active\ndue: 2026-06-20\n---\n\nConcert à Forest\n")
    write("long_term/sources/strava-2026.md",
          "| 2026-06-05 | course | Footing | 10.0 km | 50 min |\n| 2026-07-05 | course | Long | 21.1 km | 2h |\n")
    w = timeline.wrapped(2026)
    assert w["events"] == ["2026-06-20 · Concert à Forest"]
    assert w["sport_total"] == {"n": 2, "km": 31.1} and w["sport"]["course"]["n"] == 2
    assert w["top_month"] == "juin"
