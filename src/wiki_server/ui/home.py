"""Accueil: what needs the user now, a box to note something, and search."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from urllib.parse import quote

from wiki_server.ui import parts
from wiki_server.ui.layout import (
    CATEGORY_LABEL, card, chip, empty, esc, head, page_url, plural, row, rows, title_of,
)

_WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"]


def _note_box(csrf: str) -> str:
    return card("Noter quelque chose", (
        '<form method="post" action="/ui/note">'
        f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
        '<textarea name="text" rows="2" required '
        'placeholder="Un fait, une idée, un rendez-vous… La mémoire le range cette nuit."></textarea>'
        '<div class="actions"><button class="btn primary" type="submit">Retenir</button></div></form>'))


def _home_body(csrf: str) -> str:
    day = parts.today()
    hello = f"{_WEEKDAYS[day.weekday()].capitalize()} {day.day} {_MONTHS[day.month - 1]}"
    out = [head("Bonjour", hello), _note_box(csrf)]

    b = parts.buckets(day)
    now = b["late"] + b["week"]
    todo_rows = [parts.item_row(i, csrf, day, "/ui") for i in now[:8]]
    more = len(now) - 8
    body = rows(todo_rows) if todo_rows else empty("Rien d'urgent cette semaine.")
    if more > 0:
        body += f'<p class="muted">Et {plural(more, "autre chose")} cette semaine.</p>'
    out.append(card("Cette semaine", body, '<a href="/ui/temps">Tout voir</a>'))

    qs = parts.open_questions()
    if qs:
        q_rows = "".join(parts.question_block(q, csrf, "/ui") for q in qs[:3])
        link = f'<a href="/ui/questions">Toutes ({len(qs)})</a>' if len(qs) > 3 else ""
        out.append(card("La mémoire te demande", f'<ul class="rows">{q_rows}</ul>', link))

    bdays = parts.birthdays(day, 30)
    if bdays:
        out.append(card("Anniversaires à venir",
                        rows([parts.birthday_row(*t, day) for t in bdays[:4]]),
                        '<a href="/ui/memoire/people">Personnes</a>'))

    from wiki_server.dream import list_reports

    reports = [r for r in list_reports() if r.endswith(".md")]
    waiting = parts.stm_entries()
    dream_rows = [parts.report_row(parts.report_summary(reports[0]), day)] if reports else []
    dream_rows.append(row(plural(len(waiting), "note") + " en attente", "Elles seront rangées au prochain rêve.",
                          "/ui/memoire/attente", lead="📝"))
    out.append(card("Dernier rêve", rows(dream_rows), '<a href="/ui/reglages/reve">Réglages</a>'))

    out.append(card("Modifié récemment", parts.page_rows(parts.recent_pages(5), day),
                    '<a href="/ui/memoire">Toute la mémoire</a>'))
    return "".join(out)


def _snippet(rel: str, keywords: list[str]) -> str:
    from wiki_server.paths import wiki_root
    from wiki_server.query import fold

    try:
        text = (wiki_root() / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    body_started = not text.startswith("---")
    for line in text.splitlines()[1:]:
        if not body_started:
            body_started = line.strip() == "---"
            continue
        clean = line.strip().lstrip("#-*> ").replace("**", "")
        if clean and any(k in fold(clean) for k in keywords):
            return clean[:180]
    return ""


def _search(q: str) -> tuple[list[dict], str]:
    from wiki_server.graph import build_graph
    from wiki_server.query import _keywords, rank_pages

    keywords = _keywords(q)
    nodes = {n["id"]: n for n in build_graph(True)["nodes"]}
    out = []
    for rel, _score in rank_pages(q)[:30]:
        n = nodes.get(rel, {})
        out.append({"path": rel, "title": n.get("label") or "", "category": n.get("category", ""),
                    "snippet": _snippet(rel, keywords) or n.get("description", "")})
    return out, " ".join(keywords)


def register(mcp, ctx) -> None:
    @ctx.get(mcp, "/ui")
    async def ui_home(request: Request, login: str):
        if request.query_params.get("dir") is not None:  # the old file browser
            return RedirectResponse("/ui/fichiers?dir=" + quote(request.query_params["dir"]))
        body = await run_in_threadpool(_home_body, ctx.csrf(login))
        return ctx.render(request, login, "Accueil", body, section="home")

    @ctx.get(mcp, "/ui/recherche")
    async def ui_search(request: Request, login: str):
        from wiki_server.paths import wiki_root

        q = (request.query_params.get("q") or "").strip()
        form = ('<form action="/ui/recherche" role="search" class="field">'
                f'<input type="search" name="q" value="{esc(q)}" placeholder="Un nom, un lieu, un sujet…" autofocus></form>')
        if not q:
            return ctx.render(request, login, "Chercher", head("Chercher") + form, section="home")
        results, _ = await run_in_threadpool(_search, q)
        items = []
        for r in results:
            title = r["title"]
            if not title:
                try:
                    title = title_of((wiki_root() / r["path"]).read_text(encoding="utf-8"), r["path"])
                except (OSError, UnicodeDecodeError):
                    title = r["path"]
            cat = CATEGORY_LABEL.get(r["category"], "")
            items.append(row(esc(title), esc(r["snippet"]), page_url(r["path"]),
                             chip(cat) if cat else ""))
        body = (head("Chercher", f"{plural(len(results), 'résultat')} pour « {esc(q)} »") + form
                + (card("", rows(items)) if items else card("", empty(
                    "Rien trouvé. Essaie un autre mot, ou un nom seul."))))
        return ctx.render(request, login, f"Chercher {q}", body, section="home")

    @ctx.get(mcp, "/ui/questions")
    async def ui_questions(request: Request, login: str):
        qs = parts.open_questions()
        csrf = ctx.csrf(login)
        body = head("Questions de la mémoire",
                    "Ce qu'elle préfère te demander plutôt que de deviner. Une réponse courte suffit.")
        if qs:
            body += card("", '<ul class="rows">' + "".join(
                parts.question_block(q, csrf, "/ui/questions", open_=i == 0) for i, q in enumerate(qs)) + "</ul>")
        else:
            body += card("", empty("Aucune question en attente."))
        return ctx.render(request, login, "Questions", body, section="home")

    @ctx.action(mcp, "/ui/note")
    async def ui_note(request: Request, login: str, form):
        from wiki_server import capture, telegram

        text = str(form.get("text") or "").strip()
        if not text:
            return ctx.done("/ui")
        saved = await run_in_threadpool(capture.save, text)
        msg = "Noté ✓ La mémoire le rangera au prochain rêve."
        if saved["flagged"]:
            msg += (" Le secret est parti dans le coffre chiffré." if saved["vaulted"]
                    else " Ça ressemblait à un secret : masqué.")
        ask = telegram._clarify_prompt(text)
        if ask:
            msg += f" Une précision aiderait : {ask}"
        return ctx.done("/ui", msg)

    @ctx.action(mcp, "/ui/questions/answer")
    async def ui_question_answer(request: Request, login: str, form):
        from wiki_server import capture, questions

        rel = questions.resolve_question(str(form.get("path") or ""))
        answer = str(form.get("answer") or "").strip()
        back = parts.safe_back(form.get("back"))
        if rel is None:
            return PlainTextResponse("Question introuvable.", status_code=404)
        if not answer:
            return ctx.done(back)
        q = next((x for x in questions.list_questions() if x["path"] == rel), None)
        text = q["body"].strip() if q else rel
        await run_in_threadpool(capture.save, f"Réponse à la question « {text} » : {answer}")
        questions.mark_answered(rel)
        return ctx.done(back, "Merci, c'est noté ✓ Le prochain rêve en tiendra compte.")

    @ctx.action(mcp, "/ui/questions/skip")
    async def ui_question_skip(request: Request, login: str, form):
        from wiki_server import questions

        rel = questions.resolve_question(str(form.get("path") or ""))
        if rel is None:
            return PlainTextResponse("Question introuvable.", status_code=404)
        questions.mark_answered(rel)
        return ctx.done(parts.safe_back(form.get("back")), "Question retirée, elle ne reviendra pas.")

    @ctx.action(mcp, "/ui/temporal/done")
    async def ui_temporal_done(request: Request, login: str, form):
        from wiki_server import temporal
        from wiki_server.paths import WikiPathError

        rel = str(form.get("path") or "").strip()
        if not rel.startswith("temporal/"):
            return PlainTextResponse("Ce n'est pas une tâche.", status_code=400)
        try:
            if not temporal.mark_done(rel):
                return PlainTextResponse("Introuvable.", status_code=404)
        except WikiPathError:
            return PlainTextResponse("Interdit.", status_code=403)
        return ctx.done(parts.safe_back(form.get("back"), page_url(rel)), "C'est fait ✓")

