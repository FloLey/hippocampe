"""Temps: what is coming (todos, reminders, events), the month-by-month
thread of the user's life, and the year in review."""

from __future__ import annotations

import datetime

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse

from wiki_server.ui import parts
from wiki_server.ui.layout import card, empty, esc, head, page_url, plural, rows, tabs

_ICONS = {"événement": "📍", "fait": "✅", "à faire": "☐", "rappel": "🔔", "noté": "📝"}


def _tabs(active: str) -> str:
    return tabs([("/ui/temps", "À venir"), ("/ui/temps/fil", "Fil du temps"),
                 ("/ui/temps/annee", "Ton année")], active)


def _coming_body(csrf: str) -> str:
    from wiki_server import temporal

    day = parts.today()
    b = parts.buckets(day)
    out = [head("Temps", "Tes tâches, rappels et événements. Pour en ajouter, dis-le simplement "
                "en conversation ou note-le depuis l'accueil."), _tabs("/ui/temps")]
    groups = [("late", "En retard"), ("week", "Cette semaine"), ("later", "Plus tard"), ("undated", "Sans date")]
    shown = False
    for key, label in groups:
        if b[key]:
            shown = True
            out.append(card(f"{label} ({len(b[key])})",
                            rows([parts.item_row(i, csrf, day, "/ui/temps") for i in b[key]])))
    if not shown:
        out.append(card("", empty("Rien de prévu. Profite !")))
    done = [i for i in temporal.list_items() if i["meta"].get("status") == "done"][:15]
    if done:
        out.append(card("", "<details><summary>Faits récemment</summary>"
                        + rows([parts.item_row(i, csrf, day, "/ui/temps") for i in done]) + "</details>"))
    return "".join(out)


def _thread_body() -> str:
    from wiki_server import timeline

    months = timeline.months(12)
    out = [head("Temps", "Ta vie mois par mois, d'après ce que la mémoire a retenu."), _tabs("/ui/temps/fil")]
    for m in months:
        extra = []
        if m["conversations"]:
            extra.append(f"💬 {plural(m['conversations'], 'conversation')}")
        if m["sport"]["n"]:
            extra.append(f"🏃 {plural(m['sport']['n'], 'sortie')}, {m['sport']['km']:.0f} km")
        if m["new_pages"]:
            extra.append(f"🆕 {plural(len(m['new_pages']), 'nouvelle page', 'nouvelles pages')}")
        items = "".join(
            f'<li><span class="d">{esc(i["date"][8:10])}</span><span>{_ICONS.get(i["kind"], "•")}</span>'
            f'<a href="{esc(page_url(i["page"]))}">{esc(i["text"])}</a></li>'
            for i in m["items"])
        body = (f'<p class="muted">{" · ".join(extra)}</p>' if extra else "") + (
            f'<ul class="tl">{items}</ul>' if items else "")
        out.append(card(m["label"], body or empty("Rien de daté."), cls="month"))
    if not months:
        out.append(card("", empty("Rien de daté pour l'instant.")))
    return "".join(out)


def _year_body(year: int) -> str:
    from wiki_server import timeline

    w = timeline.wrapped(year)

    def stat(n, label):
        return f'<div class="stat"><b>{esc(str(n))}</b><span>{esc(label)}</span></div>'

    stats = (stat(w["notes"], "notes retenues") + stat(w["conversations"], "conversations")
             + stat(w["new_pages"], "nouvelles pages") + stat(w["done"], "choses faites"))
    if w["sport_total"]["n"]:
        stats += stat(f"{w['sport_total']['km']:.0f} km", f"en {w['sport_total']['n']} sorties")
    nav = (f'<div class="actions" style="margin:0 0 1rem"><a class="btn quiet small" href="/ui/temps/annee?year={year - 1}">← {year - 1}</a>'
           f'<a class="btn quiet small" href="/ui/temps/annee?year={year + 1}">{year + 1} →</a></div>')
    out = [head(f"Ton année {year}", "Ce que la mémoire a vu passer, en chiffres."), _tabs("/ui/temps/annee"),
           nav, f'<div class="stats">{stats}</div>']
    if w["top_month"]:
        out.append(f'<p>Ton mois le plus rempli : <b>{esc(w["top_month"])}</b>.</p>')
    if w["events"]:
        out.append(card("Les moments", '<ul class="tl">' + "".join(
            f"<li><span>📍</span><span>{esc(e)}</span></li>" for e in w["events"]) + "</ul>"))
    for cat, titles in w["by_category"].items():
        new = "Nouvelles" if cat in ("personnes", "organisations", "sources") else "Nouveaux"
        out.append(card(f"{new} {cat} ({len(titles)})", "<p>" + ", ".join(esc(t) for t in titles) + "</p>"))
    if w["sport"]:
        out.append(card("Sport", '<ul class="tl">' + "".join(
            f"<li><span>{esc(s)}</span><span class='muted'>{plural(v['n'], 'sortie')}, {v['km']:.0f} km</span></li>"
            for s, v in sorted(w["sport"].items(), key=lambda kv: -kv[1]["n"])) + "</ul>"))
    if w["hubs"]:
        out.append(card("Au cœur de ton année", "<p>Les pages les plus liées : "
                        + ", ".join(f"{esc(t)} ({n})" for t, n in w["hubs"]) + "</p>"))
    return "".join(out)


def register(mcp, ctx) -> None:
    @ctx.get(mcp, "/ui/temps")
    async def ui_coming(request: Request, login: str):
        body = await run_in_threadpool(_coming_body, ctx.csrf(login))
        return ctx.render(request, login, "Temps", body, section="time")

    @ctx.get(mcp, "/ui/temps/fil")
    async def ui_thread(request: Request, login: str):
        body = await run_in_threadpool(_thread_body)
        return ctx.render(request, login, "Fil du temps", body, section="time")

    @ctx.get(mcp, "/ui/temps/annee")
    async def ui_year(request: Request, login: str):
        try:
            year = int(request.query_params.get("year") or datetime.date.today().year)
        except ValueError:
            year = datetime.date.today().year
        body = await run_in_threadpool(_year_body, year)
        return ctx.render(request, login, f"Ton année {year}", body, section="time")

    @mcp.custom_route("/ui/timeline", methods=["GET"])
    async def ui_timeline_old(request: Request):
        return RedirectResponse("/ui/temps/fil")

    @mcp.custom_route("/ui/wrapped", methods=["GET"])
    async def ui_wrapped_old(request: Request):
        q = request.url.query
        return RedirectResponse("/ui/temps/annee" + (f"?{q}" if q else ""))
