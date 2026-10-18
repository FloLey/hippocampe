"""Mémoire: the pages by category, people as cards, the graph, one page with
its actions, the editor, and the raw file browser."""

from __future__ import annotations

import subprocess
from urllib.parse import quote

from markdown_it import MarkdownIt
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse

from wiki_server.graph import backlinks, build_graph
from wiki_server.paths import WikiPathError, resolve_under_root, wiki_root
from wiki_server.store import delete_file, parse_frontmatter, write_file
from wiki_server.ui import parts
from wiki_server.ui.graph_assets import D3_URL, GRAPH_BODY, GRAPH_SCRIPT
from wiki_server.ui.layout import (
    CATEGORIES, CATEGORY_LABEL, ago, button, card, chip, empty, esc, fmt_date, head, page_url,
    day_month, plural, post_button, pretty_name, row, rows, tabs, until,
)

_md = MarkdownIt("commonmark").enable("table")
TABS = [("/ui/memoire", "Thèmes"), ("/ui/graphe", "Graphe")]


def _tabs(active: str) -> str:
    return tabs(TABS, active)


def _ltm_nodes() -> list[dict]:
    return [n for n in build_graph()["nodes"] if n["id"].startswith("long_term/")
            and not n["id"].endswith("/index.md")]


def _last_change(rel: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(wiki_root()), "log", "-1", "--format=%cs", "--", rel],
                              check=True, capture_output=True, text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


# ---- category views ----

def _overview_body() -> str:
    nodes = _ltm_nodes()
    counts: dict[str, int] = {}
    for n in nodes:
        counts[n["category"]] = counts.get(n["category"], 0) + 1
    tiles = []
    for cat, label, desc in CATEGORIES:
        if not counts.get(cat):
            continue
        tiles.append(f'<a class="tile" href="/ui/memoire/{cat}"><span class="n">{counts[cat]}</span>'
                     f"<b>{esc(label)}</b><small>{esc(desc)}</small></a>")
    waiting = len(parts.stm_entries())
    tiles.append(f'<a class="tile" href="/ui/memoire/attente"><span class="n">{waiting}</span>'
                 "<b>En attente</b><small>Notes pas encore rangées par le rêve</small></a>")
    day = parts.today()
    return (head("Mémoire", f"{plural(len(nodes), 'page')} rangées par thème.",
                 button("Nouvelle page", "/ui/nouvelle", "secondary"))
            + _tabs("/ui/memoire") + f'<div class="tiles">{"".join(tiles)}</div>'
            + card("Modifié récemment", parts.page_rows(parts.recent_pages(8), day)))


def _people_body() -> str:
    from wiki_server import media, people

    day = parts.today()
    cards = [c for c in people.cards() if "/people/" in c["path"]]
    items = []
    for c in sorted(cards, key=lambda c: c["name"].lower()):
        photo = next(iter(media.photos_of(c["path"])), "")
        top = (f'<img src="{esc(page_url(photo))}" alt="" loading="lazy">' if photo else
               f'<div class="avatar"><span>{esc(c["name"][:1].upper())}</span></div>')
        lines = []
        if c["birthday"]:
            when, age = people.next_birthday(c["birthday"], day)
            delta = (when - day).days
            soon = f" · dans {delta} j" if delta <= 30 else ""
            d, m, _y = c["birthday"]
            lines.append(f"🎂 {day_month(d, m)}"
                         + (f" · {age} ans" if age else "") + soon)
        if c["gifts"]:
            lines.append("🎁 " + parts.clip(parts.gift_text(c["gifts"][0]), 70))
        if c["likes"]:
            lines.append("❤️ " + parts.clip(c["likes"][0], 70))
        info = "".join(f"<small>{esc(x)}</small>" for x in lines) or "<small>Rien de noté encore.</small>"
        items.append(f'<a class="person" href="{esc(page_url(c["path"]))}">{top}'
                     f'<div class="body"><b>{esc(c["name"])}</b>{info}</div></a>')
    return (head("Personnes", f"{plural(len(cards), 'personne')}. Anniversaires, idées cadeaux et goûts "
                 "viennent de leur page : dis-les simplement en conversation.",
                 button("Ajouter", "/ui/nouvelle?cat=people", "secondary"),
                 crumb='<a href="/ui/memoire">Mémoire</a>')
            + (f'<div class="people">{"".join(items)}</div>' if items else card("", empty("Personne encore."))))


