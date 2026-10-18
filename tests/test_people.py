"""People cards and birthdays."""

import datetime

from wiki_server import people


def test_card_reads_birthday_likes_gifts(wiki, write):
    write("long_term/people/camille.md",
          "# Camille\n\n## Fiche\n\n- Anniversaire : 14 mars\n- Aime les violettes.\n\n"
          "## Idées cadeaux\n\n- Levain violette\n")
    write("long_term/people/soeur.md", "# Sœur\n\n- Un fils, Bébé, né le 2 juin 2026.\n"
                                       "- Mariée le 12 juin 1993.\n- Née le 05/09/1990.\n")
    c = people.card("long_term/people/camille.md")
    assert c["birthday"] == (14, 3, None)
    assert c["likes"] == ["Aime les violettes."] and c["gifts"] == ["Levain violette"]
    assert people.card("long_term/people/soeur.md")["birthday"] == (5, 9, 1990)


def test_upcoming_birthdays_with_age_and_gifts(wiki, write):
    write("long_term/people/lina.md", "# Lina\n\n- Date de naissance : 28 janvier 1991\n"
                                      "- Idée cadeau : une pelote de laine mérinos\n")
    write("long_term/self/identity.md", "# Identity\n\n- Naissance : 16 janvier 1992\n")
    out = people.upcoming_birthdays(datetime.date(2027, 1, 10), 30)
    assert out == ["Toi : 16/1 (dans 6 jours, 35 ans)",
                   "Lina : 28/1 (dans 18 jours, 36 ans) · idées cadeaux : Idée cadeau : une pelote de laine mérinos"]
    assert people.upcoming_birthdays(datetime.date(2027, 3, 1), 30) == []


def test_digest_lists_birthdays(wiki, write, monkeypatch):
    from wiki_server import telegram

    write("long_term/people/lina.md", "# Lina\n\n- Date de naissance : 28 janvier 1991\n")
    text, _ = telegram.digest(datetime.date(2027, 1, 23))
    assert "🎂 Anniversaires" in text and "Lina : 28/1 (dans 5 jours, 36 ans)" in text
