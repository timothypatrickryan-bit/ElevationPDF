"""Synthetic golden-test PDFs with known geometry and scale (PLAN §14 step 3).

Each fixture pins one extractor behavior: a scaled sheet with dimensions, an
NTS sheet where only dimension-fit works, OCG layers, a rotated page, a
Bluebeam-style measure dictionary, a raster scan, a monochrome plot, and
SHX-style annotation comments.

Run directly to generate a sample folder for the feasibility CLI:

    python tests/make_fixtures.py /tmp/sample
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

ARCH_D_LANDSCAPE = (2592.0, 1728.0)  # 36 x 24 in
PPF = 9.0  # 1/8" = 1'-0"

_META = {"producer": "ElevationPDF fixtures", "creator": "make_fixtures.py"}


def _new_doc(width: float, height: float) -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    doc.set_metadata(dict(_META))
    page = doc.new_page(width=width, height=height)
    return doc, page


def _title_block(page: pymupdf.Page, scale_text: str, sheet_no: str = "C-101") -> None:
    page.insert_text((2150, 1690), scale_text, fontsize=10)
    page.insert_text((2460, 1660), sheet_no, fontsize=14)


def simple_scaled() -> bytes:
    """ARCH D at 1/8" = 1'-0" with two dimensioned lines and mixed geometry."""
    doc, page = _new_doc(*ARCH_D_LANDSCAPE)
    _title_block(page, 'SCALE: 1/8" = 1\'-0"')

    # border
    page.draw_rect(pymupdf.Rect(50, 50, 2542, 1678), color=(0, 0, 0), width=1.0)

    # 100 ft horizontal dimension line (900 pt at 9 ppf) with end ticks
    page.draw_line((200, 800), (1100, 800), color=(0, 0, 0), width=0.5)
    page.draw_line((200, 790), (200, 810), color=(0, 0, 0), width=0.5)
    page.draw_line((1100, 790), (1100, 810), color=(0, 0, 0), width=0.5)
    page.insert_text((610, 785), "100'-0\"", fontsize=10)

    # 50 ft vertical dimension line (450 pt) with rotated text
    page.draw_line((200, 350), (200, 800), color=(0, 0, 0), width=0.5)
    page.insert_text((185, 575), "50'-0\"", fontsize=10, rotate=90)

    # assorted geometry: polyline, rect, bezier, a colored line, a dashed line
    page.draw_polyline([(1200, 300), (1400, 350), (1600, 300)], color=(0, 0, 0), width=0.35)
    page.draw_rect(pymupdf.Rect(1200, 500, 1500, 700), color=(0, 0, 0), width=0.35)
    page.draw_bezier((1700, 600), (1800, 500), (1900, 700), (2000, 600), color=(0, 0, 0), width=0.35)
    page.draw_line((300, 1000), (800, 1000), color=(0, 0.627, 1.0), width=0.35)
    page.draw_line((300, 1100), (800, 1100), color=(0, 0, 0), width=0.35, dashes="[6 3] 0")

    return doc.tobytes()


def nts_dims() -> bytes:
    """Title block says NTS; five consistent dimensions make dimension-fit work."""
    doc, page = _new_doc(*ARCH_D_LANDSCAPE)
    _title_block(page, "SCALE: N.T.S.", sheet_no="C-102")

    dims_ft = [50, 100, 30, 80, 60]
    y = 400.0
    for ft in dims_ft:
        length = ft * PPF
        page.draw_line((300, y), (300 + length, y), color=(0, 0, 0), width=0.5)
        label = f"{ft}'-0\""
        page.insert_text((300 + length / 2 - 20, y - 15), label, fontsize=10)
        y += 150
    return doc.tobytes()


def with_ocgs() -> bytes:
    """Two OCG layers plus one layerless path."""
    doc, page = _new_doc(*ARCH_D_LANDSCAPE)
    fiber = doc.add_ocg("E-FIBER", on=True)
    anno = doc.add_ocg("ANNO", on=False)
    page.draw_line((200, 200), (1200, 200), color=(0, 0.627, 1.0), width=0.35, oc=fiber)
    page.draw_line((200, 300), (1200, 320), color=(0, 0.627, 1.0), width=0.35, oc=fiber)
    page.draw_rect(pymupdf.Rect(200, 400, 600, 600), color=(1, 0, 0), width=0.7, oc=anno)
    page.draw_line((200, 700), (1200, 700), color=(0, 0, 0), width=0.5)
    return doc.tobytes()


def rotated90() -> bytes:
    """Letter portrait with /Rotate 90; pins the coordinate normalization.

    The line is drawn at (72,72)->(172,72) in unrotated space; in sheet space
    (displayed 792x612) it must come out at (720,72)->(720,172).
    """
    doc, page = _new_doc(612, 792)
    page.draw_line((72, 72), (172, 72), color=(0, 0, 0), width=1)
    page.insert_text((72, 60), "ROT", fontsize=10)
    page.set_rotation(90)
    return doc.tobytes()


def measure_dict() -> bytes:
    """Bluebeam-style /VP + /Measure (RL): 1 in = 8 ft -> 9 ppf, C = 1/9."""
    doc, page = _new_doc(*ARCH_D_LANDSCAPE)
    page.draw_line((200, 200), (1100, 200), color=(0, 0, 0), width=0.5)
    vp = (
        "[ << /Type /Viewport /BBox [ 0 0 2592 1728 ] "
        "/Measure << /Type /Measure /Subtype /RL /R (1 in = 8 ft) "
        "/X [ << /Type /NumberFormat /U (ft) /C 0.11111111 /D 100 >> ] >> >> ]"
    )
    doc.xref_set_key(page.xref, "VP", vp)
    return doc.tobytes()


def raster_scan() -> bytes:
    """A scanned sheet: one full-page image, no vector content."""
    doc, page = _new_doc(1224, 792)
    pm = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80))
    pm.set_rect(pm.irect, (235, 235, 235))
    page.insert_image(page.rect, pixmap=pm)
    return doc.tobytes()


def monochrome() -> bytes:
    """monochrome.ctb-style plot: everything black/gray, lineweight only."""
    doc, page = _new_doc(*ARCH_D_LANDSCAPE)
    y = 200.0
    for width in (0.18, 0.35, 0.5, 0.7, 1.0, 1.4):
        page.draw_line((200, y), (2000, y), color=(0, 0, 0), width=width)
        y += 120
    page.draw_line((200, y), (2000, y), color=(0.5, 0.5, 0.5), width=0.35)
    return doc.tobytes()


def shx_comments() -> bytes:
    """SHX text plotted as geometry, with AutoCAD-style comment annotations."""
    doc, page = _new_doc(*ARCH_D_LANDSCAPE)
    # "text" drawn as strokes (stand-in for SHX geometry)
    page.draw_line((400, 400), (430, 360), color=(0, 0, 0), width=0.35)
    page.draw_line((430, 360), (460, 400), color=(0, 0, 0), width=0.35)
    for i, content in enumerate(("25'-0\"", "10'-6\"", "SEE NOTE 4")):
        rect = pymupdf.Rect(400, 400 + 60 * i, 520, 430 + 60 * i)
        annot = page.add_rect_annot(rect)
        annot.set_info(content=content)
        annot.update()
    return doc.tobytes()


FIXTURES = {
    "simple_scaled.pdf": simple_scaled,
    "nts_dims.pdf": nts_dims,
    "with_ocgs.pdf": with_ocgs,
    "rotated90.pdf": rotated90,
    "measure_dict.pdf": measure_dict,
    "raster_scan.pdf": raster_scan,
    "monochrome.pdf": monochrome,
    "shx_comments.pdf": shx_comments,
}


def build_all(outdir: Path) -> dict[str, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for name, fn in FIXTURES.items():
        path = outdir / name
        path.write_bytes(fn())
        out[name] = path
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fixtures-out")
    built = build_all(target)
    for name, path in built.items():
        print(f"wrote {path}")