def _waiting_body() -> str:
    day = parts.today()
    entries = parts.stm_entries()
    items = [row(esc(e["text"][:140] or pretty_name(e["path"])), f"noté le {esc(fmt_date(e['created'], day))}",
                 page_url(e["path"]), lead="📝") for e in entries]
    return (head("En attente du rêve", "Les notes prises en conversation. Chaque nuit, le rêve les range "
                 "dans les bonnes pages, puis elles disparaissent d'ici.", crumb='<a href="/ui/memoire">Mémoire</a>')
            + card("", rows(items) if items else empty("Tout est rangé.")))


def _category_body(cat: str) -> str:
    from wiki_server.store import last_changed

    day = parts.today()
    changed = last_changed()
    nodes = sorted((n for n in _ltm_nodes() if n["category"] == cat), key=lambda n: n["label"].lower())
    items = [row(esc(n["label"]), esc(n.get("description", "")[:160]), page_url(n["id"]),
                 f"<small>{esc(ago(changed.get(n['id'], ''), day))}</small>") for n in nodes]
    desc = next((d for c, _l, d in CATEGORIES if c == cat), "")
    return (head(CATEGORY_LABEL.get(cat, cat.capitalize()), f"{plural(len(nodes), 'page')} · {esc(desc)}",
                 button("Nouvelle page", f"/ui/nouvelle?cat={quote(cat)}", "secondary"),
                 crumb='<a href="/ui/memoire">Mémoire</a>')
            + card("", rows(items) if items else empty("Aucune page ici.")))


# ---- one page ----

def _where(rel: str) -> tuple[str, str]:
    """(section of the nav, breadcrumb html) for a file."""
    parts_ = rel.split("/")
    if rel.startswith("long_term/") and len(parts_) >= 3:
        cat = parts_[1]
        return "memory", (f'<a href="/ui/memoire">Mémoire</a> › '
                          f'<a href="/ui/memoire/{esc(cat)}">{esc(CATEGORY_LABEL.get(cat, cat))}</a>')
    if rel.startswith("short_term/"):
        return "memory", '<a href="/ui/memoire">Mémoire</a> › <a href="/ui/memoire/attente">En attente</a>'
    if rel.startswith("temporal/"):
        return "time", '<a href="/ui/temps">Temps</a>'
    if rel.startswith("questions/"):
        return "home", '<a href="/ui/questions">Questions</a>'
    if rel.startswith("dream_reports/"):
        return "settings", '<a href="/ui/reglages">Réglages</a> › <a href="/ui/reglages/reve">Rêve</a>'
    if rel.startswith(("prompts/", "exam/")) or rel == "DREAM.md":
        return "settings", '<a href="/ui/reglages">Réglages</a> › <a href="/ui/reglages/avance">Avancé</a>'
    folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    return "memory", f'<a href="/ui/fichiers?dir={quote(folder)}">Fichiers</a>'


def _split_title(rel: str, body: str) -> tuple[str, str]:
    """(title, body without its leading "# " heading)."""
    lines = body.lstrip("\n").split("\n")
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip(), "\n".join(lines[1:])
    if rel.startswith(("temporal/", "questions/", "short_term/")):
        return (parts.first_line(body)[:120] or pretty_name(rel)), body
    return pretty_name(rel), body


