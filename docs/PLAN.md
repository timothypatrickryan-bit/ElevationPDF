# PDF Construction Plan Takeoff Webapp — Plan

**Status:** Draft v2 · **Date:** 2026-09-17 · **Owner:** Tim
**Purpose of this file:** Working plan to hand to Claude Code. Sections 12–14 are written as executable tasks.
**v2:** Revised after review — see §18 for the change log.

---

## 1. Bottom line

AutoCAD-exported PDFs are usually still vector files: lines, arcs, text, and often the layer structure survive the plot. The product is therefore **not** a better PDF viewer. It is:

1. an **extraction pipeline** that pulls geometry, text, layers, and viewports back out of the PDF into a structured model, and
2. a **measurement UI** on top of that model with real snapping and scale awareness.

No DWG round-trip required. The original PDF is never modified; measurements and calibrations live as an overlay.

The first two weeks are a **feasibility pass on real plan sets**. Everything downstream depends on how those PDFs were actually plotted.

---

## 2. Goals / non-goals

**Goals**
- Open a plan set and immediately see a sheet index, layers, and a detected scale
- Measure length / run / area / count with snapping to actual drawing geometry
- Export quantities to CSV/Excel and annotated PDF
- Work well on telecom/fiber/tower drawing sets first; general construction second

**Non-goals (v1)**
- Editing the drawing or writing back to DWG
- Full markup/collaboration suite (Bluebeam parity)
- First-class support for scanned/raster sheets (supported, but second-class)

---

## 3. What is actually inside an AutoCAD PDF

| Element | Present? | Notes |
|---|---|---|
| Geometry | Yes (vector plots) | Lines, polylines, arcs as Bézier paths; each carries stroke color, lineweight, dash pattern |
| Layers | Sometimes | Stored as Optional Content Groups (OCGs) **only if** "Include layer information" was checked at plot time. Plan for both cases |
| Text | Usually | Extractable with positions when TrueType fonts were used. SHX fonts plot as geometry; newer AutoCAD attaches hidden comments containing the SHX text — read those. Otherwise OCR the region |
| Scale | Rarely | Bluebeam-calibrated PDFs carry a viewport/measure dictionary; native AutoCAD output generally does not. Scale must be detected or set |
| Viewports | Yes | Appear as clipping paths — but so do hatches, wipeouts, and text clips, often thousands per sheet. Viewport detection = heuristics over *large rectangular* clip regions containing many paths. A sheet with a 1/8" plan and 1" details has multiple scales; clip regions define where each applies |
| Raster scans | N/A | None of the above. OCR + manual calibration only |

**Units:** PDF user space is points (1/72 in). At 1/8" = 1'-0", one real foot = 0.125 in = **9 pt**. General formula: `points_per_foot = paper_inches_per_real_foot × 72`. Guard for `/UserUnit` (pages > 200 in declare a multiplier); rare on plotted sheets but cheap to carry through.

---

## 4. Capabilities by phase

### MVP
- Upload plan set (multi-page PDF or many PDFs) → auto sheet index from bookmarks / title-block text
- Render with layer toggling (native OCGs)
- Scale detection with confidence score + two-point manual calibration fallback
- Snap-to-geometry measurement: length, polyline run, area, count
  - Snap targets: endpoints, midpoints, intersections, perpendicular, nearest-on-path
- Save measurements per sheet; export CSV/xlsx; export annotated PDF

**MVP acceptance criteria**
- Measured length within **0.5%** of true value on test sheets with known dimensions
- Snap lands within **3 screen px** of the intended vertex at any zoom
- Sheets with **100k segments** pan/zoom smoothly (no visible jank); extraction < 60 s/sheet
- **Progressive availability:** sheet index visible within seconds of upload; each sheet becomes measurable as its extraction completes (pages extracted in parallel — a 500-sheet set must not serialize into hours)
- Scale auto-detected on ≥ 70% of vector sheets in the feasibility sample
- **Wrong-scale-with-high-confidence ≈ 0**, defined as: no sheet in the sample where confidence ≥ 0.8 and scale error > 1%

