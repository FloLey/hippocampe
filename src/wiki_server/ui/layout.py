"""The console's look: signed tokens, the stylesheet, the page shell and the
small set of building blocks every page is made of (cards, rows, buttons,
tabs, dates), so all pages read the same way.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import html
import json
import time
from urllib.parse import quote

from starlette.responses import HTMLResponse

esc = html.escape


# ----------------------------------------------------------------------------
# Signing helpers (stdlib HMAC; no extra dependency)
# ----------------------------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(secret: str, payload: dict) -> str:
    raw = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def _verify(secret: str, token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    raw, sig = token.split(".", 1)
    expected = _b64e(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(raw))
        if not isinstance(payload, dict):
            return None
        exp = float(payload.get("exp", 0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if exp < time.time():
        return None
    return payload


# ----------------------------------------------------------------------------
# Words and dates
# ----------------------------------------------------------------------------

# The hippocampus (a seahorse) under a crescent moon: memory sorted at night.
LOGO_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="hg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4a7fd0"/><stop offset="1" stop-color="#9b6fd6"/></linearGradient></defs><rect width="64" height="64" rx="15" fill="url(#hg)"/><path d="M14.5 9a6.5 6.5 0 1 0 7.2 8.4a5 5 0 1 1 -7.2 -8.4z" fill="#fff" opacity=".92"/><circle cx="23.5" cy="27" r="1" fill="#fff" opacity=".6"/><circle cx="16" cy="31" r=".75" fill="#fff" opacity=".45"/><g fill="#fff"><path d="M32.5 19.5 C 26 25, 26.5 36.5, 35 42.5 L 40.5 42 C 47.5 36, 48.5 23.5, 41.5 15.5 Z"/><circle cx="36.5" cy="14.5" r="5.6"/><path d="M32.6 12.6 L 23.4 18.2 Q 22.4 19.6 23.9 20.2 L 33.8 18.4 Z"/><path d="M36 9.3 L 37.6 5.6 L 40 9.8 Z"/><path d="M45.2 24.5 L 50.5 27.2 L 49.6 30.4 L 46 32.5 Z"/></g><path d="M38 41 C 39.5 47.5, 37.5 53.5, 32.5 53.5 C 28.8 53.5, 28.2 48.6, 31.8 48.4" fill="none" stroke="#fff" stroke-width="3.6" stroke-linecap="round"/><circle cx="36.2" cy="13.8" r="1.3" fill="#5b6fd0"/><g fill="none" stroke="#7d76d4" stroke-width="1" stroke-linecap="round" opacity=".55"><path d="M29.5 27.5 Q 33 28.8 36 27.6"/><path d="M29.4 32 Q 33 33.4 36.5 32"/><path d="M30.6 36.5 Q 34 37.8 37.5 36.4"/></g></svg>'
LOGO_ICON = "data:image/svg+xml," + quote(LOGO_SVG)

CATEGORIES = [  # (folder, label, one-line description), in display order
    ("self", "Moi", "Qui tu es, ta vie, tes repères"),
    ("people", "Personnes", "Famille, amis, collègues"),
    ("projects", "Projets", "Ce que tu fais ou as fait"),
    ("places", "Lieux", "Maisons, villes, restaurants"),
    ("organizations", "Organisations", "Entreprises, clubs, écoles"),
    ("concepts", "Concepts", "Idées, sujets, savoirs"),
    ("sources", "Sources", "Livres, liens, choses vues ailleurs"),
    ("entities", "Entités (ancien)", "Pages d'avant le classement"),
]
CATEGORY_LABEL = {c: label for c, label, _ in CATEGORIES}

_MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août",
           "sept.", "oct.", "nov.", "déc."]
_DAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def as_date(value) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def fmt_date(value, today: datetime.date | None = None, weekday: bool = False) -> str:
    """"17 oct.", "17 oct. 2025" (another year), or the raw text if not a date."""
    d = as_date(value)
    if d is None:
        return str(value or "")
    today = today or datetime.date.today()
    out = f"{d.day} {_MONTHS[d.month - 1]}" + ("" if d.year == today.year else f" {d.year}")
    return f"{_DAYS[d.weekday()]} {out}" if weekday else out


def day_month(d: int, m: int) -> str:
    return f"{d} {_MONTHS[m - 1]}"


def ago(value, today: datetime.date | None = None) -> str:
    """"aujourd'hui", "hier", "il y a 3 jours", "il y a 2 mois"."""
    d = as_date(value)
    if d is None:
        return ""
    n = ((today or datetime.date.today()) - d).days
    if n <= 0:
        return "aujourd'hui"
    if n == 1:
        return "hier"
    if n < 30:
        return f"il y a {n} jours"
    if n < 365:
        return f"il y a {n // 30} mois"
    return f"il y a {n // 365} an{'s' if n >= 730 else ''}"


def until(value, today: datetime.date | None = None) -> tuple[str, str]:
    """(label, tone) for a due date: ("en retard de 3 j", "late"), ("demain", "soon")..."""
    d = as_date(value)
    if d is None:
        return "", ""
    today = today or datetime.date.today()
    n = (d - today).days
    if n < 0:
        return f"en retard de {-n} j", "late"
    if n == 0:
        return "aujourd'hui", "soon"
    if n == 1:
        return "demain", "soon"
    if n < 7:
        return f"{fmt_date(d, today, weekday=True)}", "soon"
    return fmt_date(d, today), ""


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def page_url(rel: str) -> str:
    return "/ui/page/" + quote(rel)


def title_of(text: str, rel: str) -> str:
    """A page's title: its first "# " heading, else its file name made readable."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return pretty_name(rel)


