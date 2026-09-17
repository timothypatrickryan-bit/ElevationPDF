import json
from pathlib import Path

import pymupdf
import pytest

from make_fixtures import build_all

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "packages" / "model" / "schema" / "sheet-model.schema.json"


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory) -> Path:
    outdir = tmp_path_factory.mktemp("fixture-pdfs")
    build_all(outdir)
    return outdir


@pytest.fixture(scope="session")
def sheet_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


@pytest.fixture(scope="session")
def open_fixture(fixture_dir):
    docs: list[pymupdf.Document] = []

    def _open(name: str) -> pymupdf.Document:
        doc = pymupdf.open(fixture_dir / name)
        docs.append(doc)
        return doc

    yield _open
    for d in docs:
        d.close()
