"""The conversation journal: what each conversation was about, kept by itself.

Every tool call leaves a small trace (what was recalled, searched, saved or
closed) in an activity log outside git, on the vault volume. Calls closer than
``_GAP`` belong to the same conversation. Once a day is over, it is compiled
into ``journal/YYYY-MM.md``, one line per conversation, in a single commit.
prime shows the latest conversations (today's live, from the activity log),
recall the past ones on its topic, and "one year ago" what happened on this
date last year.

No model is involved: the lines are built from the topics and note summaries,
so the journal costs nothing and never invents.
"""

from __future__ import annotations

import datetime
import json
import logging
import threading

from wiki_server.paths import resolve_under_root, wiki_root
from wiki_server.sensitive import mask_sensitive

JOURNAL_DIR = "journal"
_GAP = datetime.timedelta(minutes=45)
_lock = threading.Lock()
_log = logging.getLogger("wiki_server.journal")


def _activity_dir():
    from wiki_server.vault import vault_root

    return vault_root() / "activity"


def _now_local() -> datetime.datetime:
    from wiki_server.dream.schedule import _now_local as now, read_schedule

    try:
        return now(read_schedule()["tz"]).replace(tzinfo=None)
    except Exception:
        return datetime.datetime.now()


def log(kind: str, detail: str) -> None:
    """Record one tool call. Never raises: the journal must not break a tool."""
    try:
        now = _now_local()
        entry = {"t": now.strftime("%H:%M"), "k": kind, "d": mask_sensitive(detail or "")[0][:120]}
        d = _activity_dir()
        with _lock:
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"{now.date().isoformat()}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        _log.warning("journal: could not log activity")


def _read_day(day: str) -> list[dict]:
    try:
        lines = (_activity_dir() / f"{day}.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _conversations(entries: list[dict]) -> list[list[dict]]:
    convs: list[list[dict]] = []
    last = None
    for e in entries:
        try:
            t = datetime.datetime.strptime(e["t"], "%H:%M")
        except (KeyError, ValueError):
            continue
        if last is None or t - last > _GAP:
            convs.append([])
        convs[-1].append(e)
        last = t
    return convs


def _line(conv: list[dict]) -> str:
    def of(*kinds):
        return list(dict.fromkeys(e["d"] for e in conv if e["k"] in kinds and e["d"]))

    parts = []
    topics = of("recall", "search")
    if topics:
        parts.append("sujets : " + ", ".join(topics[:6]))
    notes = of("remember")
    if notes:
        parts.append("noté : " + " ; ".join(notes[:5]) + (f" (+{len(notes) - 5})" if len(notes) > 5 else ""))
    done = of("complete")
    if done:
        parts.append("fermé : " + ", ".join(done[:5]))
    reads = of("read")
    if reads and not topics:
        parts.append("lu : " + ", ".join(reads[:4]))
    if not parts:
        parts.append("conversation sans recherche ni note")
    span = conv[0]["t"] if conv[0]["t"] == conv[-1]["t"] else f"{conv[0]['t']}–{conv[-1]['t']}"
    return f"- {span} · " + " · ".join(parts)


def day_lines(day: str) -> list[str]:
    return [_line(c) for c in _conversations(_read_day(day))]


def _month_rel(day: str) -> str:
    return f"{JOURNAL_DIR}/{day[:7]}.md"


def compile_day(day: str) -> bool:
    """Write ``day``'s conversations into its month file (idempotent). Returns
    whether the file changed."""
    from wiki_server.store import write_file

    lines = day_lines(day)
    if not lines:
        return False
    rel = _month_rel(day)
    path = resolve_under_root(rel)
    text = path.read_text(encoding="utf-8") if path.is_file() else f"# Journal {day[:7]}\n"
    blocks = _parse_month(text)
    if blocks.get(day) == lines:
        return False
    blocks[day] = lines
    body = f"# Journal {day[:7]}\n\nUne ligne par conversation, construite automatiquement.\n"
    for d in sorted(blocks, reverse=True):
        body += f"\n## {d}\n\n" + "\n".join(blocks[d]) + "\n"
    write_file(rel, body, f"journal: {day}")
    return True


def _parse_month(text: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            blocks[current] = []
        elif current and line.startswith("- "):
            blocks[current].append(line)
    return blocks


def compile_pending(today: datetime.date | None = None, days: int = 7) -> None:
    """Compile the finished days of the last week that are not in the journal
    yet (called by the scheduler; cheap when there is nothing to do)."""
    today = today or _now_local().date()
    for n in range(1, days + 1):
        try:
            compile_day((today - datetime.timedelta(days=n)).isoformat())
        except Exception:
            _log.warning("journal: compile failed")


def _all_lines() -> list[tuple[str, str]]:
    """(day, line) for every compiled conversation, newest first."""
    root = wiki_root() / JOURNAL_DIR
    out = []
    if root.is_dir():
        for p in sorted(root.glob("*.md"), reverse=True):
            for day, lines in sorted(_parse_month(p.read_text(encoding="utf-8")).items(), reverse=True):
                out.extend((day, ln) for ln in lines)
    return out


def recent(n: int = 5, today: datetime.date | None = None) -> list[str]:
    """The latest ``n`` conversations, today's included (live), newest first."""
    today = today or _now_local().date()
    compiled = _all_lines()
    done = {d for d, _ in compiled}
    live = []  # today, and any day of the past week the scheduler has not compiled yet
    for k in range(8):
        day = (today - datetime.timedelta(days=k)).isoformat()
        if k == 0 or day not in done:
            live.extend((day, ln) for ln in reversed(day_lines(day)))
    past = [(d, ln) for d, ln in compiled if d != today.isoformat()]
    merged = sorted(live + past, key=lambda t: t[0], reverse=True)  # stable: a day keeps its order
    return [f"{d} {ln[2:]}" for d, ln in merged[:n]]


def about(keywords_match, n: int = 5) -> list[str]:
    """Past conversations whose line matches (``keywords_match(text) -> bool``)."""
    return [f"{d} {ln[2:]}" for d, ln in _all_lines() if keywords_match(ln)][:n]


def one_year_ago(today: datetime.date | None = None) -> list[str]:
    """What happened on this date last year: the journal and the dated items."""
    from wiki_server import temporal

    today = today or _now_local().date()
    try:
        then = today.replace(year=today.year - 1)
    except ValueError:  # 29 February
        then = today.replace(year=today.year - 1, day=28)
    out = [f"conversation · {ln[2:]}" for d, ln in _all_lines() if d == then.isoformat()]
    for item in temporal.list_items():
        due = temporal._as_date(item["meta"].get("due", ""))
        if due and abs((due - then).days) <= 1:
            first = item["body"].splitlines()[0] if item["body"] else item["path"]
            out.append(f"{item['meta'].get('type', 'item')} ({due.isoformat()}) · {first}")
    return out