def _page_view(rel: str, raw: str, csrf: str) -> tuple[str, str, str]:
    """(title, body html, nav section)."""
    meta, body = parse_frontmatter(raw)
    if not meta:
        body = raw
    title, rest = _split_title(rel, body)
    section, crumb = _where(rel)
    day = parts.today()

    chips = []
    if rel.startswith("long_term/"):
        cat = rel.split("/")[1]
        chips.append(chip(CATEGORY_LABEL.get(cat, cat), "accent"))
    if rel.startswith("temporal/"):
        kind = meta.get("type", "todo")
        chips.append(chip(parts.KIND_LABEL.get(kind, kind), "accent"))
        status = meta.get("status", "active")
        if status == "active":
            label, tone = until(meta.get("due", ""), day)
            if label:
                chips.append(chip(label, tone))
        else:
            chips.append(chip({"done": "fait ✓", "expired": "expiré"}.get(status, status),
                              "ok" if status == "done" else ""))
    if rel.startswith("questions/"):
        chips.append(chip("ouverte" if meta.get("status", "open") == "open" else "répondue",
                          "soon" if meta.get("status", "open") == "open" else "ok"))
    changed = _last_change(rel)
    if changed:
        chips.append(f"<small>mise à jour {esc(ago(changed, day))}</small>")

    actions = []
    if rel.endswith(".md"):
        actions.append(button("Modifier", f"/ui/modifier?path={quote(rel)}", "primary"))
    if rel.startswith("temporal/") and meta.get("status", "active") == "active" and meta.get("type") != "event":
        actions.append(post_button(csrf, "/ui/temporal/done", "Marquer fait",
                                         {"path": rel, "back": page_url(rel)}, kind="secondary"))
    folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    menu = (
        '<details class="menu"><summary class="btn quiet" aria-label="Plus d\'actions">⋯</summary>'
        '<div class="menu-panel">'
        "<button class='link' type='button' onclick=\"navigator.clipboard.writeText("
        "document.getElementById('raw-md').value).then(() => this.textContent = 'Copié ✓')\">"
        "Copier le texte</button>"
        f'<a href="/ui/fichiers?dir={quote(folder)}">Voir le dossier</a>'
        "<hr>"
        '<form method="post" action="/ui/delete" onsubmit="return confirm(\'Supprimer cette page ? '
        'Elle reste récupérable dans l’historique.\')">'
        f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
        f'<input type="hidden" name="path" value="{esc(rel)}">'
        '<button class="link danger" type="submit">Supprimer</button></form>'
        "</div></details>")
    actions.append(menu)

    out = [f'<header class="page-head"><div class="crumb">{crumb}</div><div class="head-row">'
           f"<div><h1>{esc(title)}</h1><div class=\"meta\">{''.join(chips)}</div></div>"
           f'<div class="head-actions">{"".join(actions)}</div></div></header>']
    rendered = _md.render(rest).strip()
    out.append(f"<article>{rendered}</article>" if rendered else card("", empty("Page vide.")))

    if rel.startswith("questions/") and meta.get("status", "open") == "open":
        q = {"path": rel, "meta": meta, "body": body}
        out.append(card("Ta réponse", f'<ul class="rows">{parts.question_block(q, csrf, "/ui/questions", True)}</ul>'))

    if rel.startswith(("long_term/", "temporal/")):
        links_in = backlinks(rel)
        if links_in:
            items = [row(esc(t), esc(CATEGORY_LABEL.get(src.split("/")[1], "")) if src.startswith("long_term/") else "",
                         page_url(src)) for src, t in links_in]
            out.append(card(f"Liée à {plural(len(links_in), 'page')}", rows(items)))

    if rel.startswith("long_term/") and rel.endswith(".md"):
        out.append(card("", (
            "<details><summary>Ajouter une photo</summary>"
            '<form method="post" action="/ui/media/upload" enctype="multipart/form-data" style="margin-top:.75rem">'
            f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
            f'<input type="hidden" name="page" value="{esc(rel)}">'
            '<div class="field"><input type="file" name="photo" accept="image/*" required></div>'
            '<div class="field"><label>Légende (facultatif)</label><input type="text" name="caption"></div>'
            '<p class="muted">La photo est réduite et sa position GPS retirée.</p>'
            '<button class="btn primary" type="submit">Ajouter</button></form></details>')))
    out.append(f'<textarea id="raw-md" hidden>{esc(raw)}</textarea>')
    return title, "".join(out), section


def _files_body(rel: str) -> str:
    from wiki_server.query import browse

    subdirs, files = browse(rel)
    crumbs = ['<a href="/ui/fichiers">Fichiers</a>']
    acc = ""
    for seg in rel.split("/") if rel else []:
        acc = f"{acc}/{seg}" if acc else seg
        crumbs.append(f'<a href="/ui/fichiers?dir={quote(acc)}">{esc(seg)}</a>')
    items = [row(esc(name), plural(count, "page"), f"/ui/fichiers?dir={quote(cr)}", lead="📁")
             for name, cr, count in subdirs]
    items += [row(esc(name), "", page_url(cr), lead="📄") for name, cr in files]
    return (head("Fichiers" if not rel else rel.rsplit("/", 1)[-1],
                 "La mémoire telle qu'elle est stockée, dossier par dossier." if not rel else "",
                 crumb=" / ".join(crumbs) if rel else '<a href="/ui/reglages/avance">Avancé</a>')
            + card("", rows(items) if items else empty("Dossier vide.")))


