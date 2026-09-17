"""Geometry helpers: lengths, angles, flattening, and a small grid index."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Iterator

Point = tuple[float, float]
Segment = tuple[float, float, float, float]  # x1, y1, x2, y2

BEZIER_SAMPLES = 32


def dist(p: Point, q: Point) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def polyline_length(pts: list[Point]) -> float:
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def cubic_point(p0: Point, c1: Point, c2: Point, p1: Point, t: float) -> Point:
    mt = 1.0 - t
    a = mt * mt * mt
    b = 3 * mt * mt * t
    c = 3 * mt * t * t
    d = t * t * t
    return (
        a * p0[0] + b * c1[0] + c * c2[0] + d * p1[0],
        a * p0[1] + b * c1[1] + c * c2[1] + d * p1[1],
    )


def bezier_chain_samples(pts: list[Point], per_curve: int = BEZIER_SAMPLES) -> list[Point]:
    """Sample a cubic chain (3k+1 control points) into a polyline."""
    out: list[Point] = [pts[0]]
    for i in range(0, len(pts) - 3, 3):
        p0, c1, c2, p1 = pts[i], pts[i + 1], pts[i + 2], pts[i + 3]
        for s in range(1, per_curve + 1):
            out.append(cubic_point(p0, c1, c2, p1, s / per_curve))
    return out


def bezier_chain_length(pts: list[Point], per_curve: int = BEZIER_SAMPLES) -> float:
    """Arc length along the curve (never chord-to-chord — PLAN §9)."""
    return polyline_length(bezier_chain_samples(pts, per_curve))


def path_segments(kind: str, pts: list[Point], closed: bool) -> Iterator[Segment]:
    """Flatten a sheet-model path into line segments (beziers sampled)."""
    if kind == "bezier":
        flat = bezier_chain_samples(pts, per_curve=8)
    else:
        flat = list(pts)
        if closed or kind in ("rect", "quad"):
            if flat and flat[0] != flat[-1]:
                flat = flat + [flat[0]]
    for i in range(len(flat) - 1):
        a, b = flat[i], flat[i + 1]
        yield (a[0], a[1], b[0], b[1])


def segment_length(s: Segment) -> float:
    return math.hypot(s[2] - s[0], s[3] - s[1])


def segment_angle_deg(s: Segment) -> float:
    """Undirected angle in [0, 180). Sheet space is y-down; angle is CCW-visual."""
    ang = math.degrees(math.atan2(-(s[3] - s[1]), s[2] - s[0])) % 180.0
    return ang


def angle_diff_deg(a: float, b: float) -> float:
    """Smallest difference between undirected angles (mod 180)."""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


class GridIndex:
    """Uniform-grid spatial index over segments — enough for feasibility-scale
    nearest-segment queries without an external dependency."""

    def __init__(self, cell: float = 200.0) -> None:
        self.cell = cell
        self._cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        self._segments: list[Segment] = []

    def _key(self, x: float, y: float) -> tuple[int, int]:
        return (int(x // self.cell), int(y // self.cell))

    def add(self, seg: Segment) -> int:
        idx = len(self._segments)
        self._segments.append(seg)
        # register the segment in every cell its bbox touches
        x0, x1 = sorted((seg[0], seg[2]))
        y0, y1 = sorted((seg[1], seg[3]))
        kx0, ky0 = self._key(x0, y0)
        kx1, ky1 = self._key(x1, y1)
        for kx in range(kx0, kx1 + 1):
            for ky in range(ky0, ky1 + 1):
                self._cells[(kx, ky)].append(idx)
        return idx

    def extend(self, segs: Iterable[Segment]) -> None:
        for s in segs:
            self.add(s)

    def segment(self, idx: int) -> Segment:
        return self._segments[idx]

    def __len__(self) -> int:
        return len(self._segments)

    def near(self, x: float, y: float, radius: float) -> list[int]:
        """Candidate segment indices whose cells fall within radius of (x, y)."""
        k0 = self._key(x - radius, y - radius)
        k1 = self._key(x + radius, y + radius)
        seen: set[int] = set()
        out: list[int] = []
        for kx in range(k0[0], k1[0] + 1):
            for ky in range(k0[1], k1[1] + 1):
                for idx in self._cells.get((kx, ky), ()):
                    if idx not in seen:
                        seen.add(idx)
                        out.append(idx)
        return out


def point_segment_distance(x: float, y: float, s: Segment) -> float:
    px, py = s[2] - s[0], s[3] - s[1]
    denom = px * px + py * py
    if denom == 0:
        return math.hypot(x - s[0], y - s[1])
    t = ((x - s[0]) * px + (y - s[1]) * py) / denom
    t = max(0.0, min(1.0, t))
    return math.hypot(x - (s[0] + t * px), y - (s[1] + t * py))


def median(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        raise ValueError("median of empty list")
    vs = sorted(values)
    mid = n // 2
    if n % 2:
        return vs[mid]
    return 0.5 * (vs[mid - 1] + vs[mid])


def mad(values: list[float], center: float | None = None) -> float:
    """Median absolute deviation."""
    if not values:
        raise ValueError("mad of empty list")
    c = median(values) if center is None else center
    return median([abs(v - c) for v in values])
