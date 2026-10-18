"""Photos attached to pages.

A photo (from the console or the Telegram bot) is re-encoded as a JPEG of at
most ``_MAX_SIDE`` pixels, which also drops its metadata (EXIF, GPS position),
then stored under ``media/<page>/`` in the wiki and linked from the page's
"Photos" section with its caption. Small files, so the git history and the
backup stay light.
"""

from __future__ import annotations

import datetime
import io
import os
import posixpath

from wiki_server.paths import resolve_under_root
from wiki_server.store import apply_changes, slugify, unique_stem

MEDIA_DIR = "media"
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp", ".gif": "image/gif"}
_MAX_SIDE = 1600
_MAX_BYTES = 25 * 1024 * 1024


class MediaError(Exception):
    pass


def _encode(data: bytes) -> bytes:
    if len(data) > _MAX_BYTES:
        raise MediaError("Photo trop lourde (25 Mo maximum).")
    try:
        from PIL import Image, ImageOps

        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)  # keep the orientation, then drop EXIF
        img.thumbnail((_MAX_SIDE, _MAX_SIDE))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError(f"Ce fichier n'est pas une image lisible ({type(exc).__name__}).") from None


def _with_photo(page_text: str, link: str) -> str:
    """The page with ``link`` added at the end of its "## Photos" section."""
    lines = page_text.rstrip("\n").split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip().lower() == "## photos")
    except StopIteration:
        return page_text.rstrip("\n") + f"\n\n## Photos\n\n{link}\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[:end] + ["", link] + lines[end:]).rstrip("\n") + "\n"


def add_photo(page_rel: str, data: bytes, caption: str = "", day: str | None = None) -> str:
    """Store the photo for ``page_rel`` and link it from the page, in one
    commit. Returns the image's path in the wiki."""
    page = resolve_under_root(page_rel)
    if not page_rel.startswith("long_term/") or not page.is_file():
        raise MediaError("Page introuvable.")
    jpeg = _encode(data)
    day = day or datetime.date.today().isoformat()
    folder = f"{MEDIA_DIR}/{slugify(page_rel.rsplit('/', 1)[-1][:-3], maxlen=40)}"
    stem = unique_stem(folder, f"{day}-{slugify(caption or 'photo', maxlen=40)}")
    rel = f"{folder}/{stem}.jpg"
    link_target = posixpath.relpath(rel, posixpath.dirname(page_rel))
    alt = (caption or "photo").replace("[", "(").replace("]", ")").replace("\n", " ").strip()
    link = f"![{alt}]({link_target})" + (f"\n*{alt}, {day}*" if caption else f"\n*{day}*")
    text = page.read_text(encoding="utf-8")
    target = resolve_under_root(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(jpeg)
    os.chmod(target, 0o644)
    apply_changes({page_rel: _with_photo(text, link)}, [], f"media: photo on {page_rel}")
    return rel


def photos_of(page_rel: str) -> list[str]:
    """Image paths linked from a page (for the graph and recall)."""
    import re

    try:
        text = resolve_under_root(page_rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    base = posixpath.dirname(page_rel)
    return [posixpath.normpath(posixpath.join(base, m))
            for m in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text)]
