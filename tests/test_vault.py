"""The encrypted secrets vault, and how remember / prime / secret use it."""

import os

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("WIKI_AUTH_DISABLED", "1")

from wiki_server import query, server, vault  # noqa: E402


@pytest.fixture
def vaulted(wiki, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    monkeypatch.setenv("WIKI_VAULT_ROOT", str(root))
    monkeypatch.setenv("WIKI_VAULT_KEY", Fernet.generate_key().decode())
    return root


def _tool(name):
    t = getattr(server, name)
    return getattr(t, "fn", t)


def test_disabled_without_key(wiki, monkeypatch):
    monkeypatch.delenv("WIKI_VAULT_KEY", raising=False)
    assert vault.enabled() is False


def test_refuses_vault_inside_wiki(wiki, monkeypatch):
    monkeypatch.setenv("WIKI_VAULT_ROOT", str(wiki / "vault"))
    monkeypatch.setenv("WIKI_VAULT_KEY", Fernet.generate_key().decode())
    assert vault.enabled() is False


def test_put_get_is_encrypted_at_rest(vaulted):
    name = vault.put("ABCD-EFGH-JKLM-NPQR-STUV-WXYZ", "clé de récupération", "Ma clé FileVault :")
    assert name == "ma-cle-filevault"
    assert vault.get(name)["value"] == "ABCD-EFGH-JKLM-NPQR-STUV-WXYZ"
    raw = (vaulted / vault.VAULT_FILE).read_bytes()
    assert b"ABCD" not in raw and b"filevault" not in raw.lower()
    # Same value again: same entry, no duplicate.
    assert vault.put("ABCD-EFGH-JKLM-NPQR-STUV-WXYZ", "clé de récupération", "autre") == name
    assert [e["name"] for e in vault.catalog()] == [name]
    assert "value" not in vault.catalog()[0]


def test_wrong_key_cannot_read(vaulted, monkeypatch):
    vault.put("secret-value-123", "mot de passe", "wifi")
    monkeypatch.setenv("WIKI_VAULT_KEY", Fernet.generate_key().decode())
    with pytest.raises(Exception):
        vault.catalog()


def test_remember_moves_secret_to_vault(vaulted, wiki):
    out = _tool("remember")("Ma clé FileVault : ABCD-EFGH-JKLM-NPQR-STUV-WXYZ. Wifi mot de passe : hunter2!")
    assert "encrypted vault" in out
    entry = next((wiki / "short_term/entries").glob("*.md")).read_text()
    assert "ABCD" not in entry and "hunter2" not in entry
    assert "[SECRET: ma-cle-filevault]" in entry
    assert "mot de passe : [SECRET: " in entry          # the label stays readable
    assert _tool("secret")("ma-cle-filevault").startswith("ABCD-EFGH-JKLM-NPQR-STUV-WXYZ")
    assert _tool("secret")("[SECRET: ma-cle-filevault]").startswith("ABCD")


def test_secret_unknown_lists_names(vaulted):
    vault.put("x" * 30, "clé d'API", "token github")
    out = _tool("secret")("nope")
    assert "No secret named" in out and "token-github" in out


def test_prime_lists_names_not_values(vaulted, wiki, write):
    write("long_term/index.md", "# LTM\n")
    vault.put("ABCD-EFGH-JKLM-NPQR-STUV-WXYZ", "clé de récupération", "Ma clé FileVault :")
    out = query.build_prime()
    assert "ma-cle-filevault" in out and "ABCD" not in out


def test_extract_from_wiki(vaulted, wiki, write):
    write("long_term/self/identity.md",
          "# Moi\n\n- Clé de récupération FileVault : ABCD-EFGH-JKLM-NPQR-STUV-WXYZ\n- RN : 90.02.03-123.45\n")
    write("long_term/people/nina.md", "# Nina\n\nRien de secret.\n")
    rel, report = vault.extract_from_wiki()
    page = (wiki / "long_term/self/identity.md").read_text()
    assert "ABCD" not in page and "90.02.03" not in page and "[SECRET: " in page
    assert (wiki / "long_term/people/nina.md").read_text() == "# Nina\n\nRien de secret.\n"
    assert "long_term/self/identity.md" in report and "ABCD" not in report
    assert {e["kind"] for e in vault.catalog()} == {"clé de récupération", "numéro de registre national"}


def test_extract_renames_files_named_after_a_secret(vaulted, wiki, write):
    write("temporal/2027-11-17-carte-987-6543210-98-expire.md",
          "---\ntype: reminder\nstatus: active\ndue: 2027-11-17\n---\n\nCarte 987-6543210-98 expire.\n")
    rel, report = vault.extract_from_wiki()
    files = [p.name for p in (wiki / "temporal").glob("*.md")]
    assert files == ["2027-11-17-carte-x-expire.md"]
    assert "987-6543210" not in (wiki / "temporal" / files[0]).read_text()
    assert "987-6543210" not in report
