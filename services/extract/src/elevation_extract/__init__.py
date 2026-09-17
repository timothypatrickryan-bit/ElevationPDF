"""ElevationPDF extraction: rebuild a structured sheet model from plotted PDFs.

Coordinate convention (normative, see docs/PLAN.md §6 and
packages/model/schema/sheet-model.schema.json): all output coordinates are in
"sheet space" — the page as displayed (CropBox with /Rotate applied), origin
top-left, x right, y down, units = PDF points (1/72 in).
"""

EXTRACTOR_VERSION = "0.1.0"

from .extract import extract_sheet  # noqa: E402,F401