def pretty_name(rel: str) -> str:
    stem = rel.rsplit("/", 1)[-1]
    stem = stem[:-3] if stem.endswith(".md") else stem
    return stem.replace("-", " ").replace("_", " ").strip().capitalize() or rel


# ----------------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------------

def card(title: str, body: str, action: str = "", cls: str = "") -> str:
    head = ""
    if title or action:
        head = f'<div class="card-head"><h2>{esc(title)}</h2>{action}</div>'
    return f'<section class="card {cls}">{head}{body}</section>'


def row(main: str, sub: str = "", href: str = "", trailing: str = "", lead: str = "") -> str:
    """One line of a list: an optional icon, a title (linked), a muted line under
    it, and something on the right (a date, a button)."""
    title = f'<a href="{esc(href)}">{main}</a>' if href else f"<span>{main}</span>"
    return (f'<li class="row">{f"<span class=lead>{lead}</span>" if lead else ""}'
            f'<div class="row-main">{title}{f"<small>{sub}</small>" if sub else ""}</div>'
            f'{f"<div class=row-end>{trailing}</div>" if trailing else ""}</li>')


def rows(items: list[str]) -> str:
    return f'<ul class="rows">{"".join(items)}</ul>'


def button(label: str, href: str, kind: str = "primary") -> str:
    return f'<a class="btn {kind}" href="{esc(href)}">{label}</a>'


def post_button(csrf: str, action: str, label: str, fields: dict | None = None,
                kind: str = "primary", confirm: str = "") -> str:
    hidden = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(str(v))}">'
                     for k, v in (fields or {}).items())
    guard = f' onsubmit="return confirm({esc(json.dumps(confirm))})"' if confirm else ""
    return (f'<form class="inline" method="post" action="{esc(action)}"{guard}>'
            f'<input type="hidden" name="csrf" value="{esc(csrf)}">{hidden}'
            f'<button class="btn {kind}" type="submit">{label}</button></form>')


def chip(text: str, tone: str = "") -> str:
    return f'<span class="chip {tone}">{esc(text)}</span>'


def empty(text: str) -> str:
    return f'<p class="empty">{text}</p>'


def tabs(items: list[tuple[str, str]], active: str) -> str:
    """A segmented row of sub-pages: [(href, label)], the active one by href."""
    links = "".join(
        f'<a href="{esc(h)}"{" class=on aria-current=page" if h == active else ""}>{esc(l)}</a>'
        for h, l in items)
    return f'<nav class="tabs">{links}</nav>'


def head(title: str, subtitle: str = "", actions: str = "", crumb: str = "") -> str:
    crumb_html = f'<div class="crumb">{crumb}</div>' if crumb else ""
    sub = f'<p class="subtitle">{subtitle}</p>' if subtitle else ""
    act = f'<div class="head-actions">{actions}</div>' if actions else ""
    return f'<header class="page-head">{crumb_html}<div class="head-row"><div><h1>{esc(title)}</h1>{sub}</div>{act}</div></header>'


# ----------------------------------------------------------------------------
# Page shell
# ----------------------------------------------------------------------------

_ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
    "memory": '<path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H11v18H5.5A1.5 1.5 0 0 1 4 19.5zM13 3h5.5A1.5 1.5 0 0 1 20 4.5v15a1.5 1.5 0 0 1-1.5 1.5H13z"/>',
    "time": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
}
NAV = [("home", "/ui", "Accueil"), ("memory", "/ui/memoire", "Mémoire"),
       ("time", "/ui/temps", "Temps"), ("settings", "/ui/reglages", "Réglages")]


def _icon(name: str) -> str:
    return (f'<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>')


def page(title: str, body: str, *, section: str = "", login: str | None = None,
         flash: str = "", wide: bool = False, status: int = 200) -> HTMLResponse:
    """The full HTML document: top bar (desktop) or tab bar (phone), the
    confirmation banner when an action just happened, then the page."""
    nav = ""
    if login:
        links = "".join(
            f'<a href="{href}"{" class=on aria-current=page" if key == section else ""}>'
            f"{_icon(key)}<span>{label}</span></a>"
            for key, href, label in NAV)
        nav = (
            f'<nav class="mainnav">{links}</nav>'
            '<form class="topsearch" action="/ui/recherche" role="search">'
            '<input type="search" name="q" placeholder="Chercher…" aria-label="Chercher dans la mémoire">'
            "</form>"
        )
    banner = f'<div class="flash" role="status">{esc(flash)}</div>' if flash else ""
    doc = (
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>"
        "<meta name='color-scheme' content='light dark'>"
        f"<link rel='icon' href='{LOGO_ICON}'>"
        f"<title>{esc(title)} · Mémoire</title><style>{CSS}</style></head>"
        f"<body{' class=authed' if login else ''}><div class='topbar'><div class='inner'>"
        f"<a class='brand' href='/ui'><span class='logo' aria-hidden='true'>{LOGO_SVG}</span>Mémoire</a>"
        f"{nav}</div></div>"
        f"<main{' class=wide' if wide else ''}>{banner}{body}</main></body></html>"
    )
    return HTMLResponse(doc, status_code=status)


