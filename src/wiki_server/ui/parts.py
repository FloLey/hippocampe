"""Pieces shared by several pages: a todo line with its "Fait" button, a
question with its answer box, the birthday list, the dream report summary,
recently changed pages."""

from __future__ import annotations

import datetime
import re

from wiki_server.ui.layout import (
    ago, as_date, chip, empty, esc, fmt_date, page_url, plural, post_button, pretty_name,
    row, rows, title_of, until,
)

KIND_ICON = {"todo": "☐", "reminder": "🔔", "event": "📍"}
KIND_LABEL = {"todo": "À faire", "reminder": "Rappel", "event": "Événement"}


def today() -> datetime.date:
    from wiki_server.query import _today_local

    return _today_local()[0]


def safe_back(value, default: str = "/ui") -> str:
    """A redirect target from a form: only a console path, never another site."""
    value = str(value or "")
    return value if value.startswith("/ui") and not value.startswith("//") else default


def gift_text(line: str) -> str:
    """A gift idea without its "Idées cadeaux :" label."""
    return re.sub(r"^\s*id[ée]es?\s+cadeaux?\s*:\s*", "", line, flags=re.I).strip()


def clip(text: str, n: int) -> str:
    """``text`` cut to about ``n`` characters on a word boundary, with "…"."""
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def first_line(body: str) -> str:
    for line in (body or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


# ---- temporal items ----

def buckets(day: datetime.date) -> dict[str, list[dict]]:
    """Active items sorted into late / week / later / undated (events included)."""
    from wiki_server import temporal

    out: dict[str, list[dict]] = {"late": [], "week": [], "later": [], "undated": []}
    horizon = day + datetime.timedelta(days=7)
    for item in temporal.list_items(active_only=True):
        show, late = temporal.surface_state(item["meta"], day)
        if not show:
            continue
        due = as_date(item["meta"].get("due", ""))
        key = "late" if late else ("undated" if due is None else ("week" if due <= horizon else "later"))
        out[key].append(item)
    for key in out:
        out[key].sort(key=lambda i: (str(i["meta"].get("due", "")), i["path"]))
    return out


def item_row(item: dict, csrf: str, day: datetime.date, back: str) -> str:
    meta = item["meta"]
    kind = meta.get("type", "todo")
    label, tone = until(meta.get("due", ""), day)
    if kind == "event" and tone == "late":
        label, tone = "passé", ""
    trailing = chip(label, tone) if label else ""
    if meta.get("status", "active") == "active" and kind != "event":
        trailing += post_button(csrf, "/ui/temporal/done", "Fait",
                                {"path": item["path"], "back": back}, kind="secondary small")
    return row(esc(first_line(item["body"]) or pretty_name(item["path"])),
               esc(KIND_LABEL.get(kind, kind)), page_url(item["path"]), trailing,
               lead=KIND_ICON.get(kind, "•"))


# ---- questions ----

def question_block(q: dict, csrf: str, back: str, open_: bool = False) -> str:
    text = q["body"].strip()
    about = str(q["meta"].get("about", "") or "")
    sub = f'À propos de <a href="{esc(page_url(about))}">{esc(pretty_name(about))}</a>' if about.endswith(".md") else ""
    sub = sub or f"Posée {ago(q['meta'].get('created', ''), today())}"
    form = (
        f'<details class="answer"{" open" if open_ else ""}><summary>Répondre</summary>'
        '<form method="post" action="/ui/questions/answer">'
        f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
        f'<input type="hidden" name="path" value="{esc(q["path"])}">'
        f'<input type="hidden" name="back" value="{esc(back)}">'
        '<textarea name="answer" rows="2" required placeholder="Ta réponse, en une phrase"></textarea>'
        '<div class="actions"><button class="btn primary" type="submit">Envoyer</button></div></form>'
        f'<div class="actions">{post_button(csrf, "/ui/questions/skip", "Je ne sais pas, ne plus demander", {"path": q["path"], "back": back}, kind="quiet small")}</div>'
        "</details>")
    return (f'<li class="row"><span class="lead">❓</span><div class="row-main">'
            f'<span>{esc(text)}</span><small>{sub}</small>{form}</div></li>')


def open_questions() -> list[dict]:
    from wiki_server import questions

    return questions.list_questions(open_only=True)


# ---- birthdays ----

def birthdays(day: datetime.date, days: int) -> list[tuple[int, dict, datetime.date, int | None]]:
    """[(days left, card, date, age)] for birthdays within ``days``, soonest first."""
    from wiki_server import people

    out = []
    for c in people.cards():
        if not c["birthday"]:
            continue
        when, age = people.next_birthday(c["birthday"], day)
        delta = (when - day).days
        if delta <= days:
            out.append((delta, c, when, age))
    return sorted(out, key=lambda t: (t[0], t[1]["name"]))


def birthday_row(delta: int, c: dict, when: datetime.date, age: int | None, day: datetime.date) -> str:
    label = "aujourd'hui 🎉" if delta == 0 else ("demain" if delta == 1 else f"dans {delta} j")
    bits = [fmt_date(when, day, weekday=True)]
    if age:
        bits.append(f"{age} ans")
    if c["gifts"]:
        bits.append("idée cadeau : " + clip(gift_text(c["gifts"][0]), 80))
    return row(esc(c["name"]), esc(" · ".join(bits)), page_url(c["path"]),
               chip(label, "soon" if delta <= 7 else ""), lead="🎂")


# ---- dream reports ----

_NOT_A_TOPIC = ("Plan", "Coût", "Appliqué", "Santé", "Examen", "Questions", "Erreur", "Résumé")


def report_summary(rel: str) -> dict:
    """{path, day, kind, topics, exam} for a dream report."""
    from wiki_server.paths import resolve_under_root

    name = rel.rsplit("/", 1)[-1][:-3]
    kind = ("simulation" if name.endswith("-dryrun") else
            "secrets" if name.endswith("-secrets") else
            "migration" if "migration" in name else "appliqué")
    try:
        text = resolve_under_root(rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    topics = [ln for ln in text.splitlines()
              if ln.startswith("## ") and not ln[3:].startswith(_NOT_A_TOPIC)]
    exam = ""
    m = re.search(r"^## Examen\s+(\d+/\d+)", text, re.M)
    if m:
        exam = m.group(1)
    return {"path": rel, "day": name[:10], "kind": kind, "topics": len(topics), "exam": exam,
            "error": "Erreur" in text[:400]}


def report_row(r: dict, day: datetime.date) -> str:
    bits = [r["kind"]]
    if r["kind"] in ("appliqué", "simulation"):
        bits.append(plural(r["topics"], "sujet"))
    if r["exam"]:
        bits.append(f"examen {r['exam']}")
    return row(esc(fmt_date(r["day"], day)), esc(" · ".join(bits)), page_url(r["path"]),
               chip("erreur", "late") if r["error"] else "", lead="🌙")


def stm_entries() -> list[dict]:
    """Notes waiting for the dream, newest first: {path, created, text}."""
    from wiki_server.paths import wiki_root
    from wiki_server.store import parse_frontmatter

    folder = wiki_root() / "short_term" / "entries"
    out = []
    for p in sorted(folder.glob("*.md"), reverse=True) if folder.is_dir() else []:
        try:
            meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        out.append({"path": p.relative_to(wiki_root()).as_posix(),
                    "created": str(meta.get("created", ""))[:10] or p.name[:10],
                    "text": first_line(body)})
    return out


# ---- pages ----

def recent_pages(limit: int = 6) -> list[tuple[str, str, str]]:
    """[(path, title, day)] of the long-term pages changed most recently."""
    from wiki_server.paths import wiki_root
    from wiki_server.store import last_changed

    out = []
    for rel, day in sorted(last_changed().items(), key=lambda kv: kv[1], reverse=True):
        if not rel.startswith("long_term/") or not rel.endswith(".md") or rel.endswith("/index.md"):
            continue
        path = wiki_root() / rel
        if not path.is_file():
            continue
        try:
            title = title_of(path.read_text(encoding="utf-8"), rel)
        except (OSError, UnicodeDecodeError):
            continue
        out.append((rel, title, day))
        if len(out) >= limit:
            break
    return out


def page_rows(entries: list[tuple[str, str, str]], day: datetime.date) -> str:
    if not entries:
        return empty("Rien pour l'instant.")
    return rows([row(esc(t), "", page_url(r), f"<small>{esc(ago(d, day))}</small>") for r, t, d in entries])
