"""Capture-time feedback: what ``remember`` tells Claude right after a note.

The best moment to clear up a doubt is the conversation itself, while the user
is there: the nightly dream can only file a question and wait. So ``remember``
answers with the existing pages the note seems to be about, and points out what
it cannot settle on its own: a first name that several people pages share
("Bastien": bastien-dupont or bastien-martin?) or a relation word ("ma copine")
the note does not resolve. Claude then asks the user one short question and
records the answer. Pure ranking, no model call, so it stays instant.
"""

from __future__ import annotations

import logging
import re

from wiki_server.query import fold

# Relation words that name someone without saying who. Folded (no accents).
_RELATION_RE = re.compile(
    r"\b(?:(?:ma|mon|mes)\s+(?:copine|copain|compagne|compagnon|femme|mari|"
    r"partenaire|ex|mere|pere|maman|papa|frere|soeur|fils|fille|cousin|cousine|"
    r"oncle|tante|pote|potes|ami|amie|boss|chef|collegue|voisin|voisine|"
    r"beau-pere|belle-mere|beau-frere|belle-soeur)|"
    r"my\s+(?:girlfriend|boyfriend|partner|wife|husband|ex|mom|mum|mother|dad|"
    r"father|brother|sister|son|daughter|cousin|uncle|aunt|friend|boss|"
    r"colleague|neighbou?r))\b")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _shared_first_names(words: set[str], pages: list[str]) -> dict[str, list[str]]:
    """Name tokens of the text that several people pages share, unless the
    text names one of them in full ("Bastien Martin" is not ambiguous)."""
    by_token: dict[str, list[str]] = {}
    full: dict[str, set[str]] = {}
    for p in pages:
        if "/people/" not in p:
            continue
        full[p] = {t for t in _TOKEN_RE.findall(fold(p.rsplit("/", 1)[-1][:-3])) if len(t) >= 3}
        for t in full[p]:
            by_token.setdefault(t, []).append(p)
    return {t: ps for t, ps in by_token.items()
            if t in words and len(ps) > 1 and not any(full[p] <= words for p in ps)}


def ambiguities(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """What the note leaves open: first names several people pages share (each
    with its candidate pages) and relation words without a name."""
    from wiki_server.dream.pipeline import _ltm_pages

    try:
        pages = _ltm_pages()
    except Exception:
        return {}, []
    folded = fold(text)
    shared = _shared_first_names(set(_TOKEN_RE.findall(folded)), pages)
    relations = list(dict.fromkeys(m.group(0) for m in _RELATION_RE.finditer(folded)))
    return shared, relations


def feedback(text: str) -> str:
    """The note on what the memory already holds about ``text``, or ""."""
    from wiki_server.dream.index import _index_descriptions
    from wiki_server.dream.pipeline import _ltm_pages, _related_pages

    try:
        related = _related_pages(text, _ltm_pages())
        descs = _index_descriptions()
    except Exception:  # feedback is a courtesy: never fail the capture over it
        return ""
    shared, relations = ambiguities(text)

    parts = []
    if related:
        listed = "; ".join(f"{p}" + (f" ({descs[p]})" if descs.get(p) else "") for p in related)
        parts.append(f"Already in memory: {listed}.")
    doubts = [f'"{t}" could be {" or ".join(ps)}' for t, ps in sorted(shared.items())]
    if relations:
        doubts.append(f"the note says {', '.join(repr(r) for r in relations)} without a name")
    if doubts:
        parts.append(
            "Unclear: " + "; ".join(doubts) + ". Unless the conversation already "
            "settles it, ask the user now (one short question) and remember the "
            "answer, rather than leaving the nightly consolidation to guess.")
    return " ".join(parts)


def protect(text: str) -> tuple[str, list[str]]:
    """Move the secrets in ``text`` to the vault (or mask them), returning the
    safe text and the kinds found. Any vault failure masks instead: a secret
    must never fall through to disk in clear."""
    from wiki_server import vault
    from wiki_server.sensitive import mask_sensitive, replace_sensitive

    if not text:
        return text, []
    if vault.enabled():
        try:
            return replace_sensitive(
                text, lambda kind, value, before: f"[SECRET: {vault.put(value, kind, before)}]")
        except Exception:
            logging.getLogger("wiki_server").exception("vault write failed, masking instead")
    return mask_sensitive(text)


def save(content: str, summary: str = "", tags: list[str] | None = None,
         due: str | None = None, kind: str | None = None) -> dict:
    """Write one short-term entry with its secrets moved out first. Returns
    {name, created, flagged (secret kinds), refs (vault names), vaulted}."""
    from wiki_server import vault
    from wiki_server.store import write_stm_entry

    content, found = protect(content)
    summary, found_summary = protect(summary)
    tags = [protect(t)[0] for t in tags] if tags else tags
    name, created = write_stm_entry(content, summary=summary, tags=tags, due=due, kind=kind)
    refs = list(dict.fromkeys(
        r.split("]")[0] for r in (content + summary).split("[SECRET: ")[1:]))
    return {"name": name, "created": created, "flagged": list(dict.fromkeys(found + found_summary)),
            "refs": refs, "vaulted": vault.enabled()}
