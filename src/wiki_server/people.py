"""People cards: birthday, what they like, gift ideas, when last mentioned.

Read from the people pages as they are (no model): a birthday next to "né",
"née", "naissance" or "anniversaire"; likes from lines with "aime", "adore",
"fan de"; gift ideas from a line or section mentioning "cadeau". The write stage
keeps a "## Fiche" at the top of each people page so these stay easy to find.
Upcoming birthdays reach prime and the Sunday digest.
"""

from __future__ import annotations

import datetime
import re

from wiki_server.paths import wiki_root
from wiki_server.query import fold

_MONTHS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
           "septembre", "octobre", "novembre", "decembre"]
_BIRTH_CUE = re.compile(r"\b(nee?|naissance|anniversaire|birthday|born)\b")
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})(?:er)?\s+(" + "|".join(_MONTHS) + r")(?:\s+(\d{4}))?\b"), "dmy_word"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    (re.compile(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{4}))?\b"), "dmy_num"),
]
_LIKE_CUE = re.compile(r"\b(aime|adore|fan de|passionnee? de|apprecie)\b")
_GIFT_CUE = re.compile(r"cadeau")


def _birthday(line: str) -> tuple[int, int, int | None] | None:
    """(day, month, year or None) when the line states a birth date."""
    # The line must open with the cue ("Née le…", "Date de naissance :"), so a
    # child's birth on the parent's page ("Un fils, Hugo, né le…") is not taken.
    f = fold(_clean(line))
    cue = _BIRTH_CUE.match(f) or re.match(r"date de (naissance)\b", f)
    if not cue:
        return None
    rest = f[cue.start():]
    for rx, kind in _DATE_PATTERNS:
        m = rx.search(rest)
        if not m:
            continue
        try:
            if kind == "dmy_word":
                d, mo, y = int(m.group(1)), _MONTHS.index(m.group(2)) + 1, m.group(3)
            elif kind == "iso":
                y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            else:
                d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
            datetime.date(2000, mo, d)  # validates day/month (2000 is a leap year)
            return d, mo, int(y) if y else None
        except ValueError:
            continue
    return None


def _clean(line: str) -> str:
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
    return line.strip().lstrip("-*> ").replace("**", "").strip()


def card(rel: str, text: str | None = None) -> dict:
    if text is None:
        text = (wiki_root() / rel).read_text(encoding="utf-8")
    title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")),
                 rel.rsplit("/", 1)[-1][:-3])
    birthday = None
    likes, gifts = [], []
    section = ""
    for line in text.splitlines():
        if line.startswith("## "):
            section = fold(line)
            continue
        if not line.strip() or line.startswith("#"):
            continue
        f = fold(line)
        if birthday is None and "mariage" not in f and "marie" not in f.split(":")[0]:
            birthday = _birthday(line)
        if _GIFT_CUE.search(section) or _GIFT_CUE.search(f):
            gifts.append(_clean(line))
        elif _LIKE_CUE.search(f):
            likes.append(_clean(line))
    return {"path": rel, "name": title, "birthday": birthday,
            "likes": likes[:6], "gifts": [g for g in gifts if g][:8]}


def _people_pages() -> list[str]:
    folder = wiki_root() / "long_term" / "people"
    pages = sorted(p.relative_to(wiki_root()).as_posix() for p in folder.glob("*.md")) if folder.is_dir() else []
    self_page = wiki_root() / "long_term" / "self" / "identity.md"
    return pages + (["long_term/self/identity.md"] if self_page.is_file() else [])


def cards() -> list[dict]:
    out = []
    for rel in _people_pages():
        try:
            c = card(rel)
        except (OSError, UnicodeDecodeError):
            continue
        if rel.endswith("self/identity.md"):
            c["name"] = "Toi"
        out.append(c)
    return out


def next_birthday(bday: tuple[int, int, int | None], today: datetime.date) -> tuple[datetime.date, int | None]:
    """(date of the next birthday, age reached then or None)."""
    d, m, y = bday
    for year in (today.year, today.year + 1):
        try:
            when = datetime.date(year, m, d)
        except ValueError:  # 29 February on a non-leap year
            when = datetime.date(year, m, 28)
        if when >= today:
            return when, (year - y if y else None)
    raise AssertionError("unreachable")


def upcoming_birthdays(today: datetime.date, days: int = 30) -> list[str]:
    out = []
    for c in cards():
        if not c["birthday"]:
            continue
        when, age = next_birthday(c["birthday"], today)
        delta = (when - today).days
        if delta <= days:
            label = "aujourd'hui" if delta == 0 else ("demain" if delta == 1 else f"dans {delta} jours")
            age_txt = f", {age} ans" if age is not None and age > 0 else ""
            gifts = f" · idées cadeaux : {'; '.join(c['gifts'][:3])}" if c["gifts"] else ""
            out.append((delta, f"{c['name']} : {when.day}/{when.month} ({label}{age_txt}){gifts}"))
    return [text for _, text in sorted(out)]


def missing_birthdays(limit: int = 1) -> list[str]:
    """The most linked people pages without a birthday (for a gentle question)."""
    from wiki_server.graph import build_graph

    missing = {c["path"] for c in cards() if not c["birthday"] and "/people/" in c["path"]}
    degree: dict[str, int] = {}
    for e in build_graph()["edges"]:
        if e["target"] in missing:
            degree[e["target"]] = degree.get(e["target"], 0) + 1
    return [p for p, _ in sorted(degree.items(), key=lambda kv: -kv[1])][:limit]
