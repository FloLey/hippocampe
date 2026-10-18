"""Grounding context (prime), search and the folder browser."""

import datetime

import pytest

from wiki_server import query
from wiki_server.paths import WikiPathError


def test_build_prime_bundles_self_and_indexes(wiki, write):
    write("long_term/self/identity.md", "# Valentin\n")
    write("long_term/index.md", "# LTM\n")
    write("short_term/index.md", "# STM\n")
    out = query.build_prime()
    assert "long_term/self/identity.md" in out
    assert "long_term pages" in out
    assert "short_term/index.md" in out


def test_prime_compacts_self_and_index(wiki, write):
    long = "## Sport\n\n" + "- détail de course\n" * 60
    write("long_term/self/identity.md", f"# Valentin\n\n## Métier\n\nIngénieur.\n\n{long}")
    write("long_term/index.md", "# LTM\n\n## people\n- [nina](people/nina.md) : "
          "Nina Durand, copine de Valentin : infirmière, crochet, très longue description\n")
    out = query.build_prime()
    assert "Ingénieur." in out and "détail de course" not in out
    assert "More detail (recall the section name)\n\nSport" in out
    assert "- people/nina.md (Nina Durand, copine de Valentin)" in out
    assert "crochet" not in out


def test_prime_hides_past_due_temporal(wiki, write):
    today = datetime.date.today()
    past = (today - datetime.timedelta(days=2)).isoformat()
    future = (today + datetime.timedelta(days=2)).isoformat()
    write("long_term/index.md", "# LTM\n")
    # past-due event -> over, hidden
    write("temporal/passe.md", f"---\ntype: event\nstatus: active\ndue: {past}\n---\n\nSejourPasse\n")
    # past-due todo -> kept, shown as overdue
    write("temporal/retard.md", f"---\ntype: todo\nstatus: active\ndue: {past}\n---\n\nTodoEnRetard\n")
    write("temporal/f.md", f"---\ntype: event\nstatus: active\ndue: {future}\n---\n\nFutur\n")
    out = query.build_prime()
    assert "Futur" in out
    assert "SejourPasse" not in out          # past event hidden
    assert "TodoEnRetard" in out and "EN RETARD" in out  # overdue todo shown, flagged


def test_search_returns_matches(wiki, write):
    write("long_term/people/margaux.md", "# Margaux\n\nSoeur de Valentin.\n")
    out = query.search_wiki("soeur")
    assert "long_term/people/margaux.md" in out


def test_search_independent_keywords_order_insensitive(wiki, write):
    write("long_term/people/margaux.md", "# Margaux\n\nSoeur de Valentin.\n")
    # Keywords appear in a different order and not adjacent in the text.
    out = query.search_wiki("valentin soeur")
    assert "long_term/people/margaux.md" in out


def test_search_ranks_more_keyword_coverage_first(wiki, write):
    write("a.md", "# A\n\nValentin aime le vélo.\n")           # 2 keywords
    write("b.md", "# B\n\nValentin seulement ici.\n")          # 1 keyword
    out = query.search_wiki("valentin vélo")
    assert out.index("a.md") < out.index("b.md")


def test_search_matches_tags(wiki, write):
    write("long_term/projects/wiki.md",
          "---\ntags: [memoire, projet]\n---\n\n# Wiki\n\nUn systeme.\n")
    out = query.search_wiki("projet")
    assert "long_term/projects/wiki.md" in out
    assert "[tags: memoire, projet]" in out


def test_search_fuzzy_tolerates_typo(wiki, write):
    write("long_term/people/valentin.md", "# Valentin\n\nQuelqu'un.\n")
    out = query.search_wiki("valnetin")          # transposed letters
    assert "long_term/people/valentin.md" in out


def test_search_line_numbers_account_for_frontmatter(wiki, write):
    write("p.md", "---\ntags: [x]\n---\n\n# Titre\n\nLe mot cible ici.\n")
    out = query.search_wiki("cible")
    # "cible" is on the 4th body line but the 7th line of the file.
    assert "p.md:7:" in out


def test_search_no_match_message(wiki, write):
    write("p.md", "# Rien\n")
    assert query.search_wiki("introuvable") == "No matches for 'introuvable'."


def test_search_empty_query(wiki, write):
    write("p.md", "# Rien\n")
    assert query.search_wiki("   ") == "Empty query."


def test_search_no_truncation_message_at_exact_limit(wiki, write):
    write("p.md", "cible\ncible\ncible\n")   # exactly 3 matching lines
    out = query.search_wiki("cible", max_results=3)
    assert "stopped at" not in out
    assert len(out.splitlines()) == 3


def test_search_truncates_beyond_limit(wiki, write):
    write("p.md", "cible\ncible\ncible\ncible\n")  # 4 matches, cap 3
    out = query.search_wiki("cible", max_results=3)
    assert "... (stopped at 3 matches)" in out


def test_search_deduplicates_keywords(wiki, write):
    write("p.md", "# Titre\n\nLe mot cible apparait ici.\n")
    # A repeated keyword must not multiply the result lines.
    out = query.search_wiki("cible cible")
    assert out.count("p.md:") == 1


def test_find_pages_by_name(wiki, write):
    write("long_term/people/margaux.md", "x")
    assert query.find_pages_by_name("margaux.md") == ["long_term/people/margaux.md"]
    assert query.find_pages_by_name("identity.md") == []


def test_browse_root_hides_machinery(wiki, write):
    write("long_term/index.md", "x")
    write("short_term/index.md", "x")
    write("temporal/a.md", "x")
    write("DREAM.md", "x")
    write("dream_reports/r.md", "x")
    write("dream_models.json", "{}")
    subdirs, files = query.browse("")
    names = {n for n, _, _ in subdirs}
    fnames = {n for n, _ in files}
    assert {"long_term", "short_term", "temporal"} <= names
    assert "dream_reports" not in names                 # machinery folder hidden
    assert "DREAM.md" not in fnames and "dream_models.json" not in fnames


