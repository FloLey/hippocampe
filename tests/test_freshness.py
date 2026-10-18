"""Fact freshness: last-change dates from git, stale projects become questions."""

import datetime
import os
import subprocess

from wiki_server import questions
from wiki_server.dream import pipeline
from wiki_server.recall import recall
from wiki_server.store import last_changed


def _commit(root, date):
    env = {**os.environ, "GIT_AUTHOR_DATE": f"{date}T12:00:00", "GIT_COMMITTER_DATE": f"{date}T12:00:00"}
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "x"], check=True, env=env)


def test_last_changed_and_stale_projects(wiki, write):
    write("long_term/projects/vieux.md", "# Vieux projet\n\nStatut : en cours.\n")
    write("long_term/projects/fini.md", "# Fini\n\nLivré.\n")
    _commit(wiki, "2026-03-01")
    write("long_term/projects/frais.md", "# Frais\n\nStatut : en cours.\n")
    _commit(wiki, "2026-10-01")
    dates = last_changed()
    assert dates["long_term/projects/vieux.md"] == "2026-03-01"
    assert dates["long_term/projects/frais.md"] == "2026-10-01"
    stale = pipeline._stale_pages(datetime.date(2026, 10, 17))
    assert stale == [("long_term/projects/vieux.md", "2026-03-01")]  # no status word in "fini"
    assert "(last updated 2026-03-01)" in recall("vieux projet")


def test_answered_duplicates_are_not_reported(wiki, write):
    write("long_term/projects/useless.md", "# U\n")
    write("long_term/projects/useless-cailloux.md", "# A\n")
    pages = pipeline._ltm_pages()
    assert pipeline._open_duplicate_groups(pages)
    group = pipeline._duplicate_groups(pages)[0]
    rel, content = questions.build_question("dup ?", key="dup:" + "|".join(sorted(group)))
    write(rel, content)
    questions.mark_answered(rel)
    assert pipeline._open_duplicate_groups(pages) == []
