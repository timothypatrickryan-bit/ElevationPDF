#!/usr/bin/env python3
"""Phase 0 feasibility CLI (PLAN §13).

    feasibility.py <folder> --out report.csv [--json-dir DIR]

Thin shim so the path in the plan works verbatim; the implementation lives in
elevation_extract.feasibility. Works installed (`pip install -e .`) or straight
from a checkout.
"""

import sys
from pathlib import Path

try:
    from elevation_extract.feasibility import main
except ImportError:  # running from a checkout without install
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from elevation_extract.feasibility import main

if __name__ == "__main__":
    raise SystemExit(main())