### Phase 2
- **Pseudo-layers** when OCGs are absent: cluster paths by color + lineweight + dash pattern (mirrors plot-style mapping); user names/hides clusters; corrections persist per source firm. **Known ceiling:** monochrome plots (`monochrome.ctb` — everything black, differentiated only by lineweight) collapse color clustering; the feasibility pass measures how much of the sample is monochrome before this is promised (§13)
- **Dimension-based auto-calibration:** find dimension text (e.g., `25'-0"`), pair with nearest dimension/extension line pair, compute implied scale, cross-check several → robust scale even when title block says NTS or sheet was scaled-to-fit. Value parser must handle `25'-0"`, `25'-0 1/2"`, decimal feet, metric, civil stationing (`10+50`). Caveat: on older sets the dimension text itself is SHX → geometry → invisible to this method; feasibility records dimension-text source
- Per-viewport scale on mixed-scale sheets
- Text search across the whole set
- Project sharing, basic markup (cloud, callout, note)
- SHX comment text + OCR fallback

### Phase 3 (product, not tool)
- Telecom-specific takeoff objects: fiber routes, conduit runs, splice points, handholes, cable pathways, equipment footprints — counted and totaled by type
- Revision compare (overlay old/new sheet, highlight deltas)
- Quantities flow into estimating templates
- Field/mobile view

---

## 5. Architecture

```
Upload (web) ──► R2 (original PDF)
                   │
                   ▼
        job row (Postgres, SKIP LOCKED) ──► Extraction worker (Python, Render)
                              PyMuPDF: paths, text, OCGs, clip regions
                              Shapely/NumPy: intersections, dimension fitting, scale inference
                              │
                              ▼
                     sheet-model JSON ──► R2 (model) + DB (index, scale, layers)
                              │
                              ▼
Frontend ◄────────────────────┘
  pdf.js renders the real PDF (fidelity) + OCG toggle
  Base render is tiled/bitmapped: render to offscreen canvas, pan the bitmap,
  re-render debounced on zoom-end (live vector re-render janks on E-size sheets)
  Canvas overlay: measurements, snapping via spatial index (flatbush)
  Measurements/calibrations saved to DB as overlay (PDF untouched)
                              │
                              ▼
Exports: annotated PDF (server-side PyMuPDF) · CSV/xlsx quantities
```

Principles
- PDF = source of truth, read-only
- Extracted model is cached, versioned by extractor version; re-extract on upgrade
- Overlay data (calibrations, measurements, layer corrections) is keyed to sheet + model version
- **One backend deploy target for MVP.** Jobs are a Postgres table claimed with `SELECT … FOR UPDATE SKIP LOCKED` by the same Render service that runs extraction — no Cloudflare Queues until scale demands it. Malformed PDFs will crash the parser occasionally: a job that crashes N times parks itself as `extraction_failed` (poison-message handling), never blocks the queue
- **Coordinate convention is normative and lives in the sheet model** (§6). Every geometry bug in this class of app is a transform bug; normalize once at extraction

---

## 6. Sheet model (extraction output)

**Coordinate convention (normative):** all coordinates are in **sheet space** — the page as displayed (CropBox with `/Rotate` applied), origin top-left, x right, y down, units = PDF points (1/72 in). `rotation` and `user_unit` are recorded for provenance; consumers never re-apply them. The JSON Schema in `/packages/model` is the cross-language source of truth; the Python extractor's output is validated against it in CI.

