"""The exam: questions with known answers, checked after every dream.

``exam/questions.md`` holds one question per line with the words its answer
must contain::

    - Quand a lieu le voyage en Islande ? → 3, 17, mai

A question passes when ``recall(question)`` (what Claude would load to answer
it) contains every expected word, accents and case ignored. The check is pure
retrieval, without any model call, so it is free and runs after each executed
dream: a fact the dream lost or buried shows up as a failure in the report, and
the score is kept in ``exam/history.md`` so a drop is visible at a glance.
The questions file is the user's to edit from the console.
"""

from __future__ import annotations

import datetime
import re

from wiki_server.paths import resolve_under_root
from wiki_server.query import fold

EXAM_FILE = "exam/questions.md"
HISTORY_FILE = "exam/history.md"
_LINE_RE = re.compile(r"^\s*[-*]\s*(.+?)\s*(?:→|->)\s*(.+?)\s*$")


def load() -> list[tuple[str, list[str]]]:
    path = resolve_under_root(EXAM_FILE)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE_RE.match(line)
        if m:
            words = [w.strip() for w in m.group(2).split(",") if w.strip()]
            if words:
                out.append((m.group(1), words))
    return out


def run() -> tuple[int, list[tuple[str, list[str]]]]:
    """(passed, failures as [(question, missing words)])."""
    from wiki_server.recall import recall

    passed, failures = 0, []
    for question, words in load():
        bundle = fold(recall(question))
        missing = [w for w in words if fold(w) not in bundle]
        if missing:
            failures.append((question, missing))
        else:
            passed += 1
    return passed, failures


def _last_score() -> tuple[int, int] | None:
    path = resolve_under_root(HISTORY_FILE)
    if not path.is_file():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        m = re.search(r"(\d+)/(\d+)", line)
        if m and line.startswith("- "):
            return int(m.group(1)), int(m.group(2))
    return None


def report_section(day: str | None = None) -> tuple[str, dict[str, str]]:
    """(markdown section for the dream report, files to write: the history)."""
    questions = load()
    if not questions:
        return "", {}
    day = day or datetime.date.today().isoformat()
    passed, failures = run()
    total = len(questions)
    previous = _last_score()
    trend = ""
    if previous and previous[1] == total and passed < previous[0]:
        trend = f" ⚠️ en baisse (avant : {previous[0]}/{total})"
    elif previous and previous[1] == total and passed > previous[0]:
        trend = f" (mieux qu'avant : {previous[0]}/{total})"
    lines = [f"## Examen\n\n{passed}/{total} questions retrouvées{trend}."]
    for q, missing in failures:
        lines.append(f"- ❌ {q} (manque : {', '.join(missing)})")
    history = resolve_under_root(HISTORY_FILE)
    text = history.read_text(encoding="utf-8") if history.is_file() else (
        "# Historique de l'examen\n\nUne ligne par rêve exécuté.\n\n")
    text = text.rstrip("\n") + f"\n- {day} : {passed}/{total}\n"
    return "\n".join(lines) + "\n", {HISTORY_FILE: text}
