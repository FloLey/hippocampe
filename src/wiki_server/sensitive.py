"""Detection of secrets and sensitive identifiers in text headed for memory.

Everything written to the wiki lands in git history for good, and the self pages
are loaded into every conversation, so a password or a recovery key must not
slip in unnoticed. This is a small, dependency-free pattern scan: ``remember``
moves what it finds into the encrypted vault (or masks it when there is none)
before anything is written, the dream notes it in its report.
"""

from __future__ import annotations

import re
from typing import Callable

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("clé privée", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("clé d'API", re.compile(
        r"\b(?:sk-(?:ant-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
        r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[abprs]-[A-Za-z0-9-]{10,})")),
    # Six dash-separated groups of four, e.g. a FileVault / BitLocker-style key.
    ("clé de récupération", re.compile(r"\b(?:[A-Z0-9]{4}-){5}[A-Z0-9]{4}\b")),
    ("numéro de registre national", re.compile(r"\b\d{2}\.\d{2}\.\d{2}-\d{3}\.\d{2}\b")),
    ("numéro de carte d'identité", re.compile(r"\b\d{3}-\d{7}-\d{2}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")),
    # Only the value (group "v") is the secret; the label stays readable.
    ("mot de passe", re.compile(
        r"(?i)\b(?:mot de passe|mdp|password|passwd|passcode|code pin|pin)\s*[:=]\s*(?P<v>\S+)")),
]

_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def find_sensitive(text: str) -> list[str]:
    """The kinds of secret found in ``text`` (empty if none), deduplicated."""
    found = [label for label, pattern in _PATTERNS if pattern.search(text or "")]
    for m in _CARD_RE.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            found.append("numéro de carte")
            break
    return list(dict.fromkeys(found))


def replace_sensitive(text: str, replacer: Callable[[str, str, str], str]) -> tuple[str, list[str]]:
    """Replace every detected secret in ``text`` by ``replacer(kind, value,
    preceding_text)`` and return (new text, kinds replaced). The preceding text
    lets a caller name the secret after what introduces it."""
    text = text or ""
    found: list[str] = []
    for label, pattern in _PATTERNS:
        def sub(m: re.Match, label=label, pattern=pattern) -> str:
            if "v" in pattern.groupindex:
                cut = m.start("v") - m.start()
                prefix, value = m.group()[:cut], m.group("v")
            else:
                prefix, value = "", m.group()
            found.append(label)
            return prefix + replacer(label, value, m.string[:m.start()] + prefix)
        text = pattern.sub(sub, text)

    def card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            found.append("numéro de carte")
            return replacer("numéro de carte", m.group(), m.string[:m.start()])
        return m.group()

    text = _CARD_RE.sub(card, text)
    return text, list(dict.fromkeys(found))


def mask_sensitive(text: str) -> tuple[str, list[str]]:
    """``text`` with every detected secret replaced by ``[MASQUÉ: <kind>]``, and
    the kinds that were masked (empty if none)."""
    return replace_sensitive(text, lambda kind, _value, _before: f"[MASQUÉ: {kind}]")
