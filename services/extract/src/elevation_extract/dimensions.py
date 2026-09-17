"""Dimension text parsing and naive text↔line pairing (feasibility-grade).

The pairing here is deliberately simple: for each dimension-valued text, find
the nearest sufficiently long, roughly text-aligned segment and derive an
implied points-per-foot. The robust version (extension-line pairing,
arrowhead detection) is Phase 2; this exists to measure whether dimension-fit
is worth building (PLAN §13).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .geometry import (
    GridIndex,
    Segment,
    angle_diff_deg,
    mad,
    median,
    point_segment_distance,
    segment_angle_deg,
    segment_length,
)

# Sanity range for implied scale: 1"=100' (0.72 ppf) .. 3"=1'-0" (216 ppf)
PPF_MIN, PPF_MAX = 0.5, 300.0

_QUOTES = {
    "’": "'", "′": "'",
    "”": '"', "″": '"',
    "–": "-", "—": "-",
}

# 25'-0"  3'-6 1/2"  120'-0
_FT_IN_RE = re.compile(
    r"(?<![\d/.])(\d{1,4})\s*'\s*-?\s*(\d{1,2})(?:\s+(\d{1,2})\s*/\s*(\d{1,2}))?\s*\"?(?!\S*=)"
)
# bare feet: 120'  12.5'
_FT_RE = re.compile(r"(?<![\d/.])(\d{1,4}(?:\.\d+)?)\s*'(?!\s*-?\s*\d)")
# bare inches: 6"  4 1/2"
_IN_RE = re.compile(r"(?<![\d/.'])(\d{1,3})(?:\s+(\d{1,2})\s*/\s*(\d{1,2}))?\s*\"")


def parse_dim_text(s: str) -> float | None:
    """Parse a dimension-like string into feet. None when not a dimension.

    Scale strings ('1/8" = 1\'-0"') are excluded — anything containing '='.
    """
    for k, v in _QUOTES.items():
        s = s.replace(k, v)
    if "=" in s or ":" in s:
        return None

    m = _FT_IN_RE.search(s)
    if m:
        ft = float(m.group(1))
        inches = float(m.group(2))
        if m.group(3):
            inches += float(m.group(3)) / float(m.group(4))
        if inches < 12:
            return ft + inches / 12.0

    m = _FT_RE.search(s)
    if m:
        return float(m.group(1))

    m = _IN_RE.search(s)
    if m:
        inches = float(m.group(1))
        if m.group(2):
            inches += float(m.group(2)) / float(m.group(3))
        if 0 < inches:
            return inches / 12.0

    return None


@dataclass
class DimCandidate:
    text_id: str
    value_ft: float
    path_id: str | None
    measured_pt: float | None
    implied_ppf: float | None


@dataclass
class DimFit:
    candidates: list[DimCandidate]
    median_ppf: float | None
    spread_pct: float | None  # MAD / median * 100
    inlier_count: int  # candidates within 2% of the median


def _text_angle_deg(dir_vec: tuple[float, float]) -> float:
    import math

    return math.degrees(math.atan2(-dir_vec[1], dir_vec[0])) % 180.0


def find_dimension_candidates(
    texts: list[dict],
    index: GridIndex,
    seg_path_ids: list[str],
) -> DimFit:
    """texts: sheet-model text dicts. index: GridIndex over flattened segments.
    seg_path_ids: path id per segment (parallel to the index)."""
    candidates: list[DimCandidate] = []

    for t in texts:
        value = parse_dim_text(t["str"])
        if value is None or value <= 0:
            continue
        bbox = t["bbox"]
        cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
        h = max(abs(bbox[3] - bbox[1]), abs(bbox[2] - bbox[0]) / max(len(t["str"]), 1), 4.0)
        t_angle = _text_angle_deg(tuple(t["dir"]))
        radius = 6.0 * h

        best: tuple[float, int] | None = None  # (distance, seg_idx)
        for idx in index.near(cx, cy, radius):
            seg = index.segment(idx)
            if segment_length(seg) < 3.0 * h:
                continue
            if angle_diff_deg(segment_angle_deg(seg), t_angle) > 20.0:
                continue
            d = point_segment_distance(cx, cy, seg)
            if d > radius:
                continue
            if best is None or d < best[0]:
                best = (d, idx)

        if best is None:
            candidates.append(DimCandidate(t["id"], value, None, None, None))
            continue

        seg = index.segment(best[1])
        length = segment_length(seg)
        ppf = length / value
        if PPF_MIN <= ppf <= PPF_MAX:
            candidates.append(
                DimCandidate(t["id"], value, seg_path_ids[best[1]], round(length, 3), round(ppf, 5))
            )
        else:
            candidates.append(DimCandidate(t["id"], value, None, None, None))

    implied = [c.implied_ppf for c in candidates if c.implied_ppf]
    if not implied:
        return DimFit(candidates, None, None, 0)

    med = median(implied)
    spread = (mad(implied, med) / med * 100.0) if med else None
    inliers = sum(1 for v in implied if abs(v - med) / med <= 0.02)
    return DimFit(candidates, med, spread, inliers)
