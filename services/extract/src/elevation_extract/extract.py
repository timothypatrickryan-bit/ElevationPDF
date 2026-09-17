"""Rebuild a structured sheet model from one PDF page (PLAN §6).

All output coordinates are in sheet space: displayed page (CropBox with
/Rotate applied), origin top-left, y down, PDF points. PyMuPDF's extraction
APIs return CropBox-normalized but UNROTATED coordinates, so this module
applies `page.rotation_matrix` exactly once, here and nowhere else.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

import pymupdf

from . import EXTRACTOR_VERSION
from .dimensions import find_dimension_candidates
from .geometry import GridIndex, path_segments, segment_length
from .scale import (
    flag_half_size,
    looks_like_scale_text,
    parse_scale_text,
    read_measure_dict,
)

Point = tuple[float, float]

_MERGE_TOL = 1e-4
_VIEWPORT_MIN_FRAC = 0.03  # clip must cover >=3% of the page to be a viewport candidate
_VIEWPORT_MAX_FRAC = 0.98  # page-frame clips are boilerplate, not viewports
_RASTER_COVERAGE = 0.8
_RASTER_MAX_PATHS = 50
_MONOCHROME_FRAC = 0.95
_DIMFIT_MIN_INLIERS = 4
_DIMFIT_MAX_SPREAD_PCT = 5.0
_TITLE_REGION_W = 0.25  # right strip fraction
_TITLE_REGION_H = 0.15  # bottom strip fraction


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _hex_color(c) -> str | None:
    if c is None:
        return None
    try:
        vals = list(c)
    except TypeError:
        vals = [c]
    if len(vals) == 1:
        vals = vals * 3
    if len(vals) == 4:  # naive CMYK -> RGB
        cy, mg, ye, k = vals
        vals = [(1 - cy) * (1 - k), (1 - mg) * (1 - k), (1 - ye) * (1 - k)]
    r, g, b = (max(0.0, min(1.0, float(v))) for v in vals[:3])
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def _parse_dashes(dashes) -> list[float]:
    if not dashes:
        return []
    s = str(dashes)
    lb, rb = s.find("["), s.find("]")
    if lb == -1 or rb == -1:
        return []
    toks = s[lb + 1 : rb].split()
    out = []
    for t in toks:
        try:
            out.append(float(t))
        except ValueError:
            return []
    return out


def _is_grayish(hexcolor: str | None) -> bool:
    if hexcolor is None:
        return True
    r, g, b = (int(hexcolor[i : i + 2], 16) for i in (1, 3, 5))
    return max(r, g, b) - min(r, g, b) <= 16


class _Transform:
    """Unrotated fitz space -> sheet space (rotation applied)."""

    def __init__(self, page: pymupdf.Page) -> None:
        self.m = page.rotation_matrix

    def point(self, p) -> list[float]:
        q = pymupdf.Point(p) * self.m
        return [round(q.x, 3), round(q.y, 3)]

    def rect(self, r) -> list[float]:
        q = pymupdf.Rect(r) * self.m
        q.normalize()
        return [round(q.x0, 3), round(q.y0, 3), round(q.x1, 3), round(q.y1, 3)]

    def direction(self, d: tuple[float, float]) -> tuple[float, float]:
        m = self.m
        x = d[0] * m.a + d[1] * m.c
        y = d[0] * m.b + d[1] * m.d
        n = math.hypot(x, y) or 1.0
        return (round(x / n, 6), round(y / n, 6))


def _rotation_deg(direction: tuple[float, float]) -> float:
    # sheet space is y-down; positive = CCW as seen on screen
    return round(math.degrees(math.atan2(-direction[1], direction[0])) % 360.0, 1)


# ---------------------------------------------------------------------------
# drawings -> paths + viewport candidates
# ---------------------------------------------------------------------------

def _emit_run(paths: list[dict], run_kind: str, run_pts: list[Point], style: dict) -> None:
    if not run_pts:
        return
    if run_kind == "l":
        kind = "line" if len(run_pts) == 2 else "polyline"
    else:
        kind = "bezier"
    paths.append({"kind": kind, "pts": run_pts, **style})


def _drawing_to_paths(drawing: dict, tf: _Transform) -> list[dict]:
    style = {
        "stroke": _hex_color(drawing.get("color")),
        "fill": _hex_color(drawing.get("fill")),
        "width": drawing.get("width"),
        "dash": _parse_dashes(drawing.get("dashes")),
        "closed": bool(drawing.get("closePath")),
        "layer_name": drawing.get("layer") or None,
    }
    paths: list[dict] = []
    run_kind: str | None = None
    run_pts: list[Point] = []

    def flush() -> None:
        nonlocal run_kind, run_pts
        if run_kind is not None:
            _emit_run(paths, run_kind, run_pts, style)
        run_kind, run_pts = None, []

    for item in drawing.get("items") or []:
        op = item[0]
        if op == "l":
            p1, p2 = tf.point(item[1]), tf.point(item[2])
            if run_kind == "l" and run_pts and abs(run_pts[-1][0] - p1[0]) < _MERGE_TOL and abs(run_pts[-1][1] - p1[1]) < _MERGE_TOL:
                run_pts.append(p2)
            else:
                flush()
                run_kind, run_pts = "l", [p1, p2]
        elif op == "c":
            cps = [tf.point(item[i]) for i in (1, 2, 3, 4)]
            if run_kind == "c" and run_pts and abs(run_pts[-1][0] - cps[0][0]) < _MERGE_TOL and abs(run_pts[-1][1] - cps[0][1]) < _MERGE_TOL:
                run_pts.extend(cps[1:])
            else:
                flush()
                run_kind, run_pts = "c", cps
        elif op == "re":
            flush()
            r = pymupdf.Rect(item[1])
            corners = [tf.point(p) for p in (r.top_left, r.top_right, r.bottom_right, r.bottom_left)]
            paths.append({"kind": "rect", "pts": corners, **{**style, "closed": True}})
        elif op == "qu":
            flush()
            q = item[1]
            corners = [tf.point(p) for p in (q.ul, q.ur, q.lr, q.ll)]
            paths.append({"kind": "quad", "pts": corners, **{**style, "closed": True}})
    flush()
    return paths


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def extract_sheet(doc: pymupdf.Document, page_index: int, sheet_id: str | None = None) -> dict[str, Any]:
    """Extract one page into a sheet-model dict (see packages/model schema)."""
    page = doc[page_index]
    tf = _Transform(page)
    rect = page.rect  # displayed
    page_area = abs(rect.width * rect.height) or 1.0

    # --- provenance -------------------------------------------------------
    try:
        contents = page.read_contents() or b""
    except Exception:
        contents = b""
    content_hash = "sha256:" + hashlib.sha256(contents).hexdigest()

    user_unit = 1.0
    try:
        t, v = doc.xref_get_key(page.xref, "UserUnit")
        if t in ("int", "real") and v:
            user_unit = float(v)
    except Exception:
        pass

    meta = doc.metadata or {}

    # --- layers: capture plot-time visibility, then force everything on ---
    # get_drawings() honors OCG visibility, so a layer plotted hidden would
    # otherwise vanish from the model. default_visible keeps the original
    # state; the UI-config toggle makes the content extractable.
    ocgs = {}
    try:
        ocgs = doc.get_ocgs() or {}
    except Exception:
        pass
    ocg_visibility = {v["name"]: bool(v.get("on", True)) for v in ocgs.values()}
    if ocgs:
        try:
            for cfg in doc.layer_ui_configs():
                doc.set_layer_ui_config(cfg["number"], action=0)  # 0 = on
        except Exception:
            pass

    # --- drawings: paths, clips ------------------------------------------
    try:
        drawings = page.get_drawings(extended=True)
    except Exception:
        drawings = page.get_drawings()

    raw_paths: list[dict] = []
    clip_rects: list[list[float]] = []
    for d in drawings:
        dtype = d.get("type")
        if dtype == "clip":
            scissor = d.get("scissor")
            if scissor is not None:
                r = pymupdf.Rect(scissor)
                frac = abs(r.width * r.height) / page_area
                if _VIEWPORT_MIN_FRAC <= frac <= _VIEWPORT_MAX_FRAC:
                    clip_rects.append(tf.rect(r))
        elif dtype in ("s", "f", "fs", None):
            if d.get("items"):
                raw_paths.extend(_drawing_to_paths(d, tf))

    # layers: OCGs that actually appear on this page
    layer_ids: dict[str, str] = {}
    layers: list[dict] = []
    for p in raw_paths:
        name = p.pop("layer_name", None)
        if name and name not in layer_ids:
            lid = f"L{len(layer_ids) + 1}"
            layer_ids[name] = lid
            layers.append(
                {
                    "id": lid,
                    "name": name,
                    "source": "ocg",
                    "default_visible": ocg_visibility.get(name, True),
                    "signature": None,
                }
            )
        p["layer_id"] = layer_ids.get(name) if name else None

    paths = [{"id": f"P{i + 1}", **p} for i, p in enumerate(raw_paths)]

    # --- viewports (deduped clip candidates) ------------------------------
    seen: set[tuple] = set()
    viewports: list[dict] = []
    for r in clip_rects:
        key = tuple(round(v, 1) for v in r)
        if key in seen:
            continue
        seen.add(key)
        viewports.append({"id": f"V{len(viewports) + 1}", "clip_bbox": r, "scale_ref": None})

    # --- text -------------------------------------------------------------
    texts: list[dict] = []
    lines_for_scale: list[str] = []  # line-joined strings (scale text splits across spans)
    line_boxes: list[list[float]] = []
    try:
        tdict = page.get_text("dict")
    except Exception:
        tdict = {"blocks": []}
    for block in tdict.get("blocks", []):
        for line in block.get("lines", []):
            direction = tf.direction(tuple(line.get("dir", (1, 0))))
            span_strs = []
            for span in line.get("spans", []):
                s = span.get("text", "")
                if not s.strip():
                    continue
                span_strs.append(s)
                texts.append(
                    {
                        "id": f"T{len(texts) + 1}",
                        "str": s,
                        "bbox": tf.rect(span.get("bbox")),
                        "dir": [direction[0], direction[1]],
                        "rotation_deg": _rotation_deg(direction),
                        "font": span.get("font"),
                        "size": span.get("size"),
                        "layer_id": None,
                        "source": "text",
                    }
                )
            if span_strs:
                joined = " ".join(span_strs)
                lines_for_scale.append(joined)
                line_boxes.append(tf.rect(line.get("bbox", block.get("bbox"))))

    # annotation comments (AutoCAD 2016+ stores SHX text this way)
    try:
        annots = list(page.annots() or [])
    except Exception:
        annots = []
    for a in annots:
        content = (a.info or {}).get("content", "")
        if content and content.strip():
            texts.append(
                {
                    "id": f"T{len(texts) + 1}",
                    "str": content.strip(),
                    "bbox": tf.rect(a.rect),
                    "dir": [1.0, 0.0],
                    "rotation_deg": 0.0,
                    "font": None,
                    "size": None,
                    "layer_id": None,
                    "source": "shx_comment",
                }
            )

    # --- title block ------------------------------------------------------
    W, H = rect.width, rect.height
    corner = (W * (1 - _TITLE_REGION_W), H * (1 - _TITLE_REGION_H))

    def in_title_region(bb: list[float]) -> bool:
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        return cx >= W * (1 - _TITLE_REGION_W) or cy >= H * (1 - _TITLE_REGION_H)

    def in_corner(bb: list[float]) -> bool:
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        return cx >= corner[0] and cy >= corner[1]

    scale_text: str | None = None
    title_ppf: float | None = None
    title_kind: str | None = None
    candidates = [
        (s, bb) for s, bb in zip(lines_for_scale, line_boxes) if looks_like_scale_text(s)
    ]
    # prefer title-block region, then anything mentioning SCALE, then any match
    ranked = (
        [c for c in candidates if in_title_region(c[1])]
        + [c for c in candidates if "SCALE" in c[0].upper()]
        + candidates
    )
    for s, _bb in ranked:
        parsed = parse_scale_text(s)
        if parsed:
            scale_text = s.strip()
            title_ppf = parsed.ppf
            title_kind = parsed.kind
            break

    sheet_no: str | None = None
    best_d = None
    sheet_no_re = re.compile(r"^[A-Z]{1,3}[-.]?\d{1,4}(?:\.\d+)?[A-Z]?$")
    for t in texts:
        if t["source"] != "text" or not in_corner(t["bbox"]):
            continue
        cand = t["str"].strip()
        if sheet_no_re.match(cand):
            bb = t["bbox"]
            d = math.hypot(W - (bb[0] + bb[2]) / 2, H - (bb[1] + bb[3]) / 2)
            if best_d is None or d < best_d:
                best_d, sheet_no = d, cand

    title_block = None
    if scale_text or sheet_no:
        title_block = {"sheet_no": sheet_no, "title": None, "scale_text": scale_text, "bbox": None}

    # --- segments + dimension fit ----------------------------------------
    index = GridIndex(cell=max(W, H) / 20.0)
    seg_path_ids: list[str] = []
    segment_count = 0
    for p in paths:
        for seg in path_segments(p["kind"], p["pts"], p["closed"]):
            if segment_length(seg) <= 0:
                continue
            segment_count += 1
            index.add(seg)
            seg_path_ids.append(p["id"])

    dim_texts = [t for t in texts if t["source"] in ("text", "shx_comment")]
    fit = find_dimension_candidates(dim_texts, index, seg_path_ids)
    dimensions = [
        {
            "text_id": c.text_id,
            "value_ft": c.value_ft,
            "path_id": c.path_id,
            "measured_pt": c.measured_pt,
            "implied_ppf": c.implied_ppf,
        }
        for c in fit.candidates
    ]

    # --- scales (PLAN §7 order) ------------------------------------------
    scales: list[dict] = []

    measure = read_measure_dict(doc, page)
    if measure and measure.ppf:
        scales.append(
            {
                "id": f"S{len(scales) + 1}",
                "method": "measure_dict",
                "points_per_foot": round(measure.ppf, 5),
                "confidence": 1.0,
                "evidence": [measure.evidence],
            }
        )

    title_scale_idx: int | None = None
    if title_ppf:
        title_scale_idx = len(scales)
        scales.append(
            {
                "id": f"S{len(scales) + 1}",
                "method": "title_block",
                "points_per_foot": round(title_ppf, 5),
                "confidence": 0.7,
                "evidence": [f"title_block: {scale_text}"],
            }
        )

    dim_ppf: float | None = None
    if (
        fit.median_ppf
        and fit.spread_pct is not None
        and fit.inlier_count >= _DIMFIT_MIN_INLIERS
        and fit.spread_pct <= _DIMFIT_MAX_SPREAD_PCT
    ):
        dim_ppf = fit.median_ppf
        confidence = min(0.95, 0.55 + 0.05 * fit.inlier_count)
        scales.append(
            {
                "id": f"S{len(scales) + 1}",
                "method": "dimension_fit",
                "points_per_foot": round(dim_ppf, 5),
                "confidence": round(confidence, 2),
                "evidence": [
                    f"{fit.inlier_count} dimensions fit, spread {fit.spread_pct:.1f}%"
                ],
            }
        )

    # cross-check: title block vs dimension fit (PLAN §7)
    if title_scale_idx is not None and title_ppf and dim_ppf:
        err = abs(title_ppf - dim_ppf) / dim_ppf
        if err > 0.02:
            entry = scales[title_scale_idx]
            entry["confidence"] = 0.4
            note = flag_half_size(title_ppf, dim_ppf)
            entry["evidence"].append(
                note or f"disagrees with dimension fit by {err * 100:.1f}%"
            )

    if title_kind == "nts" and not scales:
        scales.append(
            {
                "id": "S1",
                "method": "none",
                "points_per_foot": None,
                "confidence": 0.0,
                "evidence": [f"title_block: {scale_text}"],
            }
        )

    # --- stats ------------------------------------------------------------
    image_area = 0.0
    try:
        for img in page.get_images(full=True):
            for r in page.get_image_rects(img[0]):
                clipped = pymupdf.Rect(r) & rect
                image_area += abs(clipped.width * clipped.height)
    except Exception:
        pass
    image_coverage = min(1.0, image_area / page_area)

    stroked = [p["stroke"] for p in paths if p["stroke"] is not None]
    distinct = len(set(stroked))
    gray = sum(1 for c in stroked if _is_grayish(c))
    monochrome = bool(stroked) and gray / len(stroked) >= _MONOCHROME_FRAC
    text_chars = sum(len(t["str"]) for t in texts if t["source"] == "text")
    is_raster = image_coverage >= _RASTER_COVERAGE and len(paths) < _RASTER_MAX_PATHS

    return {
        "sheet_id": sheet_id or f"p{page_index}",
        "page_index": page_index,
        "size_pt": [round(W, 3), round(H, 3)],
        "rotation": page.rotation,
        "user_unit": user_unit,
        "extractor_version": EXTRACTOR_VERSION,
        "content_hash": content_hash,
        "producer": meta.get("producer") or None,
        "creator": meta.get("creator") or None,
        "title_block": title_block,
        "layers": layers,
        "paths": paths,
        "texts": texts,
        "viewports": viewports,
        "dimensions": dimensions,
        "scales": scales,
        "stats": {
            "path_count": len(paths),
            "segment_count": segment_count,
            "text_char_count": text_chars,
            "image_coverage": round(image_coverage, 4),
            "is_raster": is_raster,
            "distinct_stroke_colors": distinct,
            "monochrome": monochrome,
        },
    }