def register(mcp, ctx) -> None:
    @ctx.get(mcp, "/ui/memoire")
    async def ui_memory(request: Request, login: str):
        body = await run_in_threadpool(_overview_body)
        return ctx.render(request, login, "Mémoire", body, section="memory")

    @ctx.get(mcp, "/ui/memoire/{cat}")
    async def ui_category(request: Request, login: str):
        cat = request.path_params["cat"]
        if cat == "people":
            body = await run_in_threadpool(_people_body)
            return ctx.render(request, login, "Personnes", body, section="memory")
        if cat == "attente":
            body = await run_in_threadpool(_waiting_body)
            return ctx.render(request, login, "En attente", body, section="memory")
        if cat not in CATEGORY_LABEL:
            return PlainTextResponse("Catégorie inconnue.", status_code=404)
        body = await run_in_threadpool(_category_body, cat)
        return ctx.render(request, login, CATEGORY_LABEL[cat], body, section="memory")

    @ctx.get(mcp, "/ui/graphe")
    async def ui_graph(request: Request, login: str):
        body = (head("Mémoire", "Chaque point est une page, chaque trait un lien. Survole pour voir les "
                     "voisines, touche pour ouvrir, Entrée dans la recherche pour centrer.")
                + _tabs("/ui/graphe") + GRAPH_BODY
                + f'<script src="{D3_URL}"></script><script>{GRAPH_SCRIPT}</script>')
        return ctx.render(request, login, "Graphe", body, section="memory", wide=True)

    @mcp.custom_route("/ui/graph.json", methods=["GET"])
    async def ui_graph_json(request: Request):
        if not ctx.login(request):
            return PlainTextResponse("Non connecté.", status_code=401)
        include_temporal = request.query_params.get("temporal") == "1"
        return JSONResponse(await run_in_threadpool(build_graph, include_temporal))

    @ctx.get(mcp, "/ui/page/{path:path}")
    async def ui_view(request: Request, login: str):
        from wiki_server.media import IMAGE_TYPES

        rel = request.path_params["path"]
        try:
            path = resolve_under_root(rel)
        except WikiPathError:
            return PlainTextResponse("Interdit.", status_code=403)
        if not path.is_file():
            return ctx.render(request, login, "Introuvable", head("Page introuvable",
                              "Elle a peut-être été renommée ou fusionnée par le rêve.")
                              + card("", '<a href="/ui/recherche">Chercher dans la mémoire</a>'),
                              section="memory")
        if path.suffix.lower() in IMAGE_TYPES:
            return FileResponse(path, media_type=IMAGE_TYPES[path.suffix.lower()],
                                headers={"Cache-Control": "private, max-age=86400"})
        raw = path.read_text(encoding="utf-8")
        title, body, section = await run_in_threadpool(_page_view, rel, raw, ctx.csrf(login))
        return ctx.render(request, login, title, body, section=section)

    @ctx.get(mcp, "/ui/modifier")
    async def ui_edit(request: Request, login: str):
        rel = (request.query_params.get("path") or "").strip()
        if not rel:
            return RedirectResponse("/ui/nouvelle")
        try:
            path = resolve_under_root(rel)
        except WikiPathError:
            return PlainTextResponse("Interdit.", status_code=403)
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        section, crumb = _where(rel)
        title = _split_title(rel, parse_frontmatter(content)[1] if content.startswith("---") else content)[0]
        back = page_url(rel) if path.is_file() else "/ui/memoire"
        body = (head(f"Modifier « {title} »", f"<code>{esc(rel)}</code>", crumb=crumb)
                + '<form method="post" action="/ui/save">'
                f'<input type="hidden" name="csrf" value="{esc(ctx.csrf(login))}">'
                f'<input type="hidden" name="path" value="{esc(rel)}">'
                f'<textarea class="editor" name="content" aria-label="Contenu">{esc(content)}</textarea>'
                '<p class="muted">En markdown. Un lien vers une autre page : '
                "<code>[Camille](../people/camille.md)</code>. Chaque enregistrement est gardé dans "
                "l'historique, rien n'est perdu.</p>"
                '<div class="actions"><button class="btn primary" type="submit">Enregistrer</button>'
                f'<a class="btn quiet" href="{esc(back)}">Annuler</a></div></form>')
        return ctx.render(request, login, f"Modifier {title}", body, section=section)

    @ctx.get(mcp, "/ui/nouvelle")
    async def ui_new(request: Request, login: str):
        chosen = request.query_params.get("cat", "")
        opts = "".join(f'<option value="{c}"{" selected" if c == chosen else ""}>{esc(l)}</option>'
                       for c, l, _d in CATEGORIES if c != "entities")
        body = (head("Nouvelle page", "Le plus souvent, inutile : dis-le en conversation et le rêve crée "
                     "la page. Utile pour écrire toi-même une page complète.",
                     crumb='<a href="/ui/memoire">Mémoire</a>')
                + card("", '<form method="post" action="/ui/nouvelle">'
                       f'<input type="hidden" name="csrf" value="{esc(ctx.csrf(login))}">'
                       f'<div class="grid2"><div class="field"><label for="n-cat">Thème</label>'
                       f'<select id="n-cat" name="cat">{opts}</select></div>'
                       '<div class="field"><label for="n-title">Titre</label>'
                       '<input type="text" id="n-title" name="title" required placeholder="Ex. Camille Martin"></div></div>'
                       '<div class="actions"><button class="btn primary" type="submit">Créer et écrire</button>'
                       '<a class="btn quiet" href="/ui/memoire">Annuler</a></div></form>'))
        return ctx.render(request, login, "Nouvelle page", body, section="memory")

    @ctx.action(mcp, "/ui/nouvelle")
    async def ui_new_create(request: Request, login: str, form):
        from wiki_server.store import slugify

        cat = str(form.get("cat") or "")
        title = str(form.get("title") or "").strip()
        if cat not in CATEGORY_LABEL or not title:
            return ctx.done("/ui/nouvelle", "Choisis un thème et un titre.")
        rel = f"long_term/{cat}/{slugify(title)}.md"
        if resolve_under_root(rel).is_file():
            return ctx.done(page_url(rel), "Cette page existe déjà : la voici.")
        write_file(rel, f"# {title}\n\n", f"manual: new page {rel}")
        return RedirectResponse(f"/ui/modifier?path={quote(rel)}", status_code=303)

    @ctx.action(mcp, "/ui/save")
    async def ui_save(request: Request, login: str, form):
        rel = str(form.get("path") or "").strip()
        content = str(form.get("content") or "")
        if not rel.endswith(".md"):
            return PlainTextResponse("Seuls les fichiers .md se modifient ici.", status_code=400)
        try:
            write_file(rel, content.replace("\r\n", "\n"), f"manual: edit {rel}")
        except WikiPathError:
            return PlainTextResponse("Interdit.", status_code=403)
        return ctx.done(page_url(rel), "Enregistré ✓")

    @ctx.action(mcp, "/ui/delete")
    async def ui_delete(request: Request, login: str, form):
        rel = str(form.get("path") or "").strip()
        try:
            delete_file(rel, f"manual: delete {rel}")
        except WikiPathError:
            return PlainTextResponse("Interdit.", status_code=403)
        parts_ = rel.split("/")
        back = f"/ui/memoire/{parts_[1]}" if rel.startswith("long_term/") and len(parts_) >= 3 else "/ui/memoire"
        return ctx.done(back, "Page supprimée. Elle reste récupérable dans l'historique.")

    @ctx.action(mcp, "/ui/media/upload")
    async def ui_media_upload(request: Request, login: str, form):
        from wiki_server import media

        rel = str(form.get("page") or "")
        upload = form.get("photo")
        if not hasattr(upload, "read"):
            return PlainTextResponse("Aucune photo.", status_code=400)
        data = await upload.read()
        try:
            await run_in_threadpool(media.add_photo, rel, data, str(form.get("caption") or "").strip())
        except (media.MediaError, WikiPathError) as exc:
            return ctx.done(page_url(rel), f"Photo refusée : {exc}")
        return ctx.done(page_url(rel), "Photo ajoutée ✓")

    @ctx.get(mcp, "/ui/fichiers")
    async def ui_files(request: Request, login: str):
        rel = "/".join(s for s in (request.query_params.get("dir") or "").split("/") if s)
        try:
            body = await run_in_threadpool(_files_body, rel)
        except WikiPathError:
            return PlainTextResponse("Interdit.", status_code=403)
        return ctx.render(request, login, "Fichiers", body, section="settings")

    # Old addresses, kept working (bookmarks, links in calendar notes).
    @mcp.custom_route("/ui/edit", methods=["GET"])
    async def ui_edit_old(request: Request):
        return RedirectResponse("/ui/modifier?" + request.url.query if request.url.query else "/ui/nouvelle")

    @mcp.custom_route("/ui/graph", methods=["GET"])
    async def ui_graph_old(request: Request):
        return RedirectResponse("/ui/graphe")

    @mcp.custom_route("/ui/people", methods=["GET"])
    async def ui_people_old(request: Request):
        return RedirectResponse("/ui/memoire/people")
