"""Open questions: filed by the dream when unsure, shown in prime, closed by complete."""

import datetime
import os

os.environ.setdefault("WIKI_AUTH_DISABLED", "1")

from wiki_server import query, questions, server  # noqa: E402
from wiki_server.dream import models, pipeline  # noqa: E402


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _tool(name):
    t = getattr(server, name)
    return getattr(t, "fn", t)


def _stm(write, *names):
    write("short_term/index.md", "# STM\n")
    write("long_term/index.md", "# LTM\n")
    for n in names:
        write(f"short_term/entries/{n}.md", f"---\ncreated: 2026-10-17\n---\n\n{n}\n")


def test_key_ignores_word_order_and_accents():
    assert (questions.question_key("Nina et la copine : même personne ?")
            == questions.question_key("La copine et Nina, meme personne"))


def test_dream_files_question_once(wiki, write, monkeypatch):
    _stm(write, "a")
    q = "La « copine » qui tricote est-elle Nina ?"
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {"pages": [], "questions": [q]}}])
    _, report = pipeline._execute(_now(), "2026-10-17")
    files = list((wiki / "questions").glob("*.md"))
    assert len(files) == 1 and q in files[0].read_text()
    assert "question(s) à poser" in report
    # Only a question, nothing sure: the entry waits for the answer.
    assert (wiki / "short_term/entries/a.md").exists()
    # Next night, the same question (even reworded) is not filed again.
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {"questions": ["Nina est-elle la copine qui tricote ?"]}}])
    pipeline._execute(_now(), "2026-10-18")
    assert len(list((wiki / "questions").glob("*.md"))) == 1


def test_answered_question_is_never_asked_again(wiki, write, monkeypatch):
    _stm(write, "a")
    q = "Le voyage a-t-il commencé le 10 ou le 11 octobre ?"
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [
        {"unit": {"stm": ["a.md"]}, "decision": {"questions": [q]}}])
    pipeline._execute(_now(), "2026-10-17")
    rel = questions.list_questions()[0]["path"]
    assert _tool("complete")(rel) == f"Closed {rel}."
    assert questions.list_questions(open_only=True) == []
    pipeline._execute(_now(), "2026-10-18")
    assert len(questions.list_questions()) == 1          # still the one, answered


def test_duplicate_pages_become_a_question(wiki, write, monkeypatch):
    _stm(write, "a")
    write("long_term/people/chloe.md", "# Chloe\n")
    write("long_term/people/chloe-bernard.md", "# Chloe Bernard\n")
    monkeypatch.setattr(pipeline, "_decisions", lambda u, p, se, no: [])
    pipeline._execute(_now(), "2026-10-17")
    pipeline._execute(_now(), "2026-10-18")
    qs = questions.list_questions()
    assert len(qs) == 1
    assert "chloe-bernard.md" in qs[0]["body"] and qs[0]["meta"]["key"].startswith("dup:")


def test_decide_sees_open_questions(wiki, write, monkeypatch):
    write("questions/2026-10-17-x.md", "---\nkey: q:x\nstatus: open\n---\n\nQuestion déjà posée ?\n")
    seen = []

    def fake_stage(usage, stage, context):
        seen.append((stage, context))
        if stage == "triage":
            return {"units": [{"stm": ["a.md"], "intent": "i", "touches": [], "hint": "keep"}]}
        return {"pages": [], "rationale": "r"}

    monkeypatch.setattr(pipeline, "_stage", fake_stage)
    pipeline._decisions(models._Usage(), "policy", [("a.md", "texte")], [])
    decide = next(c for s, c in seen if s == "decide")
    assert "Question déjà posée ?" in decide.split("<questions_ouvertes>")[1]


def test_prime_lists_open_questions_only(wiki, write):
    write("long_term/index.md", "# LTM\n")
    write("questions/2026-10-17-a.md", "---\nkey: q:a\nstatus: open\n---\n\nOuverte ?\n")
    write("questions/2026-10-17-b.md", "---\nkey: q:b\nstatus: answered\n---\n\nRépondue ?\n")
    out = query.build_prime()
    assert "Ouverte ? questions/2026-10-17-a.md" in out
    assert "Répondue" not in out and "one at a time" in out


def test_complete_rejects_paths_outside_questions(wiki, write):
    write("long_term/people/nina.md", "# Nina\n")
    assert questions.resolve_question("long_term/people/nina.md") is None
    assert questions.resolve_question("questions/../long_term/people/nina.md") is None
