"""Réglages: the nightly dream, the connections (Telegram, Strava, Google),
the advanced tools (prompts, models, exam, secrets), and the server logs."""

from __future__ import annotations

import datetime
import time
from urllib.parse import quote, urlparse

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse

from wiki_server.paths import WikiPathError, resolve_under_root
from wiki_server.ui import parts
from wiki_server.ui.layout import (
    _sign, _verify, button, card, chip, empty, esc, fmt_date, head, page_url, plural, post_button,
    row, rows, tabs,
)

TABS = [("/ui/reglages/reve", "Rêve"), ("/ui/reglages/connexions", "Connexions"),
        ("/ui/reglages/avance", "Avancé"), ("/ui/reglages/logs", "Logs")]
MODE_LABELS = {"off": "Désactivé", "dry-run": "Proposer seulement (rien n'est changé)",
               "execute": "Ranger automatiquement"}


def _head(active: str, subtitle: str = "") -> str:
    return head("Réglages", subtitle) + tabs(TABS, active)


# ---- the dream ----

def _dream_body(csrf: str) -> str:
    from wiki_server.dream import MODES, list_reports, schedule_status, usage_summary

    day = parts.today()
    st = schedule_status()
    if st["mode"] == "off":
        what = "Désactivé : la mémoire ne se range que quand tu lances un rêve toi-même."
    elif st["mode"] == "dry-run":
        what = (f"Chaque nuit vers {st['hour']} h, le rêve <b>propose</b> un rangement sans rien changer ; "
                "tu le lis le matin.")
    else:
        what = f"Chaque nuit vers {st['hour']} h, le rêve <b>range</b> les notes dans tes pages."
    if st["mode"] != "off":
        what += (f" Il attend au moins {plural(st['min_entries'], 'note')}, et part tout de suite "
                 f"dès qu'il y en a {st['max_entries']}.")
    now = (f"Maintenant : {plural(st['count'], 'note')} en attente, il est {esc(st['now'])} "
           f"({esc(st['tz'])}).")
    reason = "Il partira au prochain passage (sous 10 min)." if st["would_fire"] else (
        f"Pas ce soir pour l'instant : {esc(st['reason'])}." if st["mode"] != "off" else "")
    opts = "".join(f'<option value="{m}"{" selected" if m == st["mode"] else ""}>{esc(MODE_LABELS.get(m, m))}</option>'
                   for m in MODES)
    schedule = card("Rêve de la nuit", (
        f"<p>{what}</p><p class='muted'>{now} {reason}</p>"
        "<details><summary>Changer</summary>"
        '<form method="post" action="/ui/dream/schedule" style="margin-top:.75rem">'
        f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
        f'<div class="field"><label for="s-mode">Mode</label><select id="s-mode" name="mode">{opts}</select></div>'
        '<div class="grid2">'
        f'<div class="field"><label for="s-hour">Heure (0-23)</label>'
        f'<input type="number" id="s-hour" name="hour" min="0" max="23" value="{st["hour"]}"></div>'
        f'<div class="field"><label for="s-min">Notes minimum</label>'
        f'<input type="number" id="s-min" name="min_entries" min="1" value="{st["min_entries"]}"></div>'
        f'<div class="field"><label for="s-max">Rêve immédiat à partir de</label>'
        f'<input type="number" id="s-max" name="max_entries" min="1" value="{st["max_entries"]}"></div></div>'
        '<button class="btn primary" type="submit">Enregistrer</button></form></details>'))

    now_card = card("Lancer un rêve maintenant", (
        "<p class='muted'>Prend une à deux minutes. Rien n'est jamais effacé, et chaque rangement "
        "s'annule d'un clic dans l'historique.</p><div class='actions'>"
        + post_button(csrf, "/ui/dream/execute", "Ranger maintenant",
                      confirm="Ranger les notes en attente dans tes pages ?")
        + post_button(csrf, "/ui/dream/run", "Simuler (ne change rien)", kind="secondary")
        + "</div>"))

    reports = [parts.report_summary(r) for r in list_reports()]
    rep_rows = [parts.report_row(r, day) for r in reports[:8]]
    older = [parts.report_row(r, day) for r in reports[8:40]]
    rep = rows(rep_rows) if rep_rows else empty("Aucun rêve pour l'instant.")
    if older:
        rep += f"<details><summary>Plus anciens ({len(older)})</summary>{rows(older)}</details>"

    u = usage_summary()
    stages = u.get("by_stage") or {}
    order = [s for s in ("triage", "decide", "write") if s in stages] + sorted(
        s for s in stages if s not in ("triage", "decide", "write"))
    names = {"triage": "Tri", "decide": "Décision", "write": "Écriture"}
    stage_rows = "".join(
        f"<tr><th>{esc(names.get(s, s))}</th><td>${stages[s]['cost']:.2f} "
        f"<small>({esc(', '.join(sorted(stages[s].get('models', []))) or '?')})</small></td></tr>" for s in order)
    cost = card("Coût", (
        f"<p>{u['runs']} rêves, <b>${u['total_cost']:.2f}</b> au total · environ "
        f"${u['avg_cost']:.2f} par nuit (dernière : ${u['last_cost']:.2f}).</p>"
        "<details><summary>Détail</summary>"
        f'<table class="kv"><tbody>{stage_rows}'
        f"<tr><th>Tokens</th><td>{u['input_tokens']:,} entrée · {u['output_tokens']:,} sortie</td></tr>"
        "</tbody></table><p class='muted'>Les tokens sont exacts ; le coût est estimé d'après les tarifs "
        "publics des modèles.</p><div class='actions'>"
        + post_button(csrf, "/ui/dream/reset-cost", "Remettre le compteur à zéro", kind="quiet small",
                      confirm="Remettre le compteur de coût à zéro ?")
        + "</div></details>"))
    return (_head("/ui/reglages/reve", "Le rêve range chaque nuit les notes prises en conversation.")
            + schedule + now_card + card("Rapports", rep) + cost)


