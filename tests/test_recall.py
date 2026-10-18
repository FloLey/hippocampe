"""recall(topic): the bundle for one topic."""

from wiki_server import questions
from wiki_server.recall import recall


def test_recall_bundles_page_links_items_notes_questions(wiki, write):
    write("long_term/places/islande-du-sud.md", "# Islande du Sud\n\nPays visité en octobre 2026.\n")
    write("long_term/projects/voyage-islande.md",
          "# Voyage Islande\n\nVoir [Islande](../places/islande-du-sud.md). Reykjavik, Akureyri.\n")
    write("long_term/people/nina.md", "# Nina\n\nInfirmière.\n")
    write("temporal/2026-10-24-retour-islande.md",
          "---\ntype: event\nstatus: active\ndue: 2026-10-24\n---\n\nRetour de Islande\n")
    write("short_term/entries/2026-10-17-skogafoss.md", "Visite de Skogafoss en Islande.\n")
    rel, content = questions.build_question("Islande : 11 ou 10 octobre ?")
    write(rel, content)
    out = recall("Islande")
    assert "## long_term/places/islande-du-sud.md" in out
    assert "Linked from: long_term/projects/voyage-islande.md" in out
    assert "Retour de Islande" in out and "temporal/2026-10-24-retour-islande.md" in out
    assert "Skogafoss" in out and "not consolidated" in out
    assert "11 ou 10 octobre" in out
    assert "Infirmière" not in out


def test_recall_nothing(wiki):
    assert "Nothing in memory" in recall("xylophone")


def test_recall_cuts_long_page_to_matching_sections(wiki, write):
    filler = "- détail\n" * 400
    write("long_term/self/identity.md",
          f"# Identity\n\n## Musique\n\n{filler}\n## Sport\n\nCourse à pied, 10 km.\n")
    out = recall("course à pied")
    assert "10 km" in out and "- détail" not in out
    assert "(Other sections: Musique)" in out
