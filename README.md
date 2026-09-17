# ElevationPDF

Takeoff and measurement for construction plan sets, built on the vector content
that survives inside AutoCAD-plotted PDFs — geometry, text, layers, viewports —
instead of treating the PDF as a picture.

The full plan lives in [`docs/PLAN.md`](docs/PLAN.md). Short version: an
extraction pipeline (PyMuPDF) rebuilds a structured **sheet model** from each
PDF page, and a measurement UI (pdf.js + canvas overlay) provides snapping and
scale-aware length/area/count tools on top of it. The PDF is never modified.

## Repo layout

```
apps/web             React + Vite + pdf.js viewer (OCG layer toggling)
services/extract     Python extraction service, feasibility CLI, golden tests
packages/model       Sheet-model JSON Schema (normative) + shared TS types
docs                 PLAN.md and future ADRs
```

## Quickstart

### Extraction / feasibility (Python 3.11+)

```bash
cd services/extract
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                       # golden-test suite (synthetic PDFs)
.venv/bin/python tests/make_fixtures.py /tmp/sample   # generate a sample set
.venv/bin/python feasibility.py /tmp/sample --out report.csv
```

`feasibility.py <folder> --out report.csv` walks every PDF page in a folder and
emits the Phase 0 metrics from PLAN §13 (vector vs raster, OCGs, text, scale
detectability, monochrome-plot rate, segment counts), plus a printed summary
with the decision-gate stats. Plan sets under NDA stay local — that is the
point of it being a CLI.

### Web viewer (Node 20+)

```bash
npm install
npm run dev -w apps/web
```

Open the printed URL, choose a PDF, toggle layers (OCGs), navigate pages.

## Sheet model

`packages/model/schema/sheet-model.schema.json` is the normative definition of
extraction output, including the coordinate convention (displayed page,
top-left origin, y-down, PDF points). The Python extractor validates against
it in tests; the TS types in `packages/model/src` mirror it for the frontend.