# ---- connections ----

_STEPS = {
    "strava": (
        "<ol class='steps'><li>Sur <b>strava.com/settings/api</b>, crée une application : "
        "site web <code>{base}</code>, domaine de rappel <code>{host}</code>.</li>"
        "<li>Copie le <b>Client ID</b> et le <b>Client Secret</b> ci-dessous.</li>"
        "<li>Appuie sur <b>Connecter</b> et accepte.</li></ol>"
    ),
    "google": (
        "<ol class='steps'><li>Sur <b>console.cloud.google.com</b>, crée un projet « Memory wiki ».</li>"
        "<li>APIs et services, Bibliothèque : active <b>Google Calendar API</b> et <b>Gmail API</b>.</li>"
        "<li>Écran de consentement OAuth : type Externe, ajoute ton adresse comme utilisateur test, "
        "puis <b>Publier l'application</b> (sinon la connexion expire après 7 jours).</li>"
        "<li>Identifiants, Créer un ID client OAuth, type Application Web, URI de redirection : "
        "<code>{redirect}</code></li>"
        "<li>Copie l'ID client et le code secret ci-dessous, puis <b>Connecter</b>. Google affiche un "
        "avertissement (application non validée) : Paramètres avancés, Continuer.</li></ol>"
    ),
}
_WHAT = {
    "strava": "Chaque nuit, tes activités vont dans un journal sportif, avec un résumé par semaine.",
    "google": ("Chaque nuit, les 30 prochains jours de ton agenda deviennent des rappels, et les "
               "emails que tu étiquettes « Mémoire » dans Gmail sont retenus."),
}


