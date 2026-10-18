"""Open questions: what the memory is unsure about and should ask the user.

Instead of guessing (is "ma copine" the Nina we already know? did the trip run
from the 10th or the 11th?), the dream files a question here and leaves the
uncertain part pending. ``prime`` lists the open questions so Claude can ask
them, one at a time, when the conversation allows; the answer is captured with
``remember`` and the question closed with ``complete``. The next dream then
resolves the pending part with the answer in hand.

Each question has a ``key``: a question is never filed twice under the same key,
whether it is still open or already answered, so the user is never asked the
same thing again.
"""

from __future__ import annotations

import datetime
import re

from wiki_server.paths import resolve_under_root, wiki_root
from wiki_server.store import fm_value, parse_frontmatter, slugify, unique_stem

QUESTIONS_DIR = "questions"


def question_key(text: str) -> str:
    """A key for a model-written question: its words, folded and sorted, so a
    reworded duplicate ("Nina et copine : même personne ?" vs "Copine et Nina :
    même personne ?") lands on the same key."""
    from wiki_server.query import fold

    words = sorted(set(re.findall(r"[a-z0-9]{3,}", fold(text))))
    return "q:" + "-".join(words)[:160]


def list_questions(open_only: bool = False) -> list[dict]:
    """All questions, oldest first, each as {path, meta, body}."""
    d = resolve_under_root(QUESTIONS_DIR)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        try:
            meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if open_only and meta.get("status", "open") != "open":
            continue
        out.append({"path": p.relative_to(wiki_root()).as_posix(), "meta": meta, "body": body.strip()})
    return out


def known_keys() -> set[str]:
    """Keys of every question ever filed (open or answered)."""
    return {q["meta"].get("key", "") for q in list_questions()} - {""}


def _render(key: str, created: str, status: str, about: str, body: str) -> str:
    fm = [f"key: {fm_value(key)}", f"created: {fm_value(created)}", f"status: {status}"]
    if about:
        fm.append(f"about: {fm_value(about)}")
    return "---\n" + "\n".join(fm) + f"\n---\n\n{body.strip()}\n"


def answered_keys() -> set[str]:
    """Keys of the questions the user has already answered."""
    return {q["meta"].get("key", "") for q in list_questions()
            if q["meta"].get("status") == "answered"} - {""}


def build_question(text: str, key: str = "", about: str = "",
                   taken: set[str] | None = None) -> tuple[str, str]:
    """(relative path, content) for a new open question. Pure, so the dream can
    batch it into its single commit."""
    created = datetime.date.today().isoformat()
    stem = unique_stem(QUESTIONS_DIR, f"{created}-{slugify(text, maxlen=50)}", taken)
    return (f"{QUESTIONS_DIR}/{stem}.md",
            _render(key or question_key(text), created, "open", about, text))


def resolve_question(rel: str) -> str | None:
    """Normalize a question reference to its path, or None if there is none."""
    rel = (rel or "").strip().strip("'\"").lstrip("/")
    if not rel.startswith(f"{QUESTIONS_DIR}/"):
        return None
    if not rel.endswith(".md"):
        rel += ".md"
    try:
        path = resolve_under_root(rel)
    except Exception:
        return None
    if not path.is_file() or path.parent != resolve_under_root(QUESTIONS_DIR):
        return None
    return rel


def mark_answered(rel: str) -> bool:
    """Close a question (status: answered). Its key stays known, so it is never
    asked again."""
    from wiki_server.store import write_file

    rel = resolve_question(rel)
    if rel is None:
        return False
    path = resolve_under_root(rel)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    write_file(rel, _render(meta.get("key", ""), meta.get("created", ""), "answered",
                            meta.get("about", ""), body),
               f"manual: question answered {path.stem}")
    return True
