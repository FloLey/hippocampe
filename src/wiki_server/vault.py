"""The secrets vault: things the user wants remembered that must never reach git.

Secrets live in one encrypted file under ``WIKI_VAULT_ROOT`` (a separate volume,
outside the wiki's git repo, so no commit can ever pick them up), sealed with
the Fernet key in ``WIKI_VAULT_KEY``. A copy of the volume or of a backup reveals
nothing without that key. The wiki itself only holds references such as
``[SECRET: cle-filevault]``; ``prime`` lists the names, never the values, and the
``secret`` tool returns a value on demand.

Without a key the vault is disabled and callers fall back to masking.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from pathlib import Path

from wiki_server.store import slugify

VAULT_FILE = "secrets.json.enc"
CONFIG_FILE = "connections.json.enc"

_lock = threading.Lock()


class VaultUnavailable(Exception):
    """No (valid) key is configured, so nothing can be stored or read."""


def vault_root() -> Path:
    return Path(os.environ.get("WIKI_VAULT_ROOT", "/srv/vault")).resolve()


def _fernet():
    key = os.environ.get("WIKI_VAULT_KEY", "").strip()
    if not key:
        raise VaultUnavailable("WIKI_VAULT_KEY is not set.")
    from wiki_server.paths import wiki_root

    # Inside the wiki, the vault file would be swept into git by the next commit.
    if vault_root().is_relative_to(wiki_root()):
        raise VaultUnavailable("WIKI_VAULT_ROOT must be outside the wiki's git repo.")
    from cryptography.fernet import Fernet

    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise VaultUnavailable(f"WIKI_VAULT_KEY is not a valid Fernet key: {exc}") from exc


def enabled() -> bool:
    try:
        _fernet()
        return True
    except VaultUnavailable:
        return False


def _load(fernet, filename: str = VAULT_FILE) -> dict[str, dict]:
    path = vault_root() / filename
    if not path.is_file():
        return {}
    data = json.loads(fernet.decrypt(path.read_bytes()).decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _save(fernet, data: dict, filename: str = VAULT_FILE) -> None:
    root = vault_root()
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f"{filename}.tmp"
    tmp.write_bytes(fernet.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8")))
    os.chmod(tmp, 0o600)
    tmp.replace(root / filename)  # atomic: a crash never leaves half a vault


def put(value: str, kind: str, context: str = "", name: str = "") -> str:
    """Store ``value`` and return its name. The name comes from ``name`` or from
    the words just before the secret in the note (``context``), so it reads
    like what it is (``ma-cle-filevault``). Storing the same value again under
    the same kind reuses its entry instead of duplicating it."""
    fernet = _fernet()
    with _lock:
        data = _load(fernet)
        for existing, entry in data.items():
            if entry.get("value") == value and entry.get("kind") == kind:
                return existing
        base = slugify(name or " ".join(context.split()[-4:]) or kind, maxlen=40)
        if base == "note":
            base = slugify(kind, maxlen=40)
        candidate, n = base, 2
        while candidate in data:
            candidate, n = f"{base}-{n}", n + 1
        data[candidate] = {
            "value": value, "kind": kind, "context": context.strip()[-120:],
            "created": datetime.date.today().isoformat(),
        }
        _save(fernet, data)
        return candidate


def get(name: str) -> dict | None:
    """The entry (value, kind, context, created) for ``name``, or None."""
    with _lock:
        return _load(_fernet()).get((name or "").strip())


def catalog() -> list[dict]:
    """Names and metadata of every secret, without the values."""
    with _lock:
        data = _load(_fernet())
    return [{"name": k, "kind": v.get("kind", ""), "context": v.get("context", ""),
             "created": v.get("created", "")} for k, v in sorted(data.items())]


def delete(name: str) -> bool:
    fernet = _fernet()
    with _lock:
        data = _load(fernet)
        if name not in data:
            return False
        del data[name]
        _save(fernet, data)
        return True


# --- connection settings ------------------------------------------------------
# Tokens for the connections set up from the console (Telegram bot, Strava,
# Google). Same key, separate file: they are not the user's secrets, so they are
# never listed in prime nor returned by the secret tool.

def config_get(key: str) -> str:
    """A stored connection setting, or "" (also when there is no vault)."""
    try:
        fernet = _fernet()
    except VaultUnavailable:
        return ""
    with _lock:
        return str(_load(fernet, CONFIG_FILE).get(key, ""))


def config_set(key: str, value: str) -> None:
    """Store (or, with an empty value, remove) a connection setting."""
    fernet = _fernet()
    with _lock:
        data = _load(fernet, CONFIG_FILE)
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        _save(fernet, data, CONFIG_FILE)


def extract_from_wiki() -> tuple[str, str]:
    """Move every secret already written in the wiki's markdown into the vault,
    leaving ``[SECRET: name]`` references, in one commit, and write a report
    (names only). Earlier commits still hold the old text: purging the history
    is a separate, deliberate step."""
    from wiki_server.dream.runner import _local_day
    from wiki_server.query import _iter_wiki_md
    from wiki_server.sensitive import mask_sensitive, replace_sensitive
    from wiki_server.store import apply_changes

    _fernet()  # fail early, before touching anything, if there is no vault
    writes: dict[str, str] = {}
    deletes: list[str] = []
    moved: list[str] = []
    for rel, path in _iter_wiki_md():
        if rel.startswith("prompts/"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        names: list[str] = []

        def to_vault(kind: str, value: str, before: str) -> str:
            name = put(value, kind, before)
            names.append(name)
            return f"[SECRET: {name}]"

        new, kinds = replace_sensitive(text, to_vault)
        # A filename derived from the text (temporal/2027-...-987-6543210-98-...)
        # can carry the secret too, and paths are shown in prime: rename it.
        folder, _, filename = rel.rpartition("/")
        safe_name, in_name = replace_sensitive(filename, lambda *_: "x")
        target = f"{folder}/{safe_name}" if folder else safe_name
        if in_name:
            deletes.append(rel)
        if kinds or in_name:
            writes[target] = new
            label = f"{rel} -> {target}" if in_name else rel
            moved.append(f"- {label} : {', '.join(dict.fromkeys(names)) or '(nom de fichier)'}")
    day = _local_day(datetime.datetime.now(datetime.timezone.utc))
    report_rel = f"dream_reports/{day}-secrets.md"
    report = (f"# Secrets déplacés dans le coffre, {day}\n\n"
              + ("\n".join(moved) if moved else "Aucun secret trouvé dans les pages.")
              + "\n\nLes anciens commits contiennent encore le texte d'origine : "
                "purger l'historique git est une étape à part.\n")
    writes[report_rel] = mask_sensitive(report)[0]
    apply_changes(writes, deletes, "manual: move secrets to the vault")
    return report_rel, writes[report_rel]