def _telegram_card(csrf: str, code: str = "") -> str:
    from urllib.parse import quote as q

    from wiki_server import telegram, vault

    st = telegram.status()
    what = "<p class='muted'>Un résumé de ta semaine chaque dimanche, et une note envoyée au bot est retenue.</p>"
    if not vault.enabled():
        return card("Telegram", what + "<p>Le coffre chiffré n'est pas configuré : impossible d'enregistrer le jeton.</p>")
    if not st["token"]:
        status, inner = chip("à configurer"), (
            "<ol class='steps'><li>Dans Telegram, ouvre <b>@BotFather</b> et envoie <code>/newbot</code>.</li>"
            "<li>Choisis un nom, puis un identifiant qui finit par <code>bot</code>.</li>"
            "<li>Copie le jeton qu'il te donne et colle-le ici.</li></ol>"
            '<form method="post" action="/ui/connections/telegram/token">'
            f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
            '<div class="field"><label>Jeton du bot</label>'
            '<input type="password" name="token" autocomplete="off" required></div>'
            '<button class="btn primary" type="submit">Enregistrer</button></form>')
    elif not st["linked"]:
        bot = esc(st["bot"])
        status = chip("à relier", "soon")
        if code:
            link = f"https://t.me/{q(st['bot'])}?start={code}"
            inner = (f"<p>Ouvre ce lien sur ton téléphone et appuie sur <b>Démarrer</b> (valable 15 min) :</p>"
                     f'<p><a class="btn primary" href="{esc(link)}">Ouvrir @{bot} dans Telegram</a></p>'
                     f"<p class='muted'>Ou envoie-lui ce code : <b>{esc(code)}</b></p>")
        else:
            inner = (f"<p>Bot <b>@{bot}</b> prêt, pas encore relié à ton téléphone.</p><div class='actions'>"
                     + post_button(csrf, "/ui/connections/telegram/pair", "Relier mon téléphone") + "</div>")
        inner += "<div class='actions'>" + post_button(csrf, "/ui/connections/telegram/forget",
                                                        "Changer de jeton", kind="quiet small") + "</div>"
    else:
        status = chip("connecté ✓", "ok")
        inner = (f"<p>Relié à <b>@{esc(st['bot'])}</b>. Dernier résumé : "
                 f"{esc(fmt_date(st['last_digest']) if st['last_digest'] else 'jamais')}.</p><div class='actions'>"
                 + post_button(csrf, "/ui/connections/telegram/digest", "Envoyer le résumé maintenant", kind="secondary")
                 + post_button(csrf, "/ui/connections/telegram/unlink", "Délier", kind="quiet",
                               confirm="Délier ton téléphone du bot ?") + "</div>")
    if st["error"]:
        inner += f"<p class='muted'>Dernière erreur : {esc(st['error'])}</p>"
    return card("Telegram", what + inner, status)


def _provider_card(csrf: str, provider: str, base: str) -> str:
    from wiki_server import connections

    label = connections.PROVIDERS[provider]["label"]
    st = connections.status(provider)
    what = f"<p class='muted'>{_WHAT[provider]}</p>"
    act = f"/ui/connections/{provider}"
    if st["connected"]:
        status = chip("connecté ✓", "ok")
        who = f" à <b>{esc(st['account'])}</b>" if st["account"] else ""
        inner = (f"<p>Connecté{who}." + (f" Dernière synchro : {esc(st['last_sync'])}." if st["last_sync"] else "")
                 + "</p><div class='actions'>"
                 + post_button(csrf, f"{act}/sync", "Synchroniser maintenant", kind="secondary")
                 + post_button(csrf, f"{act}/disconnect", "Déconnecter", kind="quiet",
                               confirm=f"Déconnecter {label} ?") + "</div>")
        if provider == "google":
            from wiki_server.sync import calendar_settings

            cs = calendar_settings()

            def box(name: str, text: str) -> str:
                return (f'<label class="check"><input type="checkbox" name="{name}" value="1"'
                        f'{" checked" if cs[name] else ""}><span>{text}</span></label>')

            inner += (
                '<hr style="border:0;border-top:1px solid var(--line);margin:1rem 0">'
                '<form method="post" action="/ui/connections/google/calendar">'
                f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
                "<p><b>Agenda « Mémoire »</b><br><small>Un agenda à part, jamais le tien. Chaque "
                "événement a une petite note.</small></p>"
                + box("birthdays", "🎂 Anniversaires : chaque année, rappel la veille, idées cadeaux")
                + box("todos", "☐ Tâches et rappels datés, retirés une fois faits")
                + '<div class="actions"><button class="btn primary" type="submit">Enregistrer</button></div></form>')
    elif st["configured"]:
        status = chip("à connecter", "soon")
        inner = ("<p>Application enregistrée, il reste à te connecter.</p><div class='actions'>"
                 + button(f"Connecter {label}", f"{act}/start")
                 + post_button(csrf, f"{act}/forget", "Changer d'application", kind="quiet") + "</div>")
    else:
        status = chip("à configurer")
        steps = _STEPS[provider].format(base=esc(base), host=esc(urlparse(base).netloc),
                                        redirect=esc(connections.redirect_uri(base, provider)))
        inner = (f"<details><summary>Configurer (10 min, sur ordinateur)</summary>{steps}"
                 f'<form method="post" action="{act}/app">'
                 f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
                 '<div class="field"><label>Client ID</label>'
                 '<input type="text" name="client_id" autocomplete="off" required></div>'
                 '<div class="field"><label>Client secret</label>'
                 '<input type="password" name="client_secret" autocomplete="off" required></div>'
                 '<button class="btn primary" type="submit">Enregistrer</button></form></details>')
    if st["error"]:
        inner += f"<p class='muted'>Dernière erreur : {esc(st['error'])}</p>"
    title = "Google (Agenda, Gmail)" if provider == "google" else label
    return card(title, what + inner, status)


