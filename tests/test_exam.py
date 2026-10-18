"""The exam: known answers, checked by retrieval."""

from wiki_server import exam


def test_exam_scores_and_tracks_history(wiki, write):
    write("long_term/places/islande.md", "# Islande\n\nVoyage du 11 au 24 octobre 2026.\n")
    write(exam.EXAM_FILE, "# Examen\n\n- Quand est le voyage en Islande ? → 11, 24, octobre\n"
                          "- Quelle est la couleur du vélo ? → rouge\n")
    passed, failures = exam.run()
    assert passed == 1 and failures == [("Quelle est la couleur du vélo ?", ["rouge"])]
    section, files = exam.report_section("2026-10-17")
    assert "1/2 questions retrouvées." in section and "❌ Quelle est la couleur du vélo" in section
    write(exam.HISTORY_FILE, files[exam.HISTORY_FILE])
    (wiki / "long_term/places/islande.md").write_text("# Islande\n\nVoyage.\n")
    section, _ = exam.report_section("2026-10-18")
    assert "⚠️ en baisse (avant : 1/2)" in section


def test_exam_answers_are_not_searchable(wiki, write):
    from wiki_server.recall import recall

    write(exam.EXAM_FILE, "- Couleur du vélo ? → rouge\n")
    assert recall("vélo rouge").startswith("Nothing in memory")