```jsonc
{
  "sheet_id": "…", "page_index": 3, "size_pt": [2592, 1728], "rotation": 0,
  "user_unit": 1.0,
  "extractor_version": "0.1.0",
  "content_hash": "sha256:…",          // page content streams — dedupe + revision compare
  "producer": "AutoCAD PDF plot", "creator": null,
  "title_block": { "sheet_no": "C-101", "title": "SITE PLAN", "scale_text": "1/8\" = 1'-0\"", "bbox": [..] },
  "layers": [ { "id": "L1", "name": "E-FIBER", "source": "ocg|inferred|user", "default_visible": true,
                "signature": { "stroke": "#00A0FF", "width": 0.35, "dash": [4,2] } } ],
  "paths": [ { "id": "P1", "layer_id": "L1", "kind": "line|polyline|rect|quad|bezier",
               "pts": [[x,y],...],            // bezier: control points, 3k+1 per chain
               "stroke": "#..", "fill": null, // hatches/solids are fills — kept distinct
               "width": 0.35, "dash": [], "closed": false } ],
  "texts": [ { "id": "T1", "str": "25'-0\"", "bbox": [..], "dir": [1,0], "rotation_deg": 0,
               "font": "Arial", "size": 8, "layer_id": "L4",
               "source": "text|shx_comment|ocr" } ],   // rotation matters: vertical dim text is everywhere
  "viewports": [ { "id": "V1", "clip_bbox": [..], "scale_ref": "S1" } ],
  "dimensions": [ { "text_id": "T1", "value_ft": 25.0, "path_id": "P9",
                    "measured_pt": 225.1, "implied_ppf": 9.004 } ],
  "scales": [ { "id": "S1", "method": "measure_dict|title_block|dimension_fit|manual|none",
                "points_per_foot": 9.0, "confidence": 0.93,
                "evidence": ["title_block: 1/8\"=1'-0\"", "12 dimensions fit, σ=0.4%"] } ],
  "stats": { "path_count": 0, "segment_count": 0, "text_char_count": 0,
             "image_coverage": 0.0, "is_raster": false,
             "distinct_stroke_colors": 0, "monochrome": true }
}
```

**Size note:** per-path JSON at 100k segments runs to tens of MB. v0.1 ships JSON (gzipped on R2). Before the frontend loads dense sheets, add a packed geometry block — one flat coordinate array + per-path offsets (column-oriented, deserializes to typed arrays, flatbush-friendly) — keyed by the same path ids. The schema is structured so this can be added without breaking consumers.

---

## 7. Scale detection (ordered by reliability)

1. **PDF measure dictionary** (`/VP` + `/Measure`, rectilinear) if present → use directly, confidence 1.0
2. **Title-block parse:** regex for `SCALE`, `1/8" = 1'-0"`, `1:100`, `NTS`, `AS NOTED`; combine with page size → points-per-foot
3. **Dimension fit:** pair dimension strings with dimension lines; median implied ppf; confidence from spread and count. Works when title block says NTS or the sheet was plotted scaled-to-fit
4. **Manual two-point calibration** (always available, one click)

Rules
- Always show *method + evidence* in the UI. A wrong scale is worse than Acrobat.
- If title-block and dimension-fit disagree by > 2%, flag and ask.
- **Half-size prints are the most common title-block failure:** full-size sets printed at 11×17 are off by exactly 2×. When the disagreement ratio is ≈ 2.0 or ≈ 0.5 (±5%), say "half-size print?" specifically, not a generic flag. An ANSI B page size with an ARCH D title block is a corroborating signal.
- Scale is per **viewport**, not per sheet. Default the sheet to the largest viewport's scale.

---

## 8. Layer strategy

- **OCGs present:** read via PyMuPDF (`doc.get_ocgs()`, per-path `layer` in `get_drawings()`); toggle via pdf.js optional-content config
- **OCGs absent:** cluster on `(stroke, width, dash)`; label clusters by heuristics (dimension-like text nearby → "DIMS", text-only → "ANNO", dense thin lines → "EXISTING"); user renames; persist a cluster→name map per originating firm/title-block pattern. Normalize dash arrays to pattern ratios before clustering (they are in user-space units)
- **Monochrome plots are the failure mode:** with `monochrome.ctb` everything is black and clustering degenerates to a handful of lineweight buckets. Feasibility (§13) measures color diversity per sheet; if the sample is mostly monochrome, pseudo-layers need geometry-based grouping instead, and that gets decided before Phase 2 scope is committed
- Expect ~80% accuracy on inference where color survives; UX must make a fix take seconds

---

## 9. Measurement / snapping UI

- Build a flatbush index of segment endpoints + segments per sheet at load (in a Web Worker)
- Snap priority: endpoint > intersection > midpoint > perpendicular > nearest-on-path; radius fixed in **screen px** (8–10)
- **Intersections are computed lazily:** query flatbush for segments within the snap radius of the cursor and intersect just those, per frame. Never precompute all-pairs on 100k segments
- Arc length measured along the Bézier, never chord-to-chord (fiber runs come up short otherwise)
- Tools: length, polyline (running total), area (closed polygon), count (click-to-tally with category), calibrate
- Every measurement stores: sheet_id, viewport_id, scale_id, points (pt), computed value, unit, category, author, timestamp
- Tile the overlay and cull to viewport from day one; dense site plans will choke naive rendering. The base pdf.js render is bitmapped/tiled too (§5)