def _connections_body(csrf: str, base: str, code: str = "") -> str:
    return (_head("/ui/reglages/connexions", "Les jetons sont gardés dans le coffre chiffré, jamais dans git.")
            + _telegram_card(csrf, code) + _provider_card(csrf, "google", base)
            + _provider_card(csrf, "strava", base))


# ---- advanced ----

def _advanced_body(csrf: str) -> str:
    from wiki_server import exam, vault
    from wiki_server.dream import AVAILABLE_MODELS, DREAM_POLICY, STAGES, effective_models, ensure_policy
    from wiki_server.prompts import PROMPT_FILES, ensure_prompt

    ensure_policy()
    for stage in PROMPT_FILES:
        ensure_prompt(stage)
    names = {"triage": "Étape 1 · Tri", "decide": "Étape 2 · Décision", "write": "Étape 3 · Écriture"}
    helps = {"triage": "Regroupe les notes et choisit où elles vont (1 appel par nuit).",
             "decide": "Décide quoi changer, sujet par sujet (1 appel par sujet).",
             "write": "Rédige chaque page modifiée (1 appel par page)."}
    items = [("Politique du rêve", DREAM_POLICY, "Ce que le rêve doit faire ou éviter, en clair.")]
    items += [(names[s], PROMPT_FILES[s], helps[s]) for s in ("triage", "decide", "write")]
    prompt_rows = [row(esc(t), esc(h), page_url(rel),
                       button("Modifier", f"/ui/modifier?path={quote(rel)}", "quiet small")) for t, rel, h in items]
    prompts = card("Consignes du rêve", "<p class='muted'>Le format de réponse est ajouté par le code : "
                   "modifier ces textes ne casse jamais rien.</p>" + rows(prompt_rows))

    effective = effective_models()
    selects = []
    for stage in STAGES:
        current = effective.get(stage, "")
        opts = []
        if current and not any(mid == current for mid, _ in AVAILABLE_MODELS):
            opts.append(f'<option value="{esc(current)}" selected>{esc(current)} (actuel)</option>')
        opts += [f'<option value="{esc(mid)}"{" selected" if mid == current else ""}>{esc(lbl)}</option>'
                 for mid, lbl in AVAILABLE_MODELS]
        selects.append(f'<div class="field"><label for="m-{stage}">{esc(names.get(stage, stage))}</label>'
                       f'<select id="m-{stage}" name="{stage}">{"".join(opts)}</select></div>')
    models = card("Modèles", (
        "<p class='muted'>Un modèle moins cher pour le tri et la décision réduit beaucoup le coût par nuit ; "
        "l'écriture gagne à un modèle plus fort.</p>"
        '<form method="post" action="/ui/prompts/models">'
        f'<input type="hidden" name="csrf" value="{esc(csrf)}">{"".join(selects)}'
        '<button class="btn primary" type="submit">Enregistrer</button></form>'))

    questions = exam.load()
    last = ""
    try:
        hist = resolve_under_root(exam.HISTORY_FILE)
        if hist.is_file():
            lines = [ln for ln in hist.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
            if lines:
                d, _, score = lines[-1][2:].partition(" : ")
                last = f" Dernier score : <b>{esc(score)}</b> ({esc(fmt_date(d))})."
    except (OSError, WikiPathError):
        pass
    exam_rows = [row("Les questions", "Une par ligne : la question, puis les mots que la réponse doit contenir.",
                     f"/ui/modifier?path={quote(exam.EXAM_FILE)}", lead="✏️")]
    if last:
        exam_rows.append(row("L'historique des scores", "", page_url(exam.HISTORY_FILE), lead="📈"))
    exam_card = card("Examen", (
        f"<p class='muted'>Après chaque rêve, la mémoire vérifie qu'elle retrouve encore "
        f"{plural(len(questions), 'réponse connue', 'réponses connues')}, pour voir tout de suite si un fait se perd.{last}</p>"
        + rows(exam_rows)))

    tools = [row("Tous les fichiers", "La mémoire telle qu'elle est stockée.", "/ui/fichiers", lead="🗂")]
    if vault.enabled():
        tools.append(row("Ranger les secrets au coffre", "Cherche les clés et numéros encore écrits dans les "
                         "pages et les met dans le coffre chiffré.", "", post_button(
                             csrf, "/ui/prompts/extract-secrets", "Lancer", kind="quiet small",
                             confirm="Déplacer les secrets des pages vers le coffre ?"), lead="🔐"))
    try:
        ent = resolve_under_root("long_term/entities")
        if ent.is_dir() and any(ent.glob("*.md")):
            tools.append(row("Classer les anciennes pages « entities »", "En personnes, lieux et organisations.",
                             "", post_button(csrf, "/ui/prompts/migrate-entities", "Lancer", kind="quiet small",
                                             confirm="Classer les pages entities ?"), lead="🧹"))
    except WikiPathError:
        pass
    tools.append(row("Remettre les consignes par défaut", "La politique et les trois étapes reprennent "
                     "leur version d'origine.", "", post_button(
                         csrf, "/ui/prompts/reset", "Réinitialiser", kind="danger small",
                         confirm="Remettre la politique et les trois étapes par défaut ?"), lead="↺"))
    tools.append(row("Se déconnecter", "", "/ui/logout", lead="🚪"))
    return (_head("/ui/reglages/avance") + prompts + models + exam_card + card("Outils", rows(tools)))


def _logs_body() -> str:
    from wiki_server.logbuffer import recent_logs

    entries = recent_logs(200)
    if not entries:
        return _head("/ui/reglages/logs") + card("", empty("Aucun log pour l'instant (le serveur vient peut-être de démarrer)."))
    items = "".join(
        f'<div class="logrow"><small class="lvl-{esc(e["level"])}">{esc(e["time"])} · {esc(e["level"])} · '
        f'{esc(e["name"])}</small><pre>{esc(e["msg"])}</pre></div>' for e in entries)
    return (_head("/ui/reglages/logs", "Ce que le serveur a écrit récemment, le plus récent en haut. "
                  "Utile si un rêve ou une synchro échoue.") + card("", items))


def register(mcp, ctx) -> None:
    base = ctx.base
    conn = "/ui/reglages/connexions"

    @ctx.get(mcp, "/ui/reglages")
    async def ui_settings(request: Request, login: str):
        return RedirectResponse("/ui/reglages/reve")

    @ctx.get(mcp, "/ui/reglages/reve")
    async def ui_settings_dream(request: Request, login: str):
        body = await run_in_threadpool(_dream_body, ctx.csrf(login))
        return ctx.render(request, login, "Rêve", body, section="settings")

    @ctx.get(mcp, "/ui/reglages/connexions")
    async def ui_settings_connections(request: Request, login: str):
        body = await run_in_threadpool(_connections_body, ctx.csrf(login), base)
        return ctx.render(request, login, "Connexions", body, section="settings")

    @ctx.get(mcp, "/ui/reglages/avance")
    async def ui_settings_advanced(request: Request, login: str):
        body = await run_in_threadpool(_advanced_body, ctx.csrf(login))
        return ctx.render(request, login, "Avancé", body, section="settings")

    @ctx.get(mcp, "/ui/reglages/logs")
    async def ui_settings_logs(request: Request, login: str):
        return ctx.render(request, login, "Logs", _logs_body(), section="settings")

    # ---- dream actions ----
    @ctx.action(mcp, "/ui/dream/run")
    async def ui_dream_run(request: Request, login: str, form):
        from wiki_server.dream import run_dry_run

        rel, _ = await run_in_threadpool(run_dry_run)
        return ctx.done(page_url(rel), "Simulation terminée : voici ce que le rêve ferait.")

    @ctx.action(mcp, "/ui/dream/execute")
    async def ui_dream_execute(request: Request, login: str, form):
        from wiki_server.dream import run_execute

        rel, _ = await run_in_threadpool(run_execute)
        return ctx.done(page_url(rel), "Rêve terminé ✓ Voici ce qui a été rangé.")

    @ctx.action(mcp, "/ui/dream/reset-cost")
    async def ui_dream_reset_cost(request: Request, login: str, form):
        from wiki_server.dream import reset_usage

        reset_usage()
        return ctx.done("/ui/reglages/reve", "Compteur remis à zéro.")

    @ctx.action(mcp, "/ui/dream/schedule")
    async def ui_dream_schedule(request: Request, login: str, form):
        from wiki_server.dream import set_schedule

        set_schedule(form.get("mode"), form.get("hour"),
                     min_entries=form.get("min_entries"), max_entries=form.get("max_entries"))
        return ctx.done("/ui/reglages/reve", "Réglages du rêve enregistrés ✓")

    # ---- advanced actions ----
    @ctx.action(mcp, "/ui/prompts/migrate-entities")
    async def ui_prompts_migrate(request: Request, login: str, form):
        from wiki_server.dream import migrate_entities

        rel, _ = await run_in_threadpool(migrate_entities)
        return ctx.done(page_url(rel), "Pages classées ✓")

    @ctx.action(mcp, "/ui/prompts/extract-secrets")
    async def ui_prompts_extract_secrets(request: Request, login: str, form):
        from wiki_server import vault

        rel, _ = await run_in_threadpool(vault.extract_from_wiki)
        return ctx.done(page_url(rel), "Secrets rangés au coffre ✓")

    @ctx.action(mcp, "/ui/prompts/reset")
    async def ui_prompts_reset(request: Request, login: str, form):
        from wiki_server.prompts import reset_prompts

        reset_prompts()
        return ctx.done("/ui/reglages/avance", "Consignes remises par défaut ✓")

    @ctx.action(mcp, "/ui/prompts/models")
    async def ui_prompts_models(request: Request, login: str, form):
        from wiki_server.dream import STAGES, set_models

        set_models({s: form.get(s) for s in STAGES})
        return ctx.done("/ui/reglages/avance", "Modèles enregistrés ✓")

    # ---- connections ----
    # The Telegram route is registered before the generic provider one, which
    # would otherwise take /ui/connections/telegram/... for itself.
    @ctx.action(mcp, "/ui/connections/telegram/{action}")
    async def ui_connections_telegram(request: Request, login: str, form):
        from wiki_server import telegram

        action = request.path_params["action"]
        try:
            if action == "token":
                bot = await run_in_threadpool(telegram.set_token, str(form.get("token") or ""))
                return ctx.done(conn, f"Jeton accepté : @{bot}. Relie maintenant ton téléphone.")
            if action == "pair":
                body = _connections_body(ctx.csrf(login), base, code=telegram.new_pair_code())
                return ctx.render(request, login, "Connexions", body, section="settings")
            if action == "digest":
                sent = await run_in_threadpool(telegram.send_digest)
                return ctx.done(conn, "Résumé envoyé ✓" if sent else "Aucun téléphone relié.")
            if action == "unlink":
                telegram.unlink()
                return ctx.done(conn, "Téléphone délié.")
            if action == "forget":
                from wiki_server import vault

                vault.config_set(telegram.TOKEN_KEY, "")
                telegram.unlink()
                return ctx.done(conn, "Jeton supprimé.")
        except telegram.TelegramError as exc:
            return ctx.done(conn, f"Telegram a refusé : {exc}")
        return PlainTextResponse("Action inconnue.", status_code=404)

    @ctx.get(mcp, "/ui/connections/{provider}/start")
    async def ui_oauth_start(request: Request, login: str):
        from wiki_server import connections

        provider = request.path_params["provider"]
        if provider not in connections.PROVIDERS:
            return PlainTextResponse("Service inconnu.", status_code=404)
        state = _sign(ctx.secret_key, {"k": "oauth", "p": provider, "login": login, "exp": time.time() + 900})
        return RedirectResponse(connections.authorize_url(provider, base, state))

    @ctx.get(mcp, "/ui/connections/{provider}/callback")
    async def ui_oauth_callback(request: Request, login: str):
        from wiki_server import connections

        provider = request.path_params["provider"]
        if provider not in connections.PROVIDERS:
            return PlainTextResponse("Service inconnu.", status_code=404)
        payload = _verify(ctx.secret_key, request.query_params.get("state"))
        if not (payload and payload.get("k") == "oauth" and payload.get("p") == provider
                and payload.get("login") == login):
            return PlainTextResponse("Invalid OAuth state.", status_code=403)
        code = request.query_params.get("code")
        if not code:
            return ctx.done(conn, f"Connexion annulée ({request.query_params.get('error', '?')}).")
        try:
            await run_in_threadpool(connections.exchange_code, provider, code, base)
            if provider == "google":
                try:
                    prof = await run_in_threadpool(
                        connections.api_get, "google", "https://gmail.googleapis.com/gmail/v1/users/me/profile")
                    connections.set_fields("google", account=prof.get("emailAddress", ""))
                except connections.ConnectionError_:
                    pass
        except connections.ConnectionError_ as exc:
            return ctx.done(conn, str(exc))
        return ctx.done(conn, f"{connections.PROVIDERS[provider]['label']} connecté ✓ "
                              "Première synchro cette nuit, ou maintenant avec le bouton.")

    @ctx.action(mcp, "/ui/connections/{provider}/{action}")
    async def ui_oauth_action(request: Request, login: str, form):
        from wiki_server import connections, sync

        provider, action = request.path_params["provider"], request.path_params["action"]
        if provider not in connections.PROVIDERS:
            return PlainTextResponse("Service inconnu.", status_code=404)
        if action == "app":
            cid, csec = str(form.get("client_id") or "").strip(), str(form.get("client_secret") or "").strip()
            if not cid or not csec:
                return ctx.done(conn, "Le Client ID et le secret sont tous les deux nécessaires.")
            connections.set_fields(provider, client_id=cid, client_secret=csec)
            return ctx.done(conn, "Application enregistrée : appuie sur Connecter.")
        if action == "sync":
            summary = await run_in_threadpool(sync.run, provider)
            return ctx.done(conn, summary)
        if action == "calendar" and provider == "google":
            connections.set_fields("google", cal_birthdays="1" if form.get("birthdays") else "0",
                                   cal_todos="1" if form.get("todos") else "0")
            try:
                summary = await run_in_threadpool(sync.push_calendar, datetime.date.today())
            except connections.ConnectionError_ as exc:
                summary = f"mais : {exc}"
            return ctx.done(conn, f"Réglages enregistrés ✓ {summary}")
        if action == "disconnect":
            connections.disconnect(provider)
            return ctx.done(conn, "Déconnecté.")
        if action == "forget":
            connections.forget(provider)
            return ctx.done(conn, "Application oubliée.")
        return PlainTextResponse("Action inconnue.", status_code=404)

    # Old addresses, kept working (bookmarks, the Telegram and sync messages).
    for old, new in (("/ui/dream", "/ui/reglages/reve"), ("/ui/prompts", "/ui/reglages/avance"),
                     ("/ui/connections", conn), ("/ui/logs", "/ui/reglages/logs")):
        async def _redirect(request: Request, _to: str = new):
            return RedirectResponse(_to)
        _redirect.__name__ = "old_" + old.strip("/").replace("/", "_")
        mcp.custom_route(old, methods=["GET"])(_redirect)

