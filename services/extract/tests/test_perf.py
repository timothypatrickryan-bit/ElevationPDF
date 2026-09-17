"""Perf smoke test for the MVP acceptance criterion: extraction < 60 s/sheet
at 100k segments (PLAN §4). Runs in ~3 s; the generous bound absorbs CI noise
while still catching order-of-magnitude regressions.
"""

import io
import random
import time

import pymupdf

from elevation_extract import extract_sheet

N_POLYLINES = 25_000  # x4 segments each = 100k segments
TIME_BUDGET_S = 30.0


def _dense_sheet() -> bytes:
    random.seed(42)
    parts = ["0 0 0 RG 0.3 w"]
    for _ in range(N_POLYLINES):
        x, y = random.uniform(60, 2400), random.uniform(60, 1600)
        parts.append(f"{x:.1f} {y:.1f} m")
        for _ in range(4):
            x += random.uniform(-40, 40)
            y += random.uniform(-40, 40)
            parts.append(f"{x:.1f} {y:.1f} l")
        parts.append("S")
    stream = " ".join(parts).encode()

    doc = pymupdf.open()
    page = doc.new_page(width=2592, height=1728)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, stream)
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    return doc.tobytes()


def test_100k_segment_sheet_extracts_within_budget():
    doc = pymupdf.open(stream=io.BytesIO(_dense_sheet()), filetype="pdf")
    t0 = time.perf_counter()
    model = extract_sheet(doc, 0)
    elapsed = time.perf_counter() - t0

    assert model["stats"]["segment_count"] == 4 * N_POLYLINES
    assert model["stats"]["path_count"] == N_POLYLINES
    assert elapsed < TIME_BUDGET_S, f"extraction took {elapsed:.1f}s"
