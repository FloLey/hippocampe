"""The link graph of the memory: pages as nodes, markdown links as edges.

Backs the console's graph view (an Obsidian-like map of the wiki) and the
backlinks shown under each page. Pure reads, no model: the graph is rebuilt on
each request, which is cheap for a personal wiki of a few hundred pages, so it
can never drift from the files.
"""

from __future__ import annotations

import posixpath
import re

from wiki_server.paths import wiki_root
from wiki_server.store import parse_frontmatter

# [text](target) with an optional "title"; images (![..](..)) are skipped below.
_LINK_RE = re.compile(r"(!?)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_INDEX_LINE_RE = re.compile(r"\s*-\s*\[[^\]]*\]\(([^)]+)\)\s*:?\s*(.*)")


def _title(body: str, stem: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or stem
    return stem


def _descriptions(root) -> dict[str, str]:
    """Index descriptions by full page path (the index links are relative to
    long_term/)."""
    path = root / "long_term" / "index.md"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _INDEX_LINE_RE.match(line)
        if m:
            target = m.group(1).strip()
            full = target if target.startswith("long_term/") else f"long_term/{target}"
            out[full] = m.group(2).strip()
    return out


def resolve_link(source: str, target: str) -> str | None:
    """The wiki path a markdown link in ``source`` points to, or None when it is
    not a link to a markdown file in the wiki (external URL, mail, anchor only,
    image, escape above the root). ``../people/nina.md`` from
    ``long_term/projects/x.md`` resolves to ``long_term/people/nina.md``."""
    target = target.strip()
    if not target or target.startswith("#") or _SCHEME_RE.match(target):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target.endswith(".md"):
        return None
    if target.startswith("/"):
        resolved = posixpath.normpath(target.lstrip("/"))
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    if resolved.startswith("..") or resolved == ".":
        return None
    return resolved


def _pages(root, include_temporal: bool) -> list[str]:
    """Graph members: long-term pages (not the index, never private) and,
    optionally, temporal items."""
    out = []
    ltm = root / "long_term"
    if ltm.is_dir():
        for p in sorted(ltm.rglob("*.md")):
            rel = p.relative_to(root).as_posix()
            if rel == "long_term/index.md" or "private" in p.relative_to(root).parts:
                continue
            out.append(rel)
    if include_temporal and (root / "temporal").is_dir():
        out.extend(p.relative_to(root).as_posix() for p in sorted((root / "temporal").glob("*.md")))
    return out


def _category(rel: str) -> str:
    parts = rel.split("/")
    if parts[0] == "long_term" and len(parts) > 2:
        return parts[1]
    return parts[0]


def build_graph(include_temporal: bool = False) -> dict:
    """{nodes, edges, broken, orphans, duplicates} for the whole wiki.

    - nodes: {id, label, category, degree, backlinks, description}
    - edges: {source, target}, deduplicated, self-links dropped
    - broken: {source, target} links to a wiki .md that does not exist
    - orphans: node ids with no link in or out
    - duplicates: likely-duplicate groups (same heuristic as the dream report)
    """
    root = wiki_root()
    pages = _pages(root, include_temporal)
    members = set(pages)
    descs = _descriptions(root)
    titles: dict[str, str] = {}
    edges: set[tuple[str, str]] = set()
    broken: list[dict] = []
    for rel in pages:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        _meta, body = parse_frontmatter(text)
        titles[rel] = _title(body, rel.rsplit("/", 1)[-1][:-3])
        for m in _LINK_RE.finditer(body):
            if m.group(1):  # an image, not a page link
                continue
            target = resolve_link(rel, m.group(2))
            if target is None or target == rel:
                continue
            if target in members:
                edges.add((rel, target))
            elif target.startswith("long_term/") and not (root / target).is_file():
                broken.append({"source": rel, "target": target})

    from wiki_server.media import photos_of

    out_deg = {p: 0 for p in pages}
    in_deg = {p: 0 for p in pages}
    for s, t in edges:
        out_deg[s] += 1
        in_deg[t] += 1
    nodes = [{
        "id": p, "label": titles[p], "category": _category(p),
        "degree": out_deg[p] + in_deg[p], "backlinks": in_deg[p],
        "description": descs.get(p, ""),
        "photo": next(iter(photos_of(p)), ""),
    } for p in pages]

    from wiki_server.dream.pipeline import _ltm_pages, _open_duplicate_groups

    return {
        "nodes": nodes,
        "edges": [{"source": s, "target": t} for s, t in sorted(edges)],
        "broken": broken,
        "orphans": [p for p in pages if out_deg[p] + in_deg[p] == 0],
        "duplicates": _open_duplicate_groups(_ltm_pages()),
    }


def backlinks(rel: str) -> list[tuple[str, str]]:
    """(path, title) of the pages linking to ``rel``, sorted by path."""
    graph = build_graph(include_temporal=rel.startswith("temporal/"))
    titles = {n["id"]: n["label"] for n in graph["nodes"]}
    return sorted((e["source"], titles.get(e["source"], e["source"]))
                  for e in graph["edges"] if e["target"] == rel)
