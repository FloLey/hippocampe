"""The MCP tools themselves (auth disabled, as in local development)."""

import os

os.environ.setdefault("WIKI_AUTH_DISABLED", "1")

from wiki_server import server  # noqa: E402


def _tool(name):
    t = getattr(server, name)
    return getattr(t, "fn", t)  # FastMCP wraps the function in a tool object


def test_complete_closes_item(wiki, write):
    write("temporal/2026-12-31-x.md", "---\ntype: todo\nstatus: active\ndue: 2026-12-31\n---\n\nX\n")
    assert _tool("complete")("2026-12-31-x") == "Closed temporal/2026-12-31-x.md."
    assert "status: done" in (wiki / "temporal/2026-12-31-x.md").read_text()


def test_complete_unknown_lists_active(wiki, write):
    write("temporal/a.md", "---\ntype: todo\nstatus: active\n---\n\nA\n")
    out = _tool("complete")("temporal/zzz.md")
    assert "No temporal item" in out and "temporal/a.md" in out


def test_remember_masks_secret_before_writing(wiki):
    out = _tool("remember")("Ma clé FileVault : ABCD-EFGH-JKLM-NPQR-STUV-WXYZ",
                            summary="RN 90.02.03-123.45")
    assert "Remembered as" in out and "Masked" in out
    for f in (wiki / "short_term").rglob("*.md"):
        text = f.read_text()
        assert "ABCD-EFGH" not in text and "90.02.03" not in text
    entry = next((wiki / "short_term/entries").glob("*.md")).read_text()
    assert "[MASQUÉ: clé de récupération]" in entry


def test_remember_plain_has_no_warning(wiki):
    out = _tool("remember")("Nina aime les films d'animation.")
    assert "Warning" not in out


def test_read_only_tools_are_annotated():
    import asyncio

    tools = asyncio.run(server.mcp.get_tools())
    for name in ("prime", "read", "search", "recall"):
        assert tools[name].annotations.readOnlyHint is True
    assert tools["complete"].annotations.destructiveHint is False


def _people(write):
    write("long_term/people/bastien-dupont.md", "# Bastien Dupont\n")
    write("long_term/people/bastien-martin.md", "# Bastien Martin\n")
    write("long_term/people/nina.md", "# Nina\n")
    write("long_term/index.md", "# Index\n\n- [Nina](people/nina.md): compagne de Valentin\n")


def test_remember_flags_shared_first_name(wiki, write):
    _people(write)
    out = _tool("remember")("Bastien vient dîner samedi.")
    assert "Unclear" in out and "bastien-dupont" in out and "bastien-martin" in out
    assert "ask the user now" in out


def test_remember_flags_relation_word_and_lists_related(wiki, write):
    _people(write)
    out = _tool("remember")("Ma copine et Nina vont au cinéma.")
    assert "long_term/people/nina.md (compagne de Valentin)" in out
    assert "'ma copine'" in out


def test_remember_clear_note_has_no_doubt(wiki, write):
    _people(write)
    out = _tool("remember")("Bastien Martin vient dîner samedi.")
    assert "bastien-martin" in out and "Unclear" not in out
