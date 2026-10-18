"""Photos on pages: re-encoded without metadata, linked from the page."""

import io

from PIL import Image

from wiki_server import media


def _jpeg_with_gps():
    img = Image.new("RGB", (3000, 2000), "red")
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (50.0, 50.0, 0.0)}  # GPS IFD
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


def test_add_photo_resizes_strips_metadata_and_links(wiki, write):
    write("long_term/projects/poterie.md", "# Poterie\n\n## Pièces\n\nBol.\n\n## Liens\n- x\n")
    rel = media.add_photo("long_term/projects/poterie.md", _jpeg_with_gps(), "Bol terminé", day="2026-10-17")
    assert rel == "media/poterie/2026-10-17-bol-termine.jpg"
    img = Image.open(wiki / rel)
    assert max(img.size) == 1600 and not img.getexif()
    page = (wiki / "long_term/projects/poterie.md").read_text()
    assert page.endswith("## Photos\n\n![Bol terminé](../../media/poterie/2026-10-17-bol-termine.jpg)\n"
                         "*Bol terminé, 2026-10-17*\n")
    media.add_photo("long_term/projects/poterie.md", _jpeg_with_gps(), "Vase", day="2026-10-18")
    page = (wiki / "long_term/projects/poterie.md").read_text()
    assert page.count("## Photos") == 1 and page.index("bol-termine") < page.index("vase")
    assert media.photos_of("long_term/projects/poterie.md") == [
        "media/poterie/2026-10-17-bol-termine.jpg", "media/poterie/2026-10-18-vase.jpg"]


def test_rejects_non_images_and_unknown_pages(wiki, write):
    import pytest

    write("long_term/projects/p.md", "# P\n")
    with pytest.raises(media.MediaError):
        media.add_photo("long_term/projects/p.md", b"not an image")
    with pytest.raises(media.MediaError):
        media.add_photo("long_term/projects/nope.md", _jpeg_with_gps())
