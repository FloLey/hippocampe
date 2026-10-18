"""Secret / sensitive identifier detection."""

from wiki_server.sensitive import find_sensitive, mask_sensitive


def test_detects_common_secrets():
    assert "clé de récupération" in find_sensitive("FileVault : ABCD-EFGH-JKLM-NPQR-STUV-WXYZ")
    assert "numéro de registre national" in find_sensitive("RN 90.02.03-123.45")
    assert "numéro de carte d'identité" in find_sensitive("N° de carte : 123-4567890-12")
    assert "clé d'API" in find_sensitive("token sk-ant-api03-abcdefghijklmnopqrstuvwxyz")
    assert "clé privée" in find_sensitive("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert "mot de passe" in find_sensitive("mot de passe : hunter2")
    assert "IBAN" in find_sensitive("compte BE71 0961 2345 6769")
    assert "numéro de carte" in find_sensitive("carte 4111 1111 1111 1111")


def test_ignores_ordinary_text():
    assert find_sensitive("") == []
    assert find_sensitive("10 km de Lyon le 3 avril 2026 en 0:58, FC 165.") == []
    assert find_sensitive("Réservations Booking 4815162342 et 1010203040.") == []
    assert find_sensitive("Le code est propre et le passage est clair.") == []


def test_mask_replaces_only_the_secret():
    text, kinds = mask_sensitive("Clé ABCD-EFGH-JKLM-NPQR-STUV-WXYZ, carte 4111 1111 1111 1111, 20 km.")
    assert text == "Clé [MASQUÉ: clé de récupération], carte [MASQUÉ: numéro de carte], 20 km."
    assert kinds == ["clé de récupération", "numéro de carte"]
    assert mask_sensitive("Rien ici.") == ("Rien ici.", [])