CSS = """
:root {
  --bg: #f6f5f2; --panel: #ffffff; --ink: #1c1c1e; --muted: #6e6e73; --faint: #a1a1a6;
  --line: #e6e4df; --accent: #3b6fb6; --accent-soft: #e8eff9; --accent-ink: #fff;
  --ok: #2f8a5b; --ok-soft: #e6f4ec; --late: #c4452d; --late-soft: #fbeae6;
  --soon: #b7791f; --soon-soft: #fbf1e1;
  --shadow: 0 1px 2px rgba(0,0,0,.04), 0 4px 16px rgba(0,0,0,.04);
  --radius: 14px; --tabbar: 0px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #141517; --panel: #1d1f23; --ink: #ececee; --muted: #9b9ba3; --faint: #6c6c74;
    --line: #2b2e34; --accent: #78a9e6; --accent-soft: #1f2b3b; --accent-ink: #0f141b;
    --ok: #5cc28c; --ok-soft: #17291f; --late: #ef8a73; --late-soft: #2e1c18;
    --soon: #e0b25c; --soon-soft: #2c2416;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 4px 16px rgba(0,0,0,.2);
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink); line-height: 1.55; font-size: 16px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
small, .muted { color: var(--muted); font-size: .86rem; }

/* top bar (desktop) and tab bar (phone) */
.topbar { position: sticky; top: 0; z-index: 20; background: color-mix(in srgb, var(--bg) 85%, transparent);
  backdrop-filter: saturate(1.4) blur(10px); -webkit-backdrop-filter: saturate(1.4) blur(10px);
  border-bottom: 1px solid var(--line); }
.topbar .inner { max-width: 980px; margin: 0 auto; padding: .65rem 1.25rem; display: flex; align-items: center; gap: 1.25rem; }
.brand { font-weight: 700; color: var(--ink); letter-spacing: -.01em; display: flex; align-items: center; gap: .5rem; }
.brand:hover { text-decoration: none; }
.brand .logo { width: 1.6rem; height: 1.6rem; display: flex; }
.brand .logo svg { width: 100%; height: 100%; }
.mainnav { display: flex; gap: .25rem; }
.mainnav a { display: flex; align-items: center; gap: .4rem; padding: .4rem .7rem; border-radius: 999px; color: var(--muted); font-size: .93rem; font-weight: 500; }
.mainnav a:hover { color: var(--ink); text-decoration: none; background: color-mix(in srgb, var(--ink) 5%, transparent); }
.mainnav a.on { color: var(--accent); background: var(--accent-soft); }
.mainnav svg { width: 1.1rem; height: 1.1rem; }
.topsearch { margin-left: auto; flex: 0 1 240px; }
.topsearch input { width: 100%; }

main { max-width: 760px; margin: 0 auto; padding: 1.5rem 1.25rem 4rem; }
main.wide { max-width: 1200px; }

/* page header */
.page-head { margin: .25rem 0 1.25rem; }
.head-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.page-head h1 { font-size: 1.65rem; letter-spacing: -.02em; line-height: 1.2; margin: 0; }
.subtitle { margin: .3rem 0 0; color: var(--muted); }
.crumb { font-size: .86rem; color: var(--muted); margin-bottom: .35rem; }
.crumb a { color: var(--muted); }
.head-actions { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }

/* cards and rows */
.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 1rem 1.15rem; margin-bottom: 1rem; }
.card-head { display: flex; align-items: baseline; justify-content: space-between; gap: .75rem; margin-bottom: .5rem; }
.card-head h2 { font-size: 1.02rem; margin: 0; letter-spacing: -.01em; }
.card-head a { font-size: .88rem; white-space: nowrap; }
.card > p:first-child { margin-top: 0; }
.card > :last-child { margin-bottom: 0; }
.rows { list-style: none; margin: 0; padding: 0; }
.row { display: flex; align-items: center; gap: .75rem; padding: .6rem 0; border-top: 1px solid var(--line); }
.row:first-child { border-top: 0; padding-top: .2rem; }
.row:last-child { padding-bottom: .1rem; }
.row .lead { font-size: 1.1rem; width: 1.5rem; text-align: center; flex-shrink: 0; }
.row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.row-main > a, .row-main > span { color: var(--ink); font-weight: 500; overflow-wrap: anywhere; }
.row-main > a:hover { color: var(--accent); text-decoration: none; }
.row-main small { overflow-wrap: anywhere; }
.row-end { flex-shrink: 0; display: flex; gap: .4rem; align-items: center; }

/* buttons, chips, banners */
.btn { display: inline-flex; align-items: center; justify-content: center; gap: .35rem; padding: .5rem .95rem;
  border-radius: 10px; border: 1px solid transparent; font: inherit; font-size: .92rem; font-weight: 550;
  cursor: pointer; line-height: 1.2; white-space: nowrap; }
.btn:hover { text-decoration: none; filter: brightness(1.06); }
.btn.primary { background: var(--accent); color: var(--accent-ink); }
.btn.secondary { background: var(--accent-soft); color: var(--accent); }
.btn.quiet { background: transparent; color: var(--muted); border-color: var(--line); }
.btn.danger { background: transparent; color: var(--late); border-color: color-mix(in srgb, var(--late) 40%, transparent); }
.btn.small { padding: .3rem .65rem; font-size: .84rem; border-radius: 8px; }
form.inline { display: inline; margin: 0; }
.chip { display: inline-block; padding: .08rem .55rem; border-radius: 999px; font-size: .78rem; font-weight: 600;
  background: color-mix(in srgb, var(--ink) 7%, transparent); color: var(--muted); white-space: nowrap; }
.chip.ok { background: var(--ok-soft); color: var(--ok); }
.chip.late { background: var(--late-soft); color: var(--late); }
.chip.soon { background: var(--soon-soft); color: var(--soon); }
.chip.accent { background: var(--accent-soft); color: var(--accent); }
.flash { background: var(--ok-soft); color: var(--ok); border-radius: 12px; padding: .7rem 1rem; margin-bottom: 1rem; font-weight: 550; }
.empty { color: var(--muted); margin: .25rem 0; }
.note { background: var(--accent-soft); border-radius: 12px; padding: .75rem 1rem; font-size: .92rem; }

/* sub-page tabs */
.tabs { display: flex; gap: .25rem; padding: .25rem; background: color-mix(in srgb, var(--ink) 5%, transparent);
  border-radius: 12px; margin: 0 0 1.25rem; overflow-x: auto; scrollbar-width: none; }
.tabs a { flex: 1; text-align: center; padding: .45rem .8rem; border-radius: 9px; color: var(--muted); font-size: .92rem; font-weight: 550; white-space: nowrap; }
.tabs a:hover { text-decoration: none; color: var(--ink); }
.tabs a.on { background: var(--panel); color: var(--ink); box-shadow: 0 1px 3px rgba(0,0,0,.08); }

/* forms */
label { display: block; font-size: .88rem; color: var(--muted); margin-bottom: .3rem; font-weight: 500; }
input[type=text], input[type=search], input[type=password], input[type=number], textarea, select {
  width: 100%; padding: .6rem .75rem; border: 1px solid var(--line); border-radius: 10px;
  background: var(--bg); color: var(--ink); font: inherit; font-size: 1rem; }
input:focus, textarea:focus, select:focus { outline: 2px solid color-mix(in srgb, var(--accent) 45%, transparent); outline-offset: 0; border-color: var(--accent); }
textarea { min-height: 6rem; resize: vertical; line-height: 1.5; }
textarea.editor { min-height: 60vh; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9rem; }
.field { margin-bottom: .9rem; }
.check { display: flex; gap: .6rem; align-items: flex-start; color: var(--ink); font-size: .95rem; margin: .5rem 0; }
.check input { margin-top: .3rem; width: 1.05rem; height: 1.05rem; flex-shrink: 0; }
.actions { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; margin-top: .75rem; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 .9rem; }
details > summary { cursor: pointer; color: var(--accent); font-weight: 550; list-style: none; }
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: "▸ "; color: var(--faint); }
details[open] > summary::before { content: "▾ "; }
details.answer { margin-top: .4rem; }
details.answer form { margin-top: .5rem; }
.menu { position: relative; }
.menu > summary { list-style: none; }
.menu > summary::before { content: none; }
.menu[open] > summary::before { content: none; }
.menu-panel { position: absolute; right: 0; top: calc(100% + .35rem); z-index: 15; min-width: 240px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow);
  padding: .4rem; display: flex; flex-direction: column; }
.menu-panel > a, .menu-panel button.link { display: block; text-align: left; padding: .5rem .65rem; border-radius: 8px;
  color: var(--ink); background: none; border: 0; font: inherit; font-size: .93rem; cursor: pointer; width: 100%; }
.menu-panel > a:hover, .menu-panel button.link:hover { background: color-mix(in srgb, var(--ink) 6%, transparent); text-decoration: none; }
.menu-panel button.link.danger { color: var(--late); }
.menu-panel hr { border: 0; border-top: 1px solid var(--line); margin: .3rem 0; }

/* tiles (memory home), people cards, stats */
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); gap: .75rem; margin-bottom: 1rem; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow);
  padding: .9rem 1rem; color: var(--ink); display: flex; flex-direction: column; gap: .15rem; }
.tile:hover { text-decoration: none; border-color: var(--accent); }
.tile b { font-size: 1rem; }
.tile .n { font-size: 1.5rem; font-weight: 700; letter-spacing: -.02em; }
.tile small { line-height: 1.35; }
.people { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: .75rem; }
.person { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow);
  overflow: hidden; color: var(--ink); display: flex; flex-direction: column; }
.person:hover { text-decoration: none; border-color: var(--accent); }
.person img { width: 100%; height: 140px; object-fit: cover; display: block; }
.person .avatar { height: 64px; display: flex; align-items: center; padding: 0 1rem; }
.person .avatar span { width: 2.6rem; height: 2.6rem; border-radius: 50%; display: grid; place-items: center;
  background: var(--accent-soft); color: var(--accent); font-weight: 700; }
.person .body { padding: .75rem 1rem .9rem; display: flex; flex-direction: column; gap: .2rem; }
.person b { font-size: 1rem; }
.person small { display: block; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: .75rem; margin-bottom: 1rem; }
.stat { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 1rem; text-align: center; }
.stat b { display: block; font-size: 1.7rem; letter-spacing: -.02em; }
.stat span { color: var(--muted); font-size: .86rem; }
.kv { width: 100%; border-collapse: collapse; font-size: .93rem; }
.kv th { text-align: left; font-weight: 500; color: var(--muted); padding: .35rem .75rem .35rem 0; white-space: nowrap; vertical-align: top; }
.kv td { padding: .35rem 0; }
.steps { padding-left: 1.2rem; margin: .5rem 0; }
.steps li { margin: .3rem 0; }
code { background: color-mix(in srgb, var(--ink) 8%, transparent); padding: .08rem .35rem; border-radius: 5px; font-size: .88em; overflow-wrap: anywhere; }

/* rendered pages */
article { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 1.25rem 1.4rem; overflow-x: auto; margin-bottom: 1rem; }
article > :first-child { margin-top: 0; }
article > :last-child { margin-bottom: 0; }
article h1 { font-size: 1.35rem; } article h2 { font-size: 1.15rem; margin-top: 1.6rem; }
article h3 { font-size: 1rem; }
article h1, article h2, article h3 { letter-spacing: -.01em; }
article pre { background: color-mix(in srgb, var(--ink) 6%, transparent); padding: 1rem; border-radius: 10px; overflow-x: auto; }
article pre code { background: none; padding: 0; }
article table { border-collapse: collapse; width: 100%; font-size: .9rem; }
article th, article td { border: 1px solid var(--line); padding: .45rem .7rem; text-align: left; }
article th { background: color-mix(in srgb, var(--ink) 4%, transparent); }
article img { max-width: 100%; height: auto; border-radius: 10px; }
article blockquote { border-left: 3px solid var(--line); margin: 1rem 0; padding-left: 1rem; color: var(--muted); }
article ul, article ol { padding-left: 1.3rem; }
.meta { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; margin-top: .5rem; }

/* timeline */
.month h2 { text-transform: capitalize; }
.tl { list-style: none; margin: 0; padding: 0; }
.tl li { display: flex; gap: .65rem; padding: .4rem 0; border-top: 1px solid var(--line); font-size: .95rem; }
.tl li:first-child { border-top: 0; }
.tl .d { color: var(--muted); font-variant-numeric: tabular-nums; width: 2.2rem; flex-shrink: 0; }

/* logs */
.logrow { padding: .45rem 0; border-top: 1px solid var(--line); }
.logrow:first-child { border-top: 0; }
.logrow pre { margin: .2rem 0 0; white-space: pre-wrap; word-break: break-word; font-size: .82rem; }
.lvl-ERROR, .lvl-WARNING { color: var(--late); }

/* graph */
.graph-controls { display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: center; margin-bottom: .75rem; }
.graph-controls input[type=search] { flex: 1 1 220px; max-width: 360px; }
.graph-controls label { display: inline-flex; align-items: center; gap: .35rem; margin: 0; color: var(--ink); }
.graph-wrap { position: relative; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); overflow: hidden; height: calc(100vh - 290px); min-height: 420px; }
.graph-wrap svg { width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
.graph-wrap svg:active { cursor: grabbing; }
.graph-legend { display: flex; flex-wrap: wrap; gap: .3rem .9rem; margin: .6rem 0 0; font-size: .82rem; color: var(--muted); }
.graph-legend span::before { content: ""; display: inline-block; width: .7rem; height: .7rem; border-radius: 50%;
  background: var(--c); margin-right: .35rem; vertical-align: -.05rem; }
.graph-tip { position: absolute; pointer-events: none; max-width: 280px; padding: .45rem .6rem; background: var(--panel);
  color: var(--ink); border: 1px solid var(--line); border-radius: 10px; box-shadow: var(--shadow);
  font-size: .82rem; line-height: 1.4; display: none; z-index: 2; }
.graph-tip b { display: block; font-size: .88rem; }
.graph-stats { font-size: .84rem; color: var(--muted); }

.login-wrap { max-width: 380px; margin: 14vh auto; text-align: center; }

@media (max-width: 720px) {
  body.authed { --tabbar: calc(62px + env(safe-area-inset-bottom)); padding-bottom: var(--tabbar); }
  /* a backdrop-filter would make the topbar the containing block of the fixed tab bar */
  .topbar { backdrop-filter: none; -webkit-backdrop-filter: none; background: var(--bg); }
  .topbar .inner { padding: .55rem 1rem; gap: .75rem; }
  .topsearch { flex: 1 1 auto; }
  .mainnav { position: fixed; left: 0; right: 0; bottom: 0; z-index: 30; justify-content: space-around; gap: 0;
    background: color-mix(in srgb, var(--panel) 92%, transparent); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border-top: 1px solid var(--line); padding: .35rem .25rem calc(.35rem + env(safe-area-inset-bottom)); }
  .mainnav a { flex-direction: column; gap: .15rem; font-size: .72rem; padding: .3rem .5rem; border-radius: 10px; flex: 1; }
  .mainnav a.on { background: none; }
  .mainnav svg { width: 1.4rem; height: 1.4rem; }
  main { padding: 1.1rem 1rem 2.5rem; }
  .page-head h1 { font-size: 1.4rem; }
  .card { padding: .9rem 1rem; }
  article { padding: 1rem 1.05rem; }
  .grid2 { grid-template-columns: 1fr; }
  .row { flex-wrap: wrap; row-gap: .35rem; }
  .row-main { flex: 1 1 11rem; }
  .row-end { margin-left: auto; }
  .graph-wrap { height: 62vh; min-height: 340px; }
  .menu-panel { right: auto; left: 0; }
}
"""
