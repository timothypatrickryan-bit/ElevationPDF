# ElevationPDF — notes for Claude Code

Read `docs/PLAN.md` first; §6, §7, §13 are normative for the code here.

## Layout

- `services/extract` — Python (3.11+) extraction package `elevation_extract`,
  `feasibility.py` CLI, pytest golden suite driven by synthetic PDF fixtures
  (`tests/make_fixtures.py`).
- `packages/model` — sheet-model JSON Schema (source of truth) + TS types.
  Change the schema and the Python model together; the schema-validation test
  in `tests/test_extract.py` checks extractor output against it.
- `apps/web` — Vite + React + pdf.js viewer with snapping + length measurement
  against a sheet-model JSON (produced by `feasibility.py --json-dir`).

## Commands

```bash
# Python (from services/extract; venv at services/extract/.venv)
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/python feasibility.py <folder> --out report.csv

# Web (from repo root; npm workspaces)
npm install
npm run build -w apps/web
```

## Conventions that bite

- **Coordinate convention (normative, PLAN §6):** everything in the sheet model
  is in *sheet space* — displayed page (CropBox + `/Rotate` applied), origin
  top-left, y down, PDF points. PyMuPDF's `get_drawings()`/`get_text()` return
  CropBox-normalized but **unrotated** coordinates; `extract.py` applies
  `page.rotation_matrix` exactly once. Never re-apply rotation downstream.
- PyMuPDF page objects go stale after document mutations (`new_page`, etc.) —
  re-fetch `doc[i]` instead of holding references.
- `get_drawings(extended=True)` yields both styled paths (`s`/`f`/`fs`, with
  `layer` = OCG name or `""`) and `clip` items (`scissor` rects) in one pass.
- Scale math: `points_per_foot = paper_inches_per_real_foot × 72`
  (1/8" = 1'-0" → 9 ppf; 1:N metric → 864/N ppf).
- Fixture PDFs are generated, not checked in. If extractor behavior looks
  wrong, extend `tests/make_fixtures.py` to reproduce and pin it with a test.
