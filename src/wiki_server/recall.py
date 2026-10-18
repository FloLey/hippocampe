"""recall(topic): everything the memory holds on one topic, in one call.

prime stays light (who the user is, what is active); the detail is loaded on
demand. recall ranks the pages with the search scorer, then returns the best
pages in full, who links to them, the dated items and open questions on the
topic, and the recent notes not consolidated yet (the dream has not filed them,
so they are the freshest facts). A few more pages that mention the topic close
the bundle, to read if needed.
"""

from __future__ import annotations

import re

from wiki_server import questions, temporal
from wiki_server.paths import resolve_under_root
from wiki_server.query import _keywords, _match_keywords, _safe_read, rank_pages

_MAX_PAGES = 3
_FOCUS_ABOVE = 3000  # pages longer than this come back cut to the matching sections
_PAGE_CHARS = 8000
_MIN_RELATIVE_SCORE = 0.35  # a page far below the best one is a passing mention
_SKIP = ("long_term/index.md", "short_term/index.md")


def _focus(text: str, keywords: list[str]) -> str:
    """A long page cut down to its sections about the topic; the other sections
    are named at the end so they can still be read."""
    if len(text) <= _FOCUS_ABOVE:
        return text
    parts = re.split(r"(?m)^(?=## )", text)
    kept, skipped = [parts[0]], []
    for part in parts[1:]:
        (kept if _match_keywords(part, keywords) else skipped).append(part)
    if len(kept) == 1:  # the topic is only in the title or intro: keep it whole
        return text
    out = "".join(kept).rstrip()
    if skipped:
        out += "\n\n(Other sections: " + ", ".join(
            p.splitlines()[0][3:].strip() for p in skipped) + ")"
    return out


def _mentions(text: str, keywords: list[str]) -> bool:
    hits = _match_keywords(text, keywords)
    return len(hits) >= max(1, min(2, len(keywords)))


def recall(topic: str, max_pages: int = _MAX_PAGES) -> str:
    keywords = _keywords(topic)
    if not keywords:
        return "Empty topic."
    ranked = [(rel, sc) for rel, sc in rank_pages(topic) if rel not in _SKIP]
    pages = [(rel, sc) for rel, sc in ranked if rel.startswith("long_term/")]
    notes = [rel for rel, _ in ranked if rel.startswith("short_term/entries/")]
    if not pages and not notes:
        return f"Nothing in memory about {topic!r}."

    out: list[str] = [f"# Memory on {topic!r}"]
    best = pages[0][1] if pages else 0
    main = [rel for rel, sc in pages[:max(1, max_pages)] if sc >= best * _MIN_RELATIVE_SCORE]
    from wiki_server.graph import backlinks
    from wiki_server.store import last_changed

    changed = last_changed() if main else {}
    for rel in main:
        text = _focus(_safe_read(resolve_under_root(rel)), keywords)
        if len(text) > _PAGE_CHARS:
            text = text[:_PAGE_CHARS] + f"\n… (truncated, read({rel!r}) for the rest)"
        section = f"## {rel}" + (f" (last updated {changed[rel]})" if rel in changed else "") + f"\n\n{text}"
        try:
            linked = [b for b, _title in backlinks(rel) if b not in _SKIP][:12]
        except Exception:
            linked = []
        if linked:
            section += "\n\nLinked from: " + ", ".join(linked)
        out.append(section)

    items = []
    for item in temporal.list_items(active_only=True):
        if _mentions(item["body"], keywords):
            first = item["body"].splitlines()[0] if item["body"] else ""
            items.append(f"- [{item['meta'].get('type', 'item')}] {first} "
                         f"(due {item['meta'].get('due', '-')}) {item['path']}")
    if items:
        out.append("## Active dated items\n\n" + "\n".join(items))

    if notes:
        bodies = []
        for rel in notes[:5]:
            body = _safe_read(resolve_under_root(rel))
            bodies.append(f"### {rel}\n{body[:1500]}")
        out.append("## Recent notes, not consolidated yet (newest facts)\n\n" + "\n\n".join(bodies))

    open_q = [q for q in questions.list_questions(open_only=True) if _mentions(q["body"], keywords)]
    if open_q:
        out.append("## Open questions on this topic (ask the user if it fits)\n\n"
                   + "\n".join(f"- {q['body'].splitlines()[0]} {q['path']}" for q in open_q))

    from wiki_server import journal

    past = journal.about(lambda line: _mentions(line, keywords))
    if past:
        out.append("## Past conversations on this topic\n\n" + "\n".join(f"- {c}" for c in past))

    more = [rel for rel, _ in pages if rel not in main][:6]
    if more:
        out.append("## Also mentioned in\n\n" + "\n".join(f"- {rel}" for rel in more))
    return "\n\n".join(out)
