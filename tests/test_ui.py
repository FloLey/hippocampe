"""The web console: every page renders, actions are protected, old addresses
still work, and the console speaks French throughout."""

import re

import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

from wiki_server.ui import register_ui

PAGES = [
    "/ui", "/ui/recherche", "/ui/recherche?q=Lina", "/ui/questions",
    "/ui/memoire", "/ui/memoire/people", "/ui/memoire/projects", "/ui/memoire/attente", "/ui/graphe",
    "/ui/page/long_term/people/lina-martin.md", "/ui/page/temporal/2030-01-15-envoyer-le-devis.md",
    "/ui/page/questions/2026-10-01-lina-ou-camille.md", "/ui/page/long_term/nope.md",
    "/ui/modifier?path=long_term/people/lina-martin.md", "/ui/nouvelle", "/ui/fichiers",
    "/ui/temps", "/ui/temps/fil", "/ui/temps/annee",
    "/ui/reglages/reve", "/ui/reglages/connexions", "/ui/reglages/avance", "/ui/reglages/logs",
]
OLD = {"/ui/graph": "/ui/graphe", "/ui/people": "/ui/memoire/people", "/ui/timeline": "/ui/temps/fil",
       "/ui/wrapped?year=2025": "/ui/temps/annee?year=2025", "/ui/dream": "/ui/reglages/reve",
       "/ui/prompts": "/ui/reglages/avance", "/ui/connections": "/ui/reglages/connexions",
       "/ui/logs": "/ui/reglages/logs", "/ui?dir=long_term": "/ui/fichiers?dir=long_term",
       "/ui/reglages": "/ui/reglages/reve"}
# English words of the old console that must not come back as visible text.
ENGLISH = re.compile(r">\s*(Overview|Edit|Back|Save|Delete|Cancel|Folders|Pages that|New page|"
                     r"Logout|Reports|Run a dream|Copy markdown)\s*<")


@pytest.fixture
def client(wiki, write):
    write("long_term/people/lina-martin.md",
          "# Lina Martin\n\nNée le 28 janvier 1991.\n\nAime la randonnée.\n\n"
          "Travaille avec [Camille](camille-durand.md).\n")
    write("long_term/people/camille-durand.md", "# Camille Durand\n\nAmie de [Lina](lina-martin.md).\n")
    write("long_term/projects/potager.md", "# Potager\n\nUn potager sur le toit.\n")
    write("long_term/index.md", "# Index\n\n- [Potager](projects/potager.md) : un potager sur le toit\n")
    write("temporal/2030-01-15-envoyer-le-devis.md",
          "---\ntype: todo\ncreated: 2026-10-01\nstatus: active\ndue: 2030-01-15\n---\n\nEnvoyer le devis\n")
    write("questions/2026-10-01-lina-ou-camille.md",
          "---\nkey: q:test\ncreated: 2026-10-01\nstatus: open\n---\n\nLe potager, c'est avec Lina ou Camille ?\n")
    mcp = FastMCP("test")
    register_ui(mcp, owner_login="someone", client_id="", client_secret="",
                public_url="http://testserver", secret_key="test-key", auth_disabled=True)
    return TestClient(mcp.http_app(), follow_redirects=False)


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_every_page_renders_in_french(client):
    for url in PAGES:
        r = client.get(url)
        assert r.status_code == 200, url
        assert "Accueil" in r.text and "Réglages" in r.text, url
        assert not ENGLISH.search(r.text), (url, ENGLISH.search(r.text).group(0))


def test_home_shows_what_needs_the_user(client):
    html = client.get("/ui").text
    assert "Le potager, c&#x27;est avec Lina ou Camille ?" in html  # the open question
    assert "Noter quelque chose" in html


def test_page_has_title_category_and_backlinks(client):
    html = client.get("/ui/page/long_term/people/lina-martin.md").text
    assert "<h1>Lina Martin</h1>" in html
    assert "Personnes" in html and "Camille Durand" in html  # category chip, backlink


def test_people_cards(client):
    html = client.get("/ui/memoire/people").text
    assert "Lina Martin" in html and "28 janv." in html


def test_search_finds_a_page(client):
    html = client.get("/ui/recherche?q=potager").text
    assert "/ui/page/long_term/projects/potager.md" in html


def test_actions_need_the_csrf_token(client):
    assert client.post("/ui/note", data={"text": "x"}).status_code == 403
    assert client.post("/ui/temporal/done", data={"path": "temporal/x.md"}).status_code == 403


def test_quick_note_is_kept_and_confirmed(client, wiki):
    token = _csrf(client.get("/ui").text)
    r = client.post("/ui/note", data={"csrf": token, "text": "Camille a adopté un chat."})
    assert r.status_code == 303 and r.headers["location"].startswith("/ui?ok=")
    assert any("chat" in p.read_text() for p in (wiki / "short_term/entries").glob("*.md"))
    assert "Noté ✓" in client.get(r.headers["location"]).text


def test_answer_a_question(client, wiki):
    token = _csrf(client.get("/ui").text)
    r = client.post("/ui/questions/answer", data={
        "csrf": token, "path": "questions/2026-10-01-lina-ou-camille.md", "answer": "Avec Camille.",
        "back": "https://evil.example/"})
    assert r.status_code == 303 and r.headers["location"].startswith("/ui?")  # never off-site
    assert "status: answered" in (wiki / "questions/2026-10-01-lina-ou-camille.md").read_text()
    assert any("Avec Camille" in p.read_text() for p in (wiki / "short_term/entries").glob("*.md"))


def test_mark_a_todo_done(client, wiki):
    token = _csrf(client.get("/ui/temps").text)
    r = client.post("/ui/temporal/done", data={
        "csrf": token, "path": "temporal/2030-01-15-envoyer-le-devis.md", "back": "/ui/temps"})
    assert r.status_code == 303 and r.headers["location"].startswith("/ui/temps?ok=")
    assert "status: done" in (wiki / "temporal/2030-01-15-envoyer-le-devis.md").read_text()


def test_new_page_then_edit(client, wiki):
    token = _csrf(client.get("/ui/nouvelle").text)
    r = client.post("/ui/nouvelle", data={"csrf": token, "cat": "places", "title": "Café du Port"})
    assert r.headers["location"] == "/ui/modifier?path=long_term/places/cafe-du-port.md"
    assert (wiki / "long_term/places/cafe-du-port.md").read_text().startswith("# Café du Port")
    r = client.post("/ui/save", data={"csrf": token, "path": "long_term/places/cafe-du-port.md",
                                      "content": "# Café du Port\r\n\r\nSur le quai.\r\n"})
    assert r.status_code == 303
    assert (wiki / "long_term/places/cafe-du-port.md").read_text() == "# Café du Port\n\nSur le quai.\n"


def test_flash_cannot_be_forged(client):
    assert "Pirate" not in client.get("/ui?ok=Pirate").text


def test_old_addresses_redirect(client):
    for old, new in OLD.items():
        r = client.get(old)
        assert r.status_code in (302, 307), old
        assert r.headers["location"] == new, old


def test_logo_is_served(client):
    r = client.get("/ui/logo.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")
