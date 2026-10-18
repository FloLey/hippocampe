"""The three-stage pipeline and the dry-run / execute entry points.

triage clusters and routes short-term memory; decide chooses, per unit, what to
do with the pages it touches; write produces each page's final content. Execute
applies everything in one revertible commit (per-element, cumulative, atomic per
unit); the dry-run stops after decide and reports the plan.
"""

from __future__ import annotations

import datetime
import json
import re

from wiki_server import questions, temporal
from wiki_server.query import fold
from wiki_server.sensitive import find_sensitive
from wiki_server.paths import WikiPathError, resolve_under_root, wiki_root
from wiki_server.store import apply_changes, stm_index_content, write_files

from .config import DREAM_REPORTS_DIR, USAGE_FILE, _read, _stamp, ensure_policy
from .index import _regenerate_index
from .models import _Usage, _stage
from .runner import _guarded
from .usage import read_usage


def _read_stm_entries() -> list[tuple[str, str]]:
    entries_dir = resolve_under_root("short_term/entries")
    if not entries_dir.is_dir():
        return []
    out = []
    for p in sorted(entries_dir.glob("*.md")):
        try:
            out.append((p.name, p.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return out


def _stm_stem(name: str) -> str:
    name = str(name).strip().split("/")[-1]
    return name[:-3] if name.endswith(".md") else name


def _as_list(value) -> list:
    """Normalize a field that should be a list but the model may return as a
    single object or null."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _normalize_page(page) -> str | None:
    """Coerce a decide page path to a ``long_term/<...>.md`` path. The model often
    emits the category-relative form (``people/nina.md``) instead of the full path;
    we map it under long_term/ rather than reject it. Returns None if it cannot be
    a page (not a string, or not a .md path)."""
    if not isinstance(page, str):
        return None
    page = page.strip().strip("'\"").lstrip("/")
    if not page.endswith(".md"):
        return None
    if not page.startswith("long_term/"):
        page = "long_term/" + page
    return page


def _temporal_block() -> str:
    """The active temporal items, one line each with their path, so triage and
    decide can see what is already filed: close a todo that is done, and never
    re-create an item that already exists."""
    lines = []
    for i in temporal.list_items(active_only=True):
        first = i["body"].splitlines()[0] if i["body"] else ""
        lines.append(f"- {i['path']} [{i['meta'].get('type', 'todo')}] "
                     f"(due {i['meta'].get('due', '-')}) {first}")
    return "\n".join(lines) or "(aucun)"


def _questions_block() -> str:
    """The open questions, so decide never asks the same thing twice."""
    lines = [f"- {q['body'].splitlines()[0] if q['body'] else ''}"
             for q in questions.list_questions(open_only=True)]
    return "\n".join(lines) or "(aucune)"


def _decisions(usage: _Usage, policy: str, stm_entries: list[tuple[str, str]], notes: list) -> list[dict]:
    """Stage 1 (triage) + stage 2 (decide per unit). Returns [{unit, decision}]."""
    stm_map = {name: body for name, body in stm_entries}
    stm_block = "\n\n".join(f"### {name}\n{body}" for name, body in stm_entries)
    ltm_index = _read("long_term/index.md") or "(vide)"
    temporal_block = _temporal_block()
    questions_block = _questions_block()
    triage = _stage(usage, "triage",
                    f"<policy>\n{policy}\n</policy>\n\n"
                    f"<long_term_index>\n{ltm_index}\n</long_term_index>\n\n"
                    f"<temporal_actifs>\n{temporal_block}\n</temporal_actifs>\n\n"
                    f"<short_term>\n{stm_block}\n</short_term>")
    if not triage or not isinstance(triage.get("units"), list):
        notes.append("Triage : aucune unité exploitable.")
        return []

    pages_on_disk = _ltm_pages()
    pairs = []
    for unit in triage["units"]:
        if not isinstance(unit, dict):
            continue
        names = [n for n in (unit.get("stm") or []) if isinstance(n, str)]
        unit_stm = "\n\n".join(
            f"### {n}\n{stm_map.get(n) or stm_map.get(n + '.md') or ''}" for n in names
        )
        # The index lists paths relative to long_term/ (people/nina.md), and the
        # model often echoes them that way: normalize instead of dropping them,
        # or decide would see no existing page and promote a duplicate.
        touches = list(dict.fromkeys(
            p for p in (_normalize_page(t) for t in _as_list(unit.get("touches"))) if p))
        unit["touches"] = touches
        pages_block = "\n\n".join(
            f"### {t}\n{_read(t) or '(nouvelle page, vide)'}" for t in touches
        ) or "(aucune page touchée)"
        # Safety net for entity resolution: existing pages whose name (or index
        # description) matches the unit's text, found by code, so a page the
        # triage missed is still seen before a new one is promoted.
        related = _related_pages(unit_stm, pages_on_disk, exclude=set(touches))
        related_block = "\n\n".join(
            f"### {r}\n{_read(r)[:_RELATED_MAX_CHARS]}" for r in related
        ) or "(aucune)"
        decision = _stage(usage, "decide",
                          f"<policy>\n{policy}\n</policy>\n\n"
                          f"<unit intent=\"{unit.get('intent', '')}\">\n{unit_stm}\n</unit>\n\n"
                          f"<touched_pages>\n{pages_block}\n</touched_pages>\n\n"
                          f"<related_pages>\n{related_block}\n</related_pages>\n\n"
                          f"<temporal_actifs>\n{temporal_block}\n</temporal_actifs>\n\n"
                          f"<questions_ouvertes>\n{questions_block}\n</questions_ouvertes>")
        if decision:
            pairs.append({"unit": unit, "decision": decision})
        else:
            notes.append(f"Décision illisible pour : {unit.get('intent', names)}")
    return pairs


# --- entity resolution helpers ---------------------------------------------

# How many code-found related pages decide sees per unit, and how much of each.
_RELATED_MAX = 5
_RELATED_MAX_CHARS = 3000
# A token shared by more pages than this is too generic to identify one (e.g.
# "projet", "voyage", "2026"). Below it, rarer tokens weigh more (1/df).
_RELATED_MAX_DF = 5
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _ltm_pages() -> list[str]:
    """Every long-term page path except the index and the self pages (self is a
    fixed set the triage routes explicitly; it is large and not an entity)."""
    root, ltm = wiki_root(), wiki_root() / "long_term"
    if not ltm.is_dir():
        return []
    out = []
    for p in sorted(ltm.rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        parts = p.relative_to(root).parts
        if rel == "long_term/index.md" or "private" in parts or (len(parts) > 1 and parts[1] == "self"):
            continue
        out.append(rel)
    return out


def _related_pages(text: str, pages: list[str], exclude: set[str] | None = None) -> list[str]:
    """Existing pages likely about the same entities as ``text``: a page scores
    when a distinctive token of its filename (weight 2) or of its index
    description (weight 1) appears as a word in the text. Pure ranking, no model."""
    from .index import _index_descriptions

    exclude = exclude or set()
    words = set(_TOKEN_RE.findall(fold(text)))
    if not words or not pages:
        return []
    descs = _index_descriptions()
    name_toks = {p: {t for t in _TOKEN_RE.findall(fold(p.rsplit("/", 1)[-1][:-3])) if len(t) >= 3}
                 for p in pages}
    desc_toks = {p: {t for t in _TOKEN_RE.findall(fold(descs.get(p, ""))) if len(t) >= 4}
                 for p in pages}
    # Distinctiveness is judged per source: "nina" naming three files is still a
    # good name token, even if a dozen descriptions mention her in passing.
    name_df: dict[str, int] = {}
    desc_df: dict[str, int] = {}
    for p in pages:
        for t in name_toks[p]:
            name_df[t] = name_df.get(t, 0) + 1
        for t in desc_toks[p]:
            desc_df[t] = desc_df.get(t, 0) + 1
    scored = []
    for p in pages:
        if p in exclude:
            continue
        by_name = [t for t in name_toks[p] if t in words and name_df[t] <= _RELATED_MAX_DF]
        by_desc = [t for t in desc_toks[p] - name_toks[p]
                   if t in words and desc_df[t] <= _RELATED_MAX_DF]
        # A description alone is weak evidence: it needs two shared words.
        if by_name or len(by_desc) >= 2:
            score = sum(2 / name_df[t] for t in by_name) + sum(1 / desc_df[t] for t in by_desc)
            scored.append((-score, p))
    return [p for _, p in sorted(scored)[:_RELATED_MAX]]


def _write_page(usage: _Usage, policy: str, op: dict, current: str = "", sources: str = "") -> dict | None:
    """Stage 3: produce {content, description} for one integrate/promote page op.

    ``current`` is the page's content as it stands *at this point in the dream*:
    the caller passes what an earlier unit already produced for this path, so a
    page touched by several units is built cumulatively instead of overwritten.
    ``sources`` are the raw short-term entries behind the change, so details the
    decide summary dropped (dates, numbers, names) are not lost."""
    page = op.get("page", "")
    out = _stage(usage, "write",
                 f"<policy>\n{policy}\n</policy>\n\n"
                 f"<operation>\n{json.dumps(op, ensure_ascii=False)}\n</operation>\n\n"
                 f"<sources>\n{sources or '(aucune)'}\n</sources>\n\n"
                 f"<current_page path=\"{page}\">\n{current or '(nouvelle page)'}\n</current_page>")
    return out if out and isinstance(out.get("content"), str) else None


# An integration must not shrink a page below this share of its current size:
# a much shorter rewrite means content was dropped (or the output truncated).
_MIN_KEEP_RATIO = 0.7


def _lossy_rewrite(current: str, new: str) -> bool:
    return bool(current.strip()) and len(new.strip()) < _MIN_KEEP_RATIO * len(current.strip())


def _format_decisions(pairs: list[dict]) -> str:
    if not pairs:
        return "Aucune décision proposée."
    blocks = []
    for pair in pairs:
        u, d = pair["unit"], pair["decision"]
        stm_names = ", ".join(s for s in (u.get("stm") or []) if isinstance(s, str))
        lines = [f"## {u.get('intent', '(unité)')}", f"- entrées : {stm_names}"]
        pages_ops = _as_list(d.get("pages"))
        temporal_ops = _as_list(d.get("temporal"))
        for op in pages_ops:
            if isinstance(op, dict) and op.get("page"):
                lines.append(f"- {op.get('action', 'write')} {op['page']} : {op.get('change', '')}")
        for t in temporal_ops:
            if isinstance(t, dict) and t.get("content"):
                lines.append(f"- temporal : {t.get('type')} (due {t.get('due')}) : {t.get('content')}")
        done_ops = [x for x in _as_list(d.get("temporal_done")) if isinstance(x, str)]
        for x in done_ops:
            lines.append(f"- clôturer : {x}")
        for q in _as_list(d.get("questions")):
            if isinstance(q, str) and q.strip():
                lines.append(f"- question : {q.strip()}")
        if not pages_ops and not temporal_ops and not done_ops:
            lines.append("- action : garder en court terme")
        if d.get("rationale"):
            lines.append(f"- pourquoi : {d['rationale']}")
        blocks.append("\n".join(lines))

    # Pages touched by more than one unit are built cumulatively at execute time
    # (one merged page), not duplicated. Flag them so the dry-run reflects reality.
    counts: dict[str, int] = {}
    for pair in pairs:
        for op in _as_list(pair["decision"].get("pages")):
            if isinstance(op, dict) and isinstance(op.get("page"), str):
                counts[op["page"]] = counts.get(op["page"], 0) + 1
    merged = sorted(p for p, c in counts.items() if c > 1)
    body = "\n\n".join(blocks)
    if merged:
        body += "\n\n---\n\n" + "\n".join(
            f"- {p} : touchée par {counts[p]} unités, fusionnée en une seule page."
            for p in merged
        )
    return body


def run_dry_run() -> tuple[str, str]:
    """Triage + decide, reported for review. Changes no memory."""
    return _guarded(True, _dry_run)


def _dry_run(when: datetime.datetime, day: str) -> tuple[str, str]:
    policy = ensure_policy()
    stm_entries = _read_stm_entries()
    usage = _Usage()
    notes: list[str] = []
    if not stm_entries:
        body = "Mémoire court terme vide. Rien à consolider."
    else:
        body = _format_decisions(_decisions(usage, policy, stm_entries, notes))
    if notes:
        body += "\n\n---\n\n" + "\n".join(f"- {n}" for n in notes)
    cost_lines = usage.cost_lines()
    if cost_lines:
        body += "\n\n## Coût par étape\n\n" + "\n".join(f"- {c}" for c in cost_lines)

    report = f"# Dream dry-run, {_stamp(when)}\n\n{body}\n\n{_health_section()}"
    rel = f"{DREAM_REPORTS_DIR}/{day}-dryrun.md"
    files = {rel: report}
    entry = usage.entry(when)
    if entry:
        files[USAGE_FILE] = json.dumps(read_usage() + [entry], indent=2)
    write_files(files, f"dream: dry-run report {day}")
    return rel, report


def run_execute() -> tuple[str, str]:
    """Triage + decide + write, applied in one revertible commit; then the exam
    (exam.py) is checked against the new state and added to the report."""
    rel, report = _guarded(False, _execute)
    try:
        from wiki_server import exam

        section, files = exam.report_section(rel.rsplit("/", 1)[-1][:10])
        if section:
            report = report.rstrip("\n") + "\n\n" + section
            write_files({rel: report, **files}, "dream: exam")
    except Exception:
        import logging

        logging.getLogger("wiki_server").exception("exam failed")
    return rel, report


def _execute(when: datetime.datetime, day: str) -> tuple[str, str]:
    policy = ensure_policy()
    stm_entries = _read_stm_entries()
    usage = _Usage()
    notes: list[str] = []
    writes: dict[str, str] = {}
    deletes: list[str] = []

    pairs = _decisions(usage, policy, stm_entries, notes) if stm_entries else []
    if not stm_entries:
        notes.append("Mémoire court terme vide.")

    new_desc: dict[str, str] = {}
    page_paths: set[str] = set()
    consumed: set[str] = set()
    temporal_taken: set[str] = set()
    closed: set[str] = set()
    active_temporal = {i["path"]: i for i in temporal.list_items(active_only=True)}
    stm_map = {name: body for name, body in stm_entries}

    # Pass 1: per unit, collect its valid page targets and create its temporal
    # items (no model call). Page changes are grouped by path so a page touched by
    # several units is written ONCE, not once per unit.
    page_changes: dict[str, list[str]] = {}
    page_sources: dict[str, list[str]] = {}
    unit_pages: list[set[str]] = []
    unit_temporal: list[set[str]] = []
    unit_failed: list[bool] = []

    for pair in pairs:
        d = pair["decision"]
        u_pages: set[str] = set()
        u_temporal: set[str] = set()
        failed = False

        for op in _as_list(d.get("pages")):
            if not isinstance(op, dict):
                continue
            page = _normalize_page(op.get("page"))
            if page is None:
                notes.append(f"Page invalide : {op.get('page')!r}")
                failed = True
                continue
            op["page"] = page  # so the plan section of the report shows the same path
            try:
                resolve_under_root(page)
            except WikiPathError:
                notes.append(f"Page interdite : {page!r}")
                failed = True
                continue
            change_val = op.get("change")
            page_changes.setdefault(page, []).append(str(change_val).strip() if change_val is not None else "")
            srcs = page_sources.setdefault(page, [])
            for n in (pair["unit"].get("stm") or []):
                if isinstance(n, str) and n not in srcs:
                    srcs.append(n)
            u_pages.add(page)

        for t in _as_list(d.get("temporal")):
            if not isinstance(t, dict):
                continue
            content = str(t.get("content", "")).strip()
            if not content:
                continue
            due = t.get("due")
            due = due.strip() if isinstance(due, str) and due.strip().lower() not in ("", "null", "none") else None
            # A temporal item must expire: require a valid ISO due, else it would
            # sit active forever. Without one it is a durable fact, not temporal.
            if not due:
                notes.append(f"Item temporel sans échéance ignoré : {content[:60]}")
                continue
            try:
                datetime.date.fromisoformat(due[:10])
            except ValueError:
                notes.append(f"Item temporel à échéance invalide ignoré : {due!r}")
                continue
            stem = temporal.item_stem(content, due, None, temporal_taken | u_temporal)
            u_temporal.add(stem)
            rel, file_content = temporal.build_item(stem, str(t.get("type", "todo")), due, content)
            writes[rel] = file_content

        for rel in _as_list(d.get("temporal_done")):
            done = _done_change(rel, active_temporal)
            if done is None:
                existing = temporal.resolve_item(rel) if isinstance(rel, str) else None
                if existing:
                    # Already closed (e.g. by an earlier, partly failed run): the
                    # wish is fulfilled, so it must not keep the entry pending.
                    u_temporal.add(existing)
                else:
                    notes.append(f"Clôture ignorée (item temporel actif introuvable) : {rel!r}")
                continue
            writes[done[0]] = done[1]
            closed.add(done[0])
            u_temporal.add(done[0])

        temporal_taken |= {t for t in u_temporal if not t.startswith("temporal/")}
        unit_pages.append(u_pages)
        unit_temporal.append(u_temporal)
        unit_failed.append(failed)

    # Questions instead of guesses: each new one is filed once (by key, so a
    # question already open or answered is never asked again).
    known = questions.known_keys()
    asked: list[str] = []
    q_taken: set[str] = set()

    def ask(text: str, key: str = "", about: str = "") -> None:
        key = key or questions.question_key(text)
        if not text.strip() or key in known:
            return
        known.add(key)
        rel, content = questions.build_question(text, key, about, q_taken)
        q_taken.add(rel.rsplit("/", 1)[-1][:-3])
        writes[rel] = content
        asked.append(rel)

    for pair in pairs:
        stems = ", ".join(n for n in (pair["unit"].get("stm") or []) if isinstance(n, str))
        for q in _as_list(pair["decision"].get("questions")):
            if isinstance(q, str):
                ask(q.strip(), about=stems)
    # Likely duplicate pages found by code become a question too, asked once.
    for group in _duplicate_groups(_ltm_pages()):
        ask("Ces pages parlent-elles du même sujet (à fusionner) ou de sujets distincts ? "
            + ", ".join(group), key="dup:" + "|".join(sorted(group)), about="doublons probables")
    # A close person (most linked) with no birthday on file: one gentle
    # question per night, never twice for the same person.
    try:
        from wiki_server import people

        for rel in people.missing_birthdays(3):
            if f"bday:{rel}" not in known:
                ask(f"Quelle est la date d'anniversaire de {_title_of(rel)} ({rel}) ? "
                    "Et une idée de cadeau, si tu en as une.", key=f"bday:{rel}", about="fiche")
                break
    except Exception:
        pass
    # A project whose status has not moved in months: ask if it still holds,
    # a couple per night, again next quarter if it is still stale.
    today = datetime.date.today()
    quarter = f"{today.year}Q{(today.month - 1) // 3 + 1}"
    for rel, day in _stale_pages(today)[:_STALE_PER_DREAM]:
        months = max(1, (today - datetime.date.fromisoformat(day)).days // 30)
        ask(f"« {_title_of(rel)} » ({rel}) n'a pas bougé depuis {months} mois : "
            "est-ce toujours d'actualité, terminé, en pause ou abandonné ?",
            key=f"stale:{rel}:{quarter}", about="fraîcheur")

    # Pass 2: one write per unique page, merging every contributing unit's change.
    page_ok: set[str] = set()
    for path, changes in page_changes.items():
        current = _read(path)
        valid = [c for c in changes if c]
        merged = valid[0] if len(valid) == 1 else "\n".join(f"- {c}" for c in valid)
        op = {
            "action": "integrate" if current else "promote",
            "page": path,
            "change": merged,
        }
        sources = "\n\n".join(
            f"### {n}\n{stm_map.get(n) or stm_map.get(n + '.md') or ''}"
            for n in page_sources.get(path, []))
        written = _write_page(usage, policy, op, current, sources=sources)
        if not written:
            notes.append(f"Écriture échouée pour {path}.")
            continue
        if _lossy_rewrite(current, written["content"]):
            notes.append(f"Réécriture refusée pour {path} : la page aurait perdu du contenu "
                         f"({len(written['content'])} < {len(current)} caractères).")
            continue
        writes[path] = written["content"]
        page_ok.add(path)
        flagged = find_sensitive(written["content"])
        if flagged:
            notes.append(f"Attention, {path} contient un secret probable ({', '.join(flagged)}).")
        if isinstance(written.get("description"), str) and written["description"].strip():
            new_desc[path] = written["description"].strip()
    page_paths |= page_ok

    # Consume a unit's short-term entries only if it produced something, nothing
    # was invalid/forbidden, and every page it touched was written. Otherwise keep
    # it for the next dream, so the missing part is never lost.
    for i, pair in enumerate(pairs):
        u = pair["unit"]
        stm_stems = {_stm_stem(n) for n in (u.get("stm") or []) if isinstance(n, str)}
        produced = bool(unit_pages[i] or unit_temporal[i])
        pages_ok = unit_pages[i] <= page_ok
        kept = ", ".join(n for n in (u.get("stm") or []) if isinstance(n, str))
        if produced and not unit_failed[i] and pages_ok:
            consumed |= stm_stems
        elif produced or unit_failed[i]:
            notes.append(f"Gardé en court terme (un élément a échoué) : {kept}")
        else:
            notes.append(f"Gardé en court terme : {kept}")

    if page_paths or new_desc:
        writes["long_term/index.md"] = _regenerate_index(new_desc, page_paths)
        notes.append("Index régénéré.")

    if consumed:
        for stem in consumed:
            entry_rel = f"short_term/entries/{stem}.md"
            if resolve_under_root(entry_rel).is_file():
                deletes.append(entry_rel)
        writes["short_term/index.md"] = stm_index_content(exclude_stems=consumed)
        notes.append(f"Court terme consommé : {', '.join(sorted(consumed))}")

    expirations = {k: v for k, v in temporal.expire_changes(day).items() if k not in closed}
    writes.update(expirations)
    if expirations:
        notes.append(f"{len(expirations)} item(s) temporal expiré(s).")

    # Deduped summary of what landed (one line per unique page / item), prefixed
    # by the stage 1-2 plan so the execute report also shows the reasoning.
    applied: list[str] = []
    if page_paths:
        applied.append(f"{len(page_paths)} page(s) écrite(s) : " + ", ".join(sorted(page_paths)))
    temporal_made = sorted(f"temporal/{s}.md" for s in temporal_taken)
    if temporal_made:
        applied.append(f"{len(temporal_made)} item(s) temporel(s) : " + ", ".join(temporal_made))
    if closed:
        applied.append(f"{len(closed)} item(s) temporel(s) clôturé(s) : " + ", ".join(sorted(closed)))
    if asked:
        applied.append(f"{len(asked)} question(s) à poser : " + ", ".join(asked))
    remaining = sorted({_stm_stem(name) for name, _ in stm_entries} - consumed)
    if remaining:
        applied.append(f"{len(remaining)} entrée(s) restée(s) en court terme : " + ", ".join(remaining))
    applied.extend(notes)
    plan = _format_decisions(pairs) if pairs else "Aucune décision."
    cost_lines = usage.cost_lines()
    cost = ("## Coût par étape\n\n" + "\n".join(f"- {c}" for c in cost_lines) + "\n\n") if cost_lines else ""
    report = (
        f"# Dream, {_stamp(when)}\n\n## Plan (étapes 1-2)\n\n{plan}\n\n"
        f"## Appliqué (étape 3)\n\n" + "\n".join(f"- {n}" for n in applied) + "\n\n"
        + cost
        # Measured before this run's changes land (the run is one commit).
        + _health_section().replace("## Santé de la mémoire",
                                    "## Santé de la mémoire (avant ce rêve)", 1)
    )
    rel = f"{DREAM_REPORTS_DIR}/{day}.md"
    writes[rel] = report
    entry = usage.entry(when)
    if entry:
        writes[USAGE_FILE] = json.dumps(read_usage() + [entry], indent=2)
    apply_changes(writes, deletes, f"dream: {day} consolidation")
    return rel, report


_STALE_DAYS = 120
_STALE_PER_DREAM = 2
_STATUS_RE = re.compile(r"\b(en cours|prevue?s?|envisagee?s?|a faire|en attente|en pause|a venir|"
                        r"in progress|planned|statut|status|idee)\b")


def _title_of(rel: str) -> str:
    for line in _read(rel).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return rel.rsplit("/", 1)[-1][:-3]


def _stale_pages(today: datetime.date) -> list[tuple[str, str]]:
    """Project pages that state a status (in progress, planned…) and have not
    changed in ``_STALE_DAYS``, oldest first, with their last change date."""
    from wiki_server.store import last_changed

    dates = last_changed()
    out = []
    for rel in _ltm_pages():
        if not rel.startswith("long_term/projects/") or rel not in dates:
            continue
        try:
            age = (today - datetime.date.fromisoformat(dates[rel])).days
        except ValueError:
            continue
        if age >= _STALE_DAYS and _STATUS_RE.search(fold(_read(rel))):
            out.append((rel, dates[rel]))
    return sorted(out, key=lambda x: x[1])


def _open_duplicate_groups(pages: list[str]) -> list[list[str]]:
    """Likely duplicates the user has not already ruled on."""
    answered = questions.answered_keys()
    return [g for g in _duplicate_groups(pages) if "dup:" + "|".join(sorted(g)) not in answered]


def _duplicate_groups(pages: list[str]) -> list[list[str]]:
    """Likely duplicates to merge by hand, by filename alone (no model): a page
    whose name words are all contained in another page's name in the same
    category (people/nina.md vs people/nina-durand.md), or the same name in two
    categories (concepts/levain-f2.md vs projects/levain-f2.md). Some are a
    legitimate general/specific pair; the report says "probables" for that."""
    info = []
    for p in pages:
        parts = p.split("/")
        stem = parts[-1][:-3]
        info.append((p, parts[1] if len(parts) > 2 else "", stem,
                     frozenset(_TOKEN_RE.findall(fold(stem)))))
    groups: dict[str, set[str]] = {}
    for p, cat, stem, toks in info:
        for q, qcat, qstem, qtoks in info:
            if q == p or not toks:
                continue
            same_name = stem == qstem and cat != qcat
            contained = cat == qcat and toks < qtoks
            if same_name or contained:
                groups.setdefault(min(p, q) if same_name else p, {p}).add(q)
    unique = {tuple(sorted(g)) for g in groups.values()}
    # Keep only maximal groups (a pair inside a larger group adds nothing).
    return [list(g) for g in sorted(unique) if not any(set(g) < set(o) for o in unique)]


def _health_section() -> str:
    """Code-computed health of the memory, appended to every report: what the
    next manual clean-up should look at. Costs no model call."""
    from wiki_server.query import build_prime

    lines = []
    prime_chars = len(build_prime())
    lines.append(f"- Taille de prime : {prime_chars:,} caractères (~{prime_chars // 3:,} tokens), "
                 "chargés au début de chaque conversation.")
    groups = _open_duplicate_groups(_ltm_pages())
    if groups:
        lines.append(f"- Doublons probables ({len(groups)}) :")
        lines.extend(f"  - {', '.join(g)}" for g in groups)
    today = datetime.date.today()
    stale = _stale_pages(today)
    if stale:
        lines.append(f"- Projets sans nouvelles depuis {_STALE_DAYS} jours ou plus ({len(stale)}) : "
                     + ", ".join(rel for rel, _ in stale))
    overdue = [i["path"] for i in temporal.list_items(active_only=True)
               if temporal.surface_state(i["meta"], today) == (True, True)]
    if overdue:
        lines.append(f"- Items en retard non clôturés ({len(overdue)}) : " + ", ".join(overdue))
    for rel in ("long_term/self/identity.md", "long_term/self/style.md", "long_term/self/voices.md"):
        flagged = find_sensitive(_read(rel))
        if flagged:
            lines.append(f"- Secret probable dans {rel}, chargé à chaque conversation : "
                         + ", ".join(flagged))
    return "## Santé de la mémoire\n\n" + "\n".join(lines) + "\n"


def _done_change(rel, active: dict[str, dict]) -> tuple[str, str] | None:
    """(path, new content) marking an active temporal item done, or None if
    ``rel`` is not an active item (unknown, malformed, already closed)."""
    rel = temporal.resolve_item(rel) if isinstance(rel, str) else None
    item = active.get(rel) if rel else None
    if item is None:
        return None
    return rel, temporal.render_done(item["meta"], item["body"])


def list_reports() -> list[str]:
    reports_dir = resolve_under_root(DREAM_REPORTS_DIR)
    if not reports_dir.is_dir():
        return []
    return [p.relative_to(wiki_root()).as_posix()
            for p in sorted(reports_dir.glob("*.md"), reverse=True)]
