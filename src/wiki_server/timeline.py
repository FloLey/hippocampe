"""The user's life, month by month, and the year in review ("Wrapped").

Built on the fly from what the memory already holds, without any model call:
dated items (events, closed todos), dated facts in the long-term pages
("le 12 juin 2026", "2026-10-17", "(au 2026-09-16)"), the conversation
journal, the Strava log, and the pages' creation dates from git.
"""

from __future__ import annotations

import datetime
import re
import subprocess

from wiki_server.paths import wiki_root

_MONTHS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
           "septembre", "octobre", "novembre", "decembre"]
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]
_ISO_RE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_FR_RE = re.compile(r"\b(\d{1,2})(?:er)?\s+(" + "|".join(_MONTHS) + r")\s+(20\d\d)\b")
_CATEGORY_FR = {"people": "personnes", "projects": "projets", "places": "lieux",
                "concepts": "concepts", "organizations": "organisations", "sources": "sources"}


def _fold(text: str) -> str:
    from wiki_server.query import fold

    return fold(text)


def _clean(line: str) -> str:
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)  # links -> their text
    return line.strip().lstrip("-*#|> ").replace("**", "").strip()[:160]


def _page_title(text: str, rel: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return rel.rsplit("/", 1)[-1][:-3]


def _page_facts() -> list[dict]:
    """Dated lines of the long-term pages (one per line, its first date)."""
    from wiki_server.dream.pipeline import _ltm_pages

    out = []
    root = wiki_root()
    for rel in _ltm_pages():
        if rel.startswith("long_term/sources/strava-"):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        title = _page_title(text, rel)
        seen: set[tuple[str, str]] = set()
        for line in text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            day = None
            m = _ISO_RE.search(line)
            if m:
                day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            else:
                f = _FR_RE.search(_fold(line))
                if f:
                    day = f"{f.group(3)}-{_MONTHS.index(f.group(2)) + 1:02d}-{int(f.group(1)):02d}"
            if not day:
                continue
            text_line = _clean(line)
            if (day, text_line) in seen or len(text_line) < 8:
                continue
            seen.add((day, text_line))
            out.append({"date": day, "kind": "noté", "text": text_line, "page": rel, "title": title})
    return out


def _temporal_items() -> list[dict]:
    from wiki_server import temporal

    out = []
    for item in temporal.list_items():
        due = str(item["meta"].get("due", ""))[:10]
        if not _ISO_RE.match(due):
            continue
        kind = item["meta"].get("type", "todo")
        status = item["meta"].get("status", "active")
        label = {"event": "événement", "todo": "à faire", "reminder": "rappel"}.get(kind, kind)
        if status == "done" and kind != "event":
            label = "fait"
        first = item["body"].splitlines()[0] if item["body"] else item["path"]
        out.append({"date": due, "kind": label, "text": _clean(first), "page": item["path"], "title": ""})
    return out


def _journal_days() -> dict[str, int]:
    from wiki_server.journal import _all_lines

    days: dict[str, int] = {}
    for day, _line in _all_lines():
        days[day] = days.get(day, 0) + 1
    return days


def _strava_rows() -> list[dict]:
    out = []
    folder = wiki_root() / "long_term" / "sources"
    for p in sorted(folder.glob("strava-*.md")) if folder.is_dir() else []:
        for line in p.read_text(encoding="utf-8").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 5 and _ISO_RE.match(cells[0]):
                km = re.match(r"([\d.]+) km", cells[3])
                out.append({"date": cells[0], "sport": cells[1], "name": cells[2],
                            "km": float(km.group(1)) if km else 0.0})
    return out


def _created_pages() -> dict[str, str]:
    """{long-term page: date of its first commit}."""
    try:
        log = subprocess.run(
            ["git", "-C", str(wiki_root()), "log", "--reverse", "--diff-filter=A",
             "--format=@%cs", "--name-only", "--", "long_term"],
            check=True, capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    created: dict[str, str] = {}
    day = ""
    for line in log.splitlines():
        if line.startswith("@"):
            day = line[1:]
        elif line.strip() and line not in created:
            created[line] = day
    existing = set()
    from wiki_server.dream.pipeline import _ltm_pages

    existing = set(_ltm_pages())
    return {k: v for k, v in created.items() if k in existing}


def months(limit: int = 12, today: datetime.date | None = None) -> list[dict]:
    """The last ``limit`` months with something in them, newest first:
    {month: "2026-10", label, items: [...], conversations, sport: {n, km}, new_pages}."""
    today = today or datetime.date.today()
    horizon = (today + datetime.timedelta(days=60)).isoformat()
    items = [i for i in _page_facts() + _temporal_items() if i["date"] <= horizon]
    by_month: dict[str, dict] = {}

    def month(key: str) -> dict:
        if key not in by_month:
            y, m = int(key[:4]), int(key[5:7])
            by_month[key] = {"month": key, "label": f"{MONTHS_FR[m - 1]} {y}", "items": [],
                             "conversations": 0, "sport": {"n": 0, "km": 0.0}, "new_pages": []}
        return by_month[key]

    for i in items:
        month(i["date"][:7])["items"].append(i)
    for day, n in _journal_days().items():
        month(day[:7])["conversations"] += n
    for row in _strava_rows():
        s = month(row["date"][:7])["sport"]
        s["n"] += 1
        s["km"] += row["km"]
    for rel, day in _created_pages().items():
        if day:
            month(day[:7])["new_pages"].append(rel)
    for m in by_month.values():
        m["items"].sort(key=lambda i: (i["date"], i["text"]))
    return [by_month[k] for k in sorted(by_month, reverse=True)][:limit]


def wrapped(year: int) -> dict:
    """The year in review, as numbers and short lists."""
    y = str(year)
    created = {rel: d for rel, d in _created_pages().items() if d.startswith(y)}
    by_cat: dict[str, list[str]] = {}
    for rel in created:
        cat = rel.split("/")[1] if rel.count("/") >= 2 else "autre"
        by_cat.setdefault(cat, []).append(rel)
    sport = [r for r in _strava_rows() if r["date"].startswith(y)]
    sport_by: dict[str, dict] = {}
    for r in sport:
        s = sport_by.setdefault(r["sport"] or "autre", {"n": 0, "km": 0.0})
        s["n"] += 1
        s["km"] += r["km"]
    events = [i for i in _temporal_items() if i["date"].startswith(y) and i["kind"] == "événement"]
    done = [i for i in _temporal_items() if i["date"].startswith(y) and i["kind"] == "fait"]
    convs = sum(n for d, n in _journal_days().items() if d.startswith(y))
    try:
        notes = int(subprocess.run(
            ["git", "-C", str(wiki_root()), "log", "--oneline", f"--since={y}-01-01",
             f"--until={y}-12-31T23:59:59", "--grep=^stm: remember"],
            check=True, capture_output=True, text=True).stdout.count("\n"))
    except (subprocess.CalledProcessError, FileNotFoundError):
        notes = 0
    from wiki_server.graph import build_graph

    g = build_graph()
    degree: dict[str, int] = {}
    for e in g["edges"]:
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    titles = {n["id"]: n["label"] for n in g["nodes"]}
    hubs = [(titles.get(p, p), n) for p, n in sorted(degree.items(), key=lambda kv: -kv[1])
            if "/self/" not in p][:5]
    facts = [i for i in _page_facts() if i["date"].startswith(y)]
    busiest = {}
    for i in facts + events + done:
        busiest[i["date"][:7]] = busiest.get(i["date"][:7], 0) + 1
    top_month = max(busiest.items(), key=lambda kv: kv[1])[0] if busiest else ""
    return {
        "year": year, "notes": notes, "conversations": convs,
        "new_pages": len(created),
        "by_category": {(_CATEGORY_FR.get(c, c)): sorted(_title(r) for r in rels)
                        for c, rels in sorted(by_cat.items(), key=lambda kv: -len(kv[1]))},
        "events": [f"{i['date']} · {i['text']}" for i in sorted(events, key=lambda i: i["date"])],
        "done": len(done), "sport": sport_by,
        "sport_total": {"n": len(sport), "km": sum(r["km"] for r in sport)},
        "hubs": hubs,
        "top_month": (MONTHS_FR[int(top_month[5:7]) - 1] if top_month else ""),
    }


def _title(rel: str) -> str:
    try:
        return _page_title((wiki_root() / rel).read_text(encoding="utf-8"), rel)
    except (OSError, UnicodeDecodeError):
        return rel.rsplit("/", 1)[-1][:-3]
