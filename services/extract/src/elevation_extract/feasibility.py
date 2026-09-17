"""Phase 0 feasibility pass (PLAN §13).

Walks every page of every PDF in a folder, extracts the sheet model, and
emits per-sheet metrics as CSV plus a printed one-page summary with the
decision-gate stats. Plan sets under NDA stay local — this is a CLI on
purpose.

Usage:  feasibility.py <folder> --out report.csv [--json-dir DIR]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import pymupdf

from .extract import extract_sheet
from .scale import read_measure_dict

COLUMNS = [
    "file",
    "page",
    "width_pt",
    "height_pt",
    "size_class",
    "rotation",
    "producer",
    "creator",
    "is_raster",
    "image_coverage_pct",
    "path_count",
    "segment_count",
    "distinct_stroke_colors",
    "monochrome",
    "ocg_count",
    "ocg_names",
    "text_chars",
    "shx_comment_count",
    "vertical_text_pct",
    "scale_text",
    "title_block_ppf",
    "has_measure_dict",
    "measure_ppf",
    "dim_candidate_count",
    "dim_paired_count",
    "dim_ppf_median",
    "dim_spread_pct",
    "viewport_candidates",
    "predicted_method",
    "extract_seconds",
]

# (name, width_pt, height_pt) at 72 dpi, portrait orientation
_SIZES = [
    ("ANSI A", 612, 792), ("ANSI B", 792, 1224), ("ANSI C", 1224, 1584),
    ("ANSI D", 1584, 2448), ("ANSI E", 2448, 3168),
    ("ARCH A", 648, 864), ("ARCH B", 864, 1296), ("ARCH C", 1296, 1728),
    ("ARCH D", 1728, 2592), ("ARCH E", 2592, 3456), ("ARCH E1", 2160, 3024),
    ("A4", 595, 842), ("A3", 842, 1191), ("A2", 1191, 1684),
    ("A1", 1684, 2384), ("A0", 2384, 3370),
]


def size_class(w: float, h: float) -> str:
    lo, hi = sorted((w, h))
    for name, sw, sh in _SIZES:
        if abs(lo - sw) <= 4 and abs(hi - sh) <= 4:
            return name
    return "custom"


def sheet_row(model: dict, file_name: str, has_measure: bool, measure_ppf: float | None,
              extract_seconds: float) -> dict:
    texts = model["texts"]
    real_texts = [t for t in texts if t["source"] == "text"]
    vertical = sum(1 for t in real_texts if 45 <= (t["rotation_deg"] % 180) <= 135)
    vertical_pct = round(100.0 * vertical / len(real_texts), 1) if real_texts else 0.0

    scales = {s["method"]: s for s in model["scales"]}
    title_ppf = scales.get("title_block", {}).get("points_per_foot")
    dim_scale = scales.get("dimension_fit")

    dims = model["dimensions"]
    paired = [d for d in dims if d["implied_ppf"]]
    implied = [d["implied_ppf"] for d in paired]
    dim_median = None
    dim_spread = None
    if implied:
        from .geometry import mad, median

        dim_median = round(median(implied), 3)
        m = median(implied)
        dim_spread = round(mad(implied, m) / m * 100.0, 2) if m else None

    if "measure_dict" in scales:
        method = 1
    elif title_ppf:
        method = 2
    elif dim_scale:
        method = 3
    else:
        method = 4  # manual only — always succeeds, including on raster

    st = model["stats"]
    return {
        "file": file_name,
        "page": model["page_index"],
        "width_pt": model["size_pt"][0],
        "height_pt": model["size_pt"][1],
        "size_class": size_class(*model["size_pt"]),
        "rotation": model["rotation"],
        "producer": model.get("producer") or "",
        "creator": model.get("creator") or "",
        "is_raster": st["is_raster"],
        "image_coverage_pct": round(st["image_coverage"] * 100, 1),
        "path_count": st["path_count"],
        "segment_count": st["segment_count"],
        "distinct_stroke_colors": st["distinct_stroke_colors"],
        "monochrome": st["monochrome"],
        "ocg_count": len(model["layers"]),
        "ocg_names": ";".join(l["name"] for l in model["layers"]),
        "text_chars": st["text_char_count"],
        "shx_comment_count": sum(1 for t in texts if t["source"] == "shx_comment"),
        "vertical_text_pct": vertical_pct,
        "scale_text": (model.get("title_block") or {}).get("scale_text") or "",
        "title_block_ppf": round(title_ppf, 4) if title_ppf else "",
        "has_measure_dict": has_measure,
        "measure_ppf": round(measure_ppf, 4) if measure_ppf else "",
        "dim_candidate_count": len(dims),
        "dim_paired_count": len(paired),
        "dim_ppf_median": dim_median if dim_median is not None else "",
        "dim_spread_pct": dim_spread if dim_spread is not None else "",
        "viewport_candidates": len(model["viewports"]),
        "predicted_method": method,
        "extract_seconds": round(extract_seconds, 2),
    }


def run_folder(folder: Path, json_dir: Path | None = None) -> list[dict]:
    rows: list[dict] = []
    pdfs = sorted(p for p in folder.rglob("*.pdf") if p.is_file())
    if not pdfs:
        print(f"no PDFs found under {folder}", file=sys.stderr)
        return rows

    for pdf_path in pdfs:
        rel = str(pdf_path.relative_to(folder))
        try:
            doc = pymupdf.open(pdf_path)
        except Exception as e:  # noqa: BLE001 — a bad file is a data point, not a crash
            print(f"  !! {rel}: failed to open ({e})", file=sys.stderr)
            rows.append({c: "" for c in COLUMNS} | {"file": rel, "page": -1, "predicted_method": "error"})
            continue
        for i in range(doc.page_count):
            t0 = time.perf_counter()
            try:
                model = extract_sheet(doc, i, sheet_id=f"{rel}:p{i}")
                measure = read_measure_dict(doc, doc[i])
                dt = time.perf_counter() - t0
                rows.append(
                    sheet_row(model, rel, has_measure=measure is not None,
                              measure_ppf=measure.ppf if measure else None,
                              extract_seconds=dt)
                )
                if json_dir is not None:
                    out = json_dir / f"{rel.replace('/', '__')}.p{i}.sheetmodel.json"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(model, indent=1))
            except Exception as e:  # noqa: BLE001
                print(f"  !! {rel} p{i}: extraction failed ({e})", file=sys.stderr)
                rows.append({c: "" for c in COLUMNS} | {"file": rel, "page": i, "predicted_method": "error"})
        doc.close()
    return rows


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.0f}%" if d else "n/a"


def summarize(rows: list[dict]) -> str:
    ok = [r for r in rows if r["predicted_method"] != "error"]
    n = len(ok)
    if n == 0:
        return "No sheets extracted."
    vector = [r for r in ok if not r["is_raster"]]
    with_ocgs = [r for r in ok if (r["ocg_count"] or 0) > 0]
    with_text = [r for r in ok if (r["text_chars"] or 0) >= 100]
    auto = [r for r in ok if r["predicted_method"] in (1, 2, 3)]
    mono = [r for r in vector if r["monochrome"]]
    max_seg = max((r["segment_count"] or 0) for r in ok)

    # worst-case score: raster, no OCGs, no text, manual-only scale
    def badness(r: dict) -> int:
        return (
            (3 if r["is_raster"] else 0)
            + (1 if not r["ocg_count"] else 0)
            + (1 if (r["text_chars"] or 0) < 100 else 0)
            + (1 if r["predicted_method"] == 4 else 0)
        )

    worst = sorted(ok, key=badness, reverse=True)[:3]

    lines = [
        "── Feasibility summary ─────────────────────────────",
        f"sheets analyzed:        {n}  (errors: {len(rows) - n})",
        f"vector sheets:          {_pct(len(vector), n)}",
        f"with OCG layers:        {_pct(len(with_ocgs), n)}",
        f"text-extractable:       {_pct(len(with_text), n)}   (>=100 chars)",
        f"auto-scalable (1-3):    {_pct(len(auto), n)}",
        f"monochrome (of vector): {_pct(len(mono), len(vector))}",
        f"max segment count:      {max_seg}",
        "",
        "decision gates (PLAN §13):",
        f"  OCGs on <50% of sheets -> pseudo-layers into MVP:      {'YES' if len(with_ocgs) < 0.5 * n else 'no'}",
        f"  monochrome >60% of no-OCG vector sheets -> redesign:   "
        f"{'YES' if vector and len([r for r in vector if not r['ocg_count'] and r['monochrome']]) > 0.6 * max(len([r for r in vector if not r['ocg_count']]), 1) else 'no'}",
        "",
        "worst-case sheets:",
    ]
    for r in worst:
        notes = []
        if r["is_raster"]:
            notes.append("raster")
        if not r["ocg_count"]:
            notes.append("no OCGs")
        if (r["text_chars"] or 0) < 100:
            notes.append("little text")
        if r["predicted_method"] == 4:
            notes.append("manual scale only")
        lines.append(f"  {r['file']} p{r['page']}: {', '.join(notes) or 'ok'}")
    lines.append("────────────────────────────────────────────────────")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", type=Path, help="folder of plan-set PDFs (searched recursively)")
    ap.add_argument("--out", type=Path, default=Path("report.csv"), help="CSV output path")
    ap.add_argument("--json-dir", type=Path, default=None,
                    help="also dump each sheet model as JSON into this directory")
    args = ap.parse_args(argv)

    if not args.folder.is_dir():
        print(f"not a directory: {args.folder}", file=sys.stderr)
        return 2

    rows = run_folder(args.folder, args.json_dir)
    if not rows:
        return 1

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})

    print(summarize(rows))
    print(f"\nwrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
