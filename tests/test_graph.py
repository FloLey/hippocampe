"""The link graph behind the console's graph view and backlinks."""

import os

from wiki_server import graph

os.environ.setdefault("WIKI_AUTH_DISABLED", "1")


def test_resolve_link_forms():
    src = "long_term/projects/x.md"
    assert graph.resolve_link(src, "../people/nina.md") == "long_term/people/nina.md"
    assert graph.resolve_link(src, "y.md") == "long_term/projects/y.md"
    assert graph.resolve_link(src, "./y.md#section") == "long_term/projects/y.md"
    assert graph.resolve_link(src, "/long_term/places/p.md") == "long_term/places/p.md"
    assert graph.resolve_link(src, "https://example.com/a.md") is None
    assert graph.resolve_link(src, "mailto:a@b.c") is None
    assert graph.resolve_link(src, "#top") is None
    assert graph.resolve_link(src, "image.png") is None
    assert graph.resolve_link(src, "../../../../etc/passwd.md") is None


def _wiki(write):
    write("long_term/index.md", "# LTM\n\n- [nina](people/nina.md) : Copine de Valentin\n")
    write("long_term/people/nina.md", "---\ntags: [x]\n---\n\n# Nina\n\n[Projet](../projects/led.md)\n")
    write("long_term/projects/led.md",
          "# LED crochet\n\n[Nina](../people/nina.md), [encore](../people/nina.md), "
          "[soi](led.md), ![img](../people/nina.md), [web](https://x.y), "
          "[perdu](../people/camille.md)\n")
    write("long_term/concepts/seule.md", "# Seule\n\nAucun lien.\n")
    write("long_term/private/secret.md", "# S\n\n[M](../people/nina.md)\n")
    write("temporal/t.md", "---\ntype: todo\n---\n\nVoir [Nina](../long_term/people/nina.md)\n")


def test_build_graph(wiki, write):
    _wiki(write)
    g = graph.build_graph()
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"long_term/people/nina.md", "long_term/projects/led.md", "long_term/concepts/seule.md"}
    # Duplicate links, self links and images collapse to one edge each way.
    assert {(e["source"], e["target"]) for e in g["edges"]} == {
        ("long_term/people/nina.md", "long_term/projects/led.md"),
        ("long_term/projects/led.md", "long_term/people/nina.md"),
    }
    assert g["broken"] == [{"source": "long_term/projects/led.md", "target": "long_term/people/camille.md"}]
    assert g["orphans"] == ["long_term/concepts/seule.md"]
    nina = next(n for n in g["nodes"] if n["id"] == "long_term/people/nina.md")
    assert nina["label"] == "Nina" and nina["category"] == "people"
    assert nina["degree"] == 2 and nina["backlinks"] == 1
    assert nina["description"] == "Copine de Valentin"


def test_build_graph_with_temporal(wiki, write):
    _wiki(write)
    g = graph.build_graph(include_temporal=True)
    assert ("temporal/t.md", "long_term/people/nina.md") in {(e["source"], e["target"]) for e in g["edges"]}
    assert next(n for n in g["nodes"] if n["id"] == "temporal/t.md")["category"] == "temporal"


def test_backlinks(wiki, write):
    _wiki(write)
    assert graph.backlinks("long_term/people/nina.md") == [("long_term/projects/led.md", "LED crochet")]
    assert graph.backlinks("long_term/concepts/seule.md") == []


def test_duplicates_exposed(wiki, write):
    write("long_term/people/nina.md", "# Nina\n")
    write("long_term/people/nina-durand.md", "# Nina Durand\n")
    g = graph.build_graph()
    assert ["long_term/people/nina-durand.md", "long_term/people/nina.md"] in g["duplicates"]


def test_graph_routes_registered():
    from wiki_server import server

    paths = {getattr(r, "path", None) for r in server.mcp._additional_http_routes}
    assert {"/ui/graph", "/ui/graph.json"} <= paths