---

## 10. Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React + TypeScript, Vite; pdf.js; canvas overlay; flatbush | pdf.js has OCG support and faithful rendering |
| Hosting (web) | Cloudflare Pages | Already in use |
| Storage | Cloudflare R2 (PDFs, sheet models, exports) | Cheap, S3-compatible |
| DB | Postgres (Render) | Projects, sheets, layers, calibrations, measurements — and the job queue (decision closed, §17) |
| Jobs | Postgres job table (`FOR UPDATE SKIP LOCKED`) worked by the extraction service | One backend deploy target; Cloudflare Queues only if scale demands it later |
| Extraction | Python 3.11+, FastAPI, PyMuPDF, Shapely, NumPy; Tesseract for OCR fallback (PaddleOCR is the upgrade path for small rotated text) | Best PDF geometry access |
| Exports | Annotated PDF server-side via PyMuPDF (already in stack; pdf-lib is barely maintained); xlsx via exceljs (SheetJS's npm package is frozen) | |
| Auth | Cloudflare Access (internal) → Clerk (customers) | |

Repo shape (monorepo):
```
/apps/web            React app
/services/extract    Python extraction + feasibility scripts
/packages/model      Shared TS types + JSON schema for the sheet model
/docs                this plan, ADRs
```

---

## 11. Data model (v1)

- **Org** → **Project** → **PlanSet** (upload batch, source firm, revision) → **Sheet**
- **Sheet**: page_index, size, model_uri, extractor_version, content_hash, title_block fields
- **Layer**: sheet_id, name, source (ocg|inferred|user), signature, visible_default
- **Viewport**: sheet_id, clip_bbox
- **Calibration**: viewport_id, method, points_per_foot, confidence, evidence, set_by
- **Measurement**: sheet_id, viewport_id, calibration_id, type, geometry_pt, value, unit, category, layer_id
- **Takeoff**: project_id, name, measurement_ids, exported_at
- **LayerNameMap**: org_id, firm_key, signature → name (Phase 2 corrections memory)

**Recalibration policy (normative):** measurement geometry in points is the source of truth. Stored values are a cache; when a calibration is corrected, values recompute from geometry against the new calibration, with an audit entry. A fixed calibration must never silently poison prior measurements.

---

## 12. Roadmap

| Phase | Window | Deliverable |
|---|---|---|
| 0 — Feasibility | Weeks 1–2 | Report on 10–15 real sets (see §13). Decides Phase 2 scope |
| 1 — MVP | Weeks 3–10 | §4 MVP, tested against the same sets |
| 2 | Weeks 11–18 | Pseudo-layers, dimension-fit scale, multi-viewport, search, sharing |
| 3 | After first live bid | Telecom takeoff objects, revision compare, estimating hand-off, mobile |

---

## 13. Phase 0 — Feasibility pass (first Claude Code task)

**Input:** folder of 10–15 real plan sets — carrier sets, tower-company sets, a couple of subs' sets, at least two known raster scans.

**Handling:** carrier and tower-co sets are usually under NDA. Run the feasibility pass **locally**; do not upload the sample to R2 by default.

**Script:** `services/extract/feasibility.py <folder> --out report.csv`

Per sheet, output:
- page size (+ ANSI/ARCH size class), rotation, vector vs raster (path count vs image coverage)
- OCG count + names
- extractable text chars; SHX comment (annotation) count; % vertical text
- **distinct stroke colors + monochrome flag** (sizes the pseudo-layer risk, §8)
- **PDF Producer/Creator metadata** (identifies AutoCAD version / Bluebeam / MicroStation; keys the per-firm corrections memory later)
- title-block scale text found (Y/N + string) and parsed ppf
- measure dictionary present (Y/N) and parsed ppf
- dimension candidates found (count, **source: text vs SHX comment**) and implied ppf median + spread
- path/segment count (rendering load estimate)
- **large rectangular clip regions** (viewport-candidate count — is viewport detection tractable on this sample?)
- estimated scale method that would succeed (1–4 from §7; 4 = manual only, which always succeeds — including on raster)

**Report:** one page — % vector, % with OCGs, % text-extractable, % auto-scalable (methods 1–3), % monochrome, max segment count. Include 3 worst-case sheets with notes.

**Decision gate:** if < 50% of sheets have OCGs, pseudo-layers move into MVP. If dimension-fit succeeds on > 60% of NTS sheets, prioritize it over title-block parsing. If > 60% of no-OCG sheets are monochrome, pseudo-layer clustering is redesigned (geometry-based) before being promised.

---

## 14. Suggested Claude Code kickoff sequence

1. Scaffold monorepo per §10; commit this file to `/docs/PLAN.md`
2. `packages/model`: JSON schema (the normative model, §6) + shared TS types
3. `services/extract`: PyMuPDF sheet-model extractor emitting §6 JSON — **with the golden harness from day one**: synthetic PDFs with known geometry/scale (scaled sheet, NTS + dimensions, OCG layers, rotated page, measure dict, raster scan, monochrome) asserted in CI. This is what keeps the 0.5% criterion honest across PyMuPDF upgrades
4. `feasibility.py` (§13) → run on sample folder → report
5. `apps/web`: load a PDF with pdf.js, list/toggle OCGs, render overlay canvas
6. Spatial index + snapping + length tool against extracted paths
7. Scale: title-block parse + manual calibrate; show evidence panel
8. Persist measurements; CSV export
9. Then Phase 2 backlog per §4

---

## 15. Risks

| Risk | Mitigation |
|---|---|
| Wrong scale trusted by user | Confidence + evidence always visible; disagreement flags (half-size special-cased); calibrate is one click |
| Sets plotted without layers | Pseudo-layer clustering; corrections memory per firm |
| **Monochrome plots gut color clustering** | Measured in feasibility before Phase 2 scope commits; geometry-based grouping as fallback |
| Dense sheets slow | Tiled/culled overlay **and** bitmapped base render; index built once per sheet; Web Worker for index build; packed geometry encoding for the model |
| SHX text unreadable | Comment extraction; OCR region fallback; treat as Phase 2 |
| Raster sets | Explicit "scanned sheet" mode; manual calibration only; no false promises |
| Malformed PDFs crash extraction | Poison-job handling: park as `extraction_failed` after N crashes, never block the queue |

---

## 16. Positioning

Bluebeam Revu is the incumbent: capable, per-seat priced, general-construction oriented, manual calibration and manual takeoff objects. PlanSwift, STACK, Togal.AI are estimator-focused and generic. The opening is a **telecom/fiber-native** takeoff tool that auto-detects scale, understands splice points and conduit runs, and flows quantities into the contractor's estimating and ops stack.

---

## 17. Open decisions

- ~~Postgres on Render vs. D1~~ → **Closed: Postgres.** D1 is SQLite (no PostGIS, weak JSON), and Render already hosts the worker
- ~~Internal tool first vs. customer-facing from day one~~ → **Closed: internal-first (Pro-Tel estimating).** Real sets legally in hand, a captive user for the corrections-memory loop, no auth/tenancy pressure in MVP
- Sample plan sets: which 10–15, and who owns gathering them
- Product name — working name **ElevationPDF** (the repo)

---

## 18. Revision notes

**v1 → v2** (2026-09-17, after review):
- §6: coordinate convention made normative (sheet space: displayed page, top-left origin, y-down, points); `stroke`/`fill` split; text `dir`/`rotation_deg`; `content_hash`; `stats` block; packed-geometry note; JSON Schema in `/packages/model` is the source of truth
- §5/§10: Cloudflare Queues dropped from MVP in favor of a Postgres `SKIP LOCKED` job table (one backend deploy target); poison-job handling; annotated-PDF export moved server-side (PyMuPDF); SheetJS → exceljs; bitmapped base render called out
- §4: progressive-availability acceptance criterion; wrong-scale-with-confidence threshold defined
- §7: half-size print special case
- §8/§15: monochrome-plot risk named and gated on feasibility data
- §13: added columns (colors/monochrome, Producer metadata, clip viewport candidates, vertical text, dimension source); NDA note — run feasibility locally
- §14: golden-test harness pulled into the extractor step
- §11: recalibration policy
- §17: Postgres and internal-first decisions closed
