"""Scale detection: title-block scale strings and PDF /VP measure dictionaries.

points_per_foot (ppf) = paper_inches_per_real_foot * 72   (PLAN §3)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedScale:
    kind: str  # "arch" | "ratio" | "nts" | "as_noted"
    ppf: float | None
    text: str


# Normalize typography before matching.
_QUOTES = {
    "’": "'", "′": "'", "ʹ": "'",
    "”": '"', "″": '"', "“": '"',
    "–": "-", "—": "-",
}

_NUM = r"(?:\d+(?:\.\d+)?)"
_FRAC = r"(?:\d+\s*/\s*\d+)"
_MIXED = rf"(?:\d+\s+{_FRAC}|{_FRAC}|{_NUM})"

# e.g.  1/8" = 1'-0"   3/16"=1'-0"   1" = 20'   1 in = 8 ft   1/2 inch = 1 foot
_ARCH_RE = re.compile(
    rf"({_MIXED})\s*(?:\"|in(?:ch(?:es)?)?\.?)\s*=\s*({_NUM})\s*(?:'|ft\.?|foot|feet)"
    rf"(?:\s*-\s*({_NUM})\s*\"?)?",
    re.IGNORECASE,
)

# e.g.  1:100   1 : 50   (metric ratio; 1 paper unit = N real units)
_RATIO_RE = re.compile(r"\b1\s*:\s*(\d{1,6})\b")

_NTS_RE = re.compile(r"\bN\.?\s?T\.?\s?S\.?\b|NOT\s+TO\s+SCALE", re.IGNORECASE)
_AS_NOTED_RE = re.compile(r"AS\s+NOTED|AS\s+SHOWN|SCALE\s*:?\s*VARIES", re.IGNORECASE)


def _norm(s: str) -> str:
    for k, v in _QUOTES.items():
        s = s.replace(k, v)
    return s


def _num(tok: str) -> float:
    tok = tok.strip()
    m = re.fullmatch(r"(\d+)\s+(\d+)\s*/\s*(\d+)", tok)
    if m:
        return float(m.group(1)) + float(m.group(2)) / float(m.group(3))
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", tok)
    if m:
        return float(m.group(1)) / float(m.group(2))
    return float(tok)


def parse_scale_text(s: str) -> ParsedScale | None:
    """Parse a scale string into points-per-foot. Returns None when nothing
    scale-like is present. NTS / AS NOTED parse with ppf=None."""
    s = _norm(s)

    if _NTS_RE.search(s):
        return ParsedScale("nts", None, s.strip())
    if _AS_NOTED_RE.search(s):
        return ParsedScale("as_noted", None, s.strip())

    m = _ARCH_RE.search(s)
    if m:
        paper_in = _num(m.group(1))
        real_ft = _num(m.group(2))
        if m.group(3):
            real_ft += _num(m.group(3)) / 12.0
        if paper_in > 0 and real_ft > 0:
            return ParsedScale("arch", paper_in * 72.0 / real_ft, m.group(0).strip())

    m = _RATIO_RE.search(s)
    if m:
        n = int(m.group(1))
        if n > 1:
            # 1 paper unit = n real units -> one real foot = 12/n paper inches
            return ParsedScale("ratio", 864.0 / n, m.group(0).strip())

    return None


def looks_like_scale_text(s: str) -> bool:
    s = _norm(s)
    return bool(
        _ARCH_RE.search(s)
        or _RATIO_RE.search(s)
        or _NTS_RE.search(s)
        or _AS_NOTED_RE.search(s)
    )


# ---------------------------------------------------------------------------
# /VP + /Measure (PDF 1.6 viewport measure dictionaries — Bluebeam writes
# these on calibration; native AutoCAD output generally does not).
# ---------------------------------------------------------------------------

# U units per foot: real_ft = measured_U / U_PER_FT[U]
_U_PER_FT = {
    "ft": 1.0, "feet": 1.0, "foot": 1.0, "'": 1.0,
    "in": 12.0, "inch": 12.0, "inches": 12.0, '"': 12.0,
    "yd": 1.0 / 3.0,
    "mi": 1.0 / 5280.0,
    "m": 0.3048, "meter": 0.3048, "meters": 0.3048,
    "cm": 30.48,
    "mm": 304.8,
    "km": 0.0003048,
}

_C_RE = re.compile(r"/U\s*\(([^)]*)\)[^>]*?/C\s+([0-9.eE+-]+)|/C\s+([0-9.eE+-]+)[^>]*?/U\s*\(([^)]*)\)")
_R_RE = re.compile(r"/R\s*\(([^)]*)\)")


@dataclass
class MeasureInfo:
    ppf: float | None
    evidence: str


def _resolve_vp_source(doc, page) -> str | None:
    """Return the raw PDF source text of the page's /VP array, or None."""
    try:
        t, v = doc.xref_get_key(page.xref, "VP")
    except Exception:
        return None
    if t in ("array", "dict") and v:
        return v
    if t == "xref" and v:
        try:
            xref = int(v.split()[0])
            return doc.xref_object(xref)
        except Exception:
            return None
    return None


def read_measure_dict(doc, page) -> MeasureInfo | None:
    """Detect and parse a rectilinear measure dictionary on the page.

    Prefers the /X NumberFormat conversion factor (/C, exact); falls back to
    parsing the human-readable /R ratio string. v0.1 parses source text rather
    than walking indirect objects — sufficient for Bluebeam-style inline
    dictionaries; revisit against real calibrated samples.
    """
    src = _resolve_vp_source(doc, page)
    if not src or "/Measure" not in src:
        return None

    m = _C_RE.search(src)
    if m:
        unit = (m.group(1) or m.group(4) or "").strip().lower()
        c_tok = m.group(2) or m.group(3)
        try:
            c = float(c_tok)
        except (TypeError, ValueError):
            c = 0.0
        u_per_ft = _U_PER_FT.get(unit)
        if c > 0 and u_per_ft is not None:
            # measured_U = user_space_pt * C  ->  1 ft = u_per_ft U -> pts = u_per_ft / C
            return MeasureInfo(
                ppf=u_per_ft / c,
                evidence=f"measure dict: /C {c} /U ({unit})",
            )

    m = _R_RE.search(src)
    if m:
        parsed = parse_scale_text(m.group(1))
        if parsed and parsed.ppf:
            return MeasureInfo(ppf=parsed.ppf, evidence=f"measure dict /R: {m.group(1)}")
        return MeasureInfo(ppf=None, evidence=f"measure dict present, unparsed /R: {m.group(1)}")

    return MeasureInfo(ppf=None, evidence="measure dict present, unparsed")


def flag_half_size(ppf_a: float, ppf_b: float) -> str | None:
    """PLAN §7: a ~2x (or ~0.5x) disagreement is almost always a half-size print."""
    if ppf_a <= 0 or ppf_b <= 0:
        return None
    ratio = ppf_a / ppf_b
    for target in (2.0, 0.5):
        if abs(ratio - target) / target <= 0.05:
            return f"half-size print? title-block vs dimension-fit ratio = {ratio:.3f}"
    return None