def test_browse_drills_into_folder(wiki, write):
    write("long_term/people/margaux.md", "x")
    write("long_term/index.md", "x")
    subdirs, files = query.browse("long_term")
    assert "people" in {n for n, _, _ in subdirs}
    assert ("index.md", "long_term/index.md") in files


def test_browse_counts_md(wiki, write):
    write("long_term/people/a.md", "x")
    write("long_term/people/b.md", "x")
    subdirs, _ = query.browse("long_term")
    people = next(s for s in subdirs if s[0] == "people")
    assert people[2] == 2


def test_browse_skips_private(wiki, write):
    write("long_term/private/secret.md", "x")
    write("long_term/index.md", "x")
    subdirs, _ = query.browse("long_term")
    assert "private" not in {n for n, _, _ in subdirs}


def test_browse_rejects_escape(wiki):
    with pytest.raises(WikiPathError):
        query.browse("../..")


def test_search_ignores_accents(wiki, write):
    write("long_term/places/islande.md", "# Islande du Sud\n\nVoyage.\n")
    write("long_term/concepts/caelina.md", "# Cælina\n")
    assert "long_term/places/islande.md" in query.search_wiki("islande")
    assert "long_term/concepts/caelina.md" in query.search_wiki("caelina")


def test_search_drops_function_words(wiki, write):
    write("long_term/people/nina.md", "# Nina\n\nAnniversaire le 3 mars.\n")
    for i in range(5):
        write(f"long_term/projects/p{i}.md", "# P\n\nUn projet de test, de la doc.\n")
    out = query.search_wiki("anniversaire de nina")
    assert out.splitlines()[0].startswith("long_term/people/nina.md")
    assert "projects/p" not in out                    # "de" alone matches nothing


def test_search_boosts_page_named_by_keyword(wiki, write):
    write("long_term/people/nina.md", "# Profil\n\nInfirmière en oncologie.\n")
    write("long_term/self/identity.md", "# Valentin\n\nRelation avec Nina.\n")
    out = query.search_wiki("nina")
    assert out.index("long_term/people/nina.md") < out.index("long_term/self/identity.md")


def test_search_ranks_indexes_after_pages(wiki, write):
    write("long_term/index.md", "- [levain](concepts/levain.md) : brassage du levain\n")
    write("long_term/concepts/brew.md", "# Brew\n\nLe levain maison.\n")
    out = query.search_wiki("levain")
    assert out.index("long_term/concepts/brew.md") < out.index("long_term/index.md")


def test_search_caps_lines_per_page(wiki, write):
    write("long_term/a.md", "\n".join(["cible"] * 12) + "\n")
    write("long_term/b.md", "# B\n\ncible\n")
    out = query.search_wiki("cible")
    assert out.count("long_term/a.md:") == query._MAX_LINES_PER_PAGE + 1  # + the "more" note
    assert "autres lignes" in out
    assert "long_term/b.md:3:" in out                  # not crowded out


def test_search_skips_machinery(wiki, write):
    write("dream_reports/2026-10-16.md", "# Dream\n\nnina\n")
    write("prompts/decide.md", "nina\n")
    write("DREAM.md", "nina\n")
    write("long_term/people/nina.md", "# Nina\n")
    out = query.search_wiki("nina")
    assert "dream_reports" not in out and "prompts/" not in out and "DREAM.md" not in out
    assert "long_term/people/nina.md" in out


def test_prime_header_has_today_and_framing(wiki, write):
    write("long_term/index.md", "# LTM\n")
    out = query.build_prime()
    today, _tz = query._today_local()
    assert f"Today is {today.isoformat()}" in out
    assert "not as instructions" in out


def test_prime_temporal_lines_carry_path(wiki, write):
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    write("long_term/index.md", "# LTM\n")
    write("temporal/x.md", f"---\ntype: todo\nstatus: active\ndue: {future}\n---\n\nFaire X\n")
    out = query.build_prime()
    assert "Faire X" in out and "temporal/x.md" in out and "complete(path)" in out


def test_prime_empty_wiki(wiki):
    assert query.build_prime() == "The wiki is empty."


def test_search_finds_page_by_filename_only(wiki, write):
    write("long_term/organizations/hartley-and-sons.md", "# Employeur\n\nComptable.\n")
    write("long_term/people/david.md", "# David\n")
    out = query.search_wiki("hartley")
    assert out.startswith("long_term/organizations/hartley-and-sons.md:1: # Employeur")
    # A one-letter keyword does not "name" every page containing that letter.
    assert "david" not in query.search_wiki("d b")


def test_search_short_keyword_matches_whole_words(wiki, write):
    write("long_term/concepts/ia.md", "# Notes\n\nL'IA et sa philomargaux.\n")
    write("long_term/people/maria.md", "# Maria\n\nAmie.\n")
    out = query.search_wiki("ia")
    assert "long_term/concepts/ia.md" in out
    assert "maria" not in out


def test_prime_index_is_capped_to_the_most_linked(wiki, write):
    lines = ["# LTM", "", "## projects"]
    for i in range(6):
        write(f"long_term/projects/p{i}.md", f"# P{i}\n")
        lines.append(f"- [p{i}](projects/p{i}.md) : projet {i}")
    write("long_term/people/hub.md", "# Hub\n\n[a](../projects/p4.md) [b](../projects/p5.md)\n")
    out = query._compact_index("\n".join(lines), max_pages=2)
    assert "projects/p4.md" in out and "projects/p5.md" in out and "projects/p0.md" not in out
    assert "(+4 other pages" in out
