"""The Telegram bot, with the Telegram API stubbed out."""

import datetime

import pytest
from cryptography.fernet import Fernet

from wiki_server import questions, telegram, vault


@pytest.fixture
def bot(wiki, tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("WIKI_VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    sent = []

    def fake_call(method, http_timeout=30, tok=None, **params):
        sent.append((method, params))
        if method == "sendMessage":
            return {"message_id": len(sent)}
        if method == "getMe":
            return {"username": "memoire_bot"}
        return True

    monkeypatch.setattr(telegram, "_call", fake_call)
    return sent


def _texts(sent):
    return [p["text"] for m, p in sent if m == "sendMessage"]


def _msg(text, chat=42, reply_to=None):
    m = {"chat": {"id": chat}, "text": text}
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"message": m}


def _link(chat=42):
    code = telegram.new_pair_code()
    telegram.handle_update(_msg(f"/start {code}", chat=chat))


def test_pairing_needs_the_code_and_ignores_strangers(bot, wiki):
    telegram.new_pair_code()
    telegram.handle_update(_msg("/start 000000x"))
    assert not telegram.status()["linked"] and bot == []
    _link()
    assert telegram.status()["linked"]
    bot.clear()
    telegram.handle_update(_msg("coucou", chat=99))
    assert bot == [] and not list((wiki / "short_term/entries").glob("*.md"))


def test_message_is_captured_and_secret_vaulted(bot, wiki):
    _link()
    telegram.handle_update(_msg("Ma clé FileVault : ABCD-EFGH-JKLM-NPQR-STUV-WXYZ"))
    entry = next((wiki / "short_term/entries").glob("*.md")).read_text()
    assert "ABCD" not in entry and "[SECRET:" in entry
    assert "Secret mis au coffre" in _texts(bot)[-1]
    assert vault.catalog()


def test_ambiguous_note_asks_and_answer_is_remembered(bot, wiki, write):
    write("long_term/people/bastien-dupont.md", "# A\n")
    write("long_term/people/bastien-martin.md", "# B\n")
    _link()
    telegram.handle_update(_msg("Bastien vient samedi"))
    assert "Quel Bastien : Bastien Dupont ou Bastien Martin ?" in _texts(bot)[-1]
    ask_id = len(bot)
    telegram.handle_update(_msg("Martin", reply_to=ask_id))
    entries = sorted(p.read_text() for p in (wiki / "short_term/entries").glob("*.md"))
    assert any("Précision sur la note" in e and "Martin" in e for e in entries)


def test_question_reply_closes_it(bot, wiki, write):
    rel, content = questions.build_question("Lisbonne : fusionner ?")
    write(rel, content)
    _link()
    telegram.handle_update(_msg("/question"))
    ask_id = len(bot)
    telegram.handle_update(_msg("oui fusionne", reply_to=ask_id))
    assert questions.list_questions(open_only=True) == []
    assert any("oui fusionne" in p.read_text() for p in (wiki / "short_term/entries").glob("*.md"))


def test_digest_lists_week_and_button_closes(bot, wiki, write, monkeypatch):
    today = datetime.date(2026, 10, 18)
    monkeypatch.setattr(telegram, "_today", lambda: today)
    write("temporal/a.md", "---\ntype: todo\nstatus: active\ndue: 2026-10-20\n---\n\nAppeler le notaire\n")
    write("temporal/b.md", "---\ntype: todo\nstatus: active\ndue: 2026-10-15\n---\n\nPayer la facture\n")
    write("temporal/c.md", "---\ntype: event\nstatus: active\ndue: 2026-10-22\n---\n\nConcert\n")
    write("temporal/d.md", "---\ntype: todo\nstatus: active\ndue: 2026-12-01\n---\n\nPlus tard\n")
    text, closable = telegram.digest(today)
    assert "En retard" in text and "Payer la facture" in text and "Appeler le notaire (mar 20/10)" in text
    assert "Concert" in text and "Plus tard" not in text
    assert closable == ["temporal/b.md", "temporal/a.md"]
    _link()
    assert telegram.send_digest()
    markup = [p for m, p in bot if m == "sendMessage"][-1]["reply_markup"]["inline_keyboard"]
    first = markup[0][0]["callback_data"]
    telegram.handle_update({"callback_query": {"id": "1", "data": first,
                                               "message": {"chat": {"id": 42}}}})
    assert "status: done" in (wiki / "temporal/b.md").read_text()


def test_digest_due_on_sunday_morning_once():
    sunday = datetime.datetime(2026, 10, 18, 9, 5)
    assert telegram._digest_due(sunday, "")
    assert not telegram._digest_due(sunday, "2026-10-18")
    assert not telegram._digest_due(sunday.replace(hour=8), "")
    assert not telegram._digest_due(datetime.datetime(2026, 10, 19, 10), "")


def test_token_is_kept_in_the_vault(bot, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert telegram.token() == ""
    assert telegram.set_token(" 999:xyz ") == "memoire_bot"
    assert telegram.token() == "999:xyz"
    assert vault.catalog() == []  # not listed among the user's secrets


def test_photo_with_caption_goes_to_the_page(bot, wiki, write, monkeypatch):
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (10, 10)).save(out, "JPEG")
    monkeypatch.setattr(telegram, "_download", lambda fid: out.getvalue())
    write("long_term/projects/poterie.md", "# Poterie\n\nPièces.\n")
    _link()
    telegram.handle_update({"message": {"chat": {"id": 42}, "caption": "Poterie : bol fini",
                                        "photo": [{"file_id": "a", "file_size": 10}]}})
    assert "Photo ajoutée à « Poterie »" in _texts(bot)[-1]
    assert "## Photos" in (wiki / "long_term/projects/poterie.md").read_text()
    telegram.handle_update({"message": {"chat": {"id": 42}, "photo": [{"file_id": "b"}]}})
    assert "Ajoute une légende" in _texts(bot)[-1]
