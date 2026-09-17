"""Golden tests: extractor output on synthetic PDFs with known geometry/scale.

These are what keep the MVP's 0.5% length criterion honest across PyMuPDF
upgrades (PLAN §14 step 3).
"""

import jsonschema
import pytest

from elevation_extract import extract_sheet
from elevation_extract.geometry import path_segments, segment_length


def _model(open_fixture, name):
    return extract_sheet(open_fixture(name), 0)


def _all_segment_lengths(model):
    for p in model["paths"]:
        for seg in path_segments(p["kind"], p["pts"], p["closed"]):
            yield p, segment_length(seg)


def _has_segment_of_length(model, length, tol=0.5):
    return any(abs(l - length) <= tol for _, l in _all_segment_lengths(model))


def test_all_fixtures_validate_against_schema(open_fixture, sheet_schema):
    from make_fixtures import FIXTURES

    for name in FIXTURES:
        model = _model(open_fixture, name)
        jsonschema.validate(model, sheet_schema)


@pytest.fixture(scope="module")
def simple_model(open_fixture):
    return _model(open_fixture, "simple_scaled.pdf")


@pytest.fixture(scope="module")
def nts_model(open_fixture):
    return _model(open_fixture, "nts_dims.pdf")


@pytest.fixture(scope="module")
def ocgs_model(open_fixture):
    return _model(open_fixture, "with_ocgs.pdf")


class TestSimpleScaled:
    @pytest.fixture()
    def model(self, simple_model):
        return simple_model

    def test_page_basics(self, model):
        assert model["size_pt"] == [2592.0, 1728.0]
        assert model["rotation"] == 0
        assert model["user_unit"] == 1.0
        assert model["content_hash"].startswith("sha256:")
        assert model["producer"] == "ElevationPDF fixtures"

    def test_known_geometry_lengths(self, model):
        # 100 ft line at 9 ppf = 900 pt; 50 ft = 450 pt (accuracy target 0.5%)
        assert _has_segment_of_length(model, 900.0)
        assert _has_segment_of_length(model, 450.0)

    def test_styles_survive(self, model):
        strokes = {p["stroke"] for p in model["paths"]}
        assert "#00a0ff" in strokes  # the blue line
        dashed = [p for p in model["paths"] if p["dash"]]
        assert dashed and dashed[0]["dash"] == [6.0, 3.0]
        kinds = {p["kind"] for p in model["paths"]}
        assert {"rect", "bezier"} <= kinds
        assert "polyline" in kinds or "line" in kinds

    def test_title_block(self, model):
        tb = model["title_block"]
        assert tb is not None
        assert '1/8"' in tb["scale_text"]
        assert tb["sheet_no"] == "C-101"

    def test_title_block_scale(self, model):
        by_method = {s["method"]: s for s in model["scales"]}
        assert "title_block" in by_method
        assert by_method["title_block"]["points_per_foot"] == pytest.approx(9.0, rel=1e-6)
        assert by_method["title_block"]["confidence"] >= 0.6

    def test_dimension_candidates(self, model):
        implied = [d["implied_ppf"] for d in model["dimensions"] if d["implied_ppf"]]
        assert len(implied) >= 2
        for ppf in implied:
            assert ppf == pytest.approx(9.0, rel=0.01)

    def test_vertical_dim_text_rotation(self, model):
        vertical = [t for t in model["texts"] if "50'" in t["str"]]
        assert vertical
        assert vertical[0]["rotation_deg"] == pytest.approx(90.0, abs=1.0)

    def test_stats(self, model):
        st = model["stats"]
        assert st["path_count"] >= 8
        assert st["segment_count"] >= 10
        assert st["distinct_stroke_colors"] >= 2
        assert st["monochrome"] is False
        assert st["is_raster"] is False


class TestNtsDims:
    @pytest.fixture()
    def model(self, nts_model):
        return nts_model

    def test_dimension_fit_scale(self, model):
        by_method = {s["method"]: s for s in model["scales"]}
        assert "dimension_fit" in by_method
        fit = by_method["dimension_fit"]
        assert fit["points_per_foot"] == pytest.approx(9.0, rel=0.005)
        assert fit["confidence"] >= 0.7
        assert "title_block" not in by_method  # NTS gives no title-block ppf

    def test_scale_text_recorded(self, model):
        assert "N.T.S" in model["title_block"]["scale_text"].upper()


class TestWithOcgs:
    @pytest.fixture()
    def model(self, ocgs_model):
        return ocgs_model

    def test_layers(self, model):
        names = {l["name"]: l for l in model["layers"]}
        assert set(names) == {"E-FIBER", "ANNO"}
        assert all(l["source"] == "ocg" for l in model["layers"])
        assert names["E-FIBER"]["default_visible"] is True
        assert names["ANNO"]["default_visible"] is False

    def test_paths_carry_layer_ids(self, model):
        by_name = {l["name"]: l["id"] for l in model["layers"]}
        fiber_paths = [p for p in model["paths"] if p["layer_id"] == by_name["E-FIBER"]]
        anno_paths = [p for p in model["paths"] if p["layer_id"] == by_name["ANNO"]]
        bare_paths = [p for p in model["paths"] if p["layer_id"] is None]
        assert len(fiber_paths) == 2
        assert len(anno_paths) == 1
        assert len(bare_paths) == 1


class TestRotated:
    def test_rotation_normalized_to_sheet_space(self, open_fixture):
        model = _model(open_fixture, "rotated90.pdf")
        assert model["rotation"] == 90
        assert model["size_pt"] == [792.0, 612.0]
        # (72,72)->(172,72) unrotated must become (720,72)->(720,172) displayed
        line = model["paths"][0]
        pts = sorted(line["pts"], key=lambda p: p[1])
        assert pts[0] == pytest.approx([720.0, 72.0], abs=0.01)
        assert pts[1] == pytest.approx([720.0, 172.0], abs=0.01)
        # every coordinate stays inside the displayed page
        for p in model["paths"]:
            for x, y in p["pts"]:
                assert 0 <= x <= 792 and 0 <= y <= 612


class TestMeasureDict:
    def test_measure_dict_scale(self, open_fixture):
        model = _model(open_fixture, "measure_dict.pdf")
        by_method = {s["method"]: s for s in model["scales"]}
        assert "measure_dict" in by_method
        s = by_method["measure_dict"]
        assert s["points_per_foot"] == pytest.approx(9.0, rel=0.001)
        assert s["confidence"] == 1.0


class TestRasterScan:
    def test_raster_detected(self, open_fixture):
        model = _model(open_fixture, "raster_scan.pdf")
        st = model["stats"]
        assert st["is_raster"] is True
        assert st["image_coverage"] >= 0.95
        assert st["path_count"] < 50


class TestMonochrome:
    def test_monochrome_flag(self, open_fixture):
        model = _model(open_fixture, "monochrome.pdf")
        st = model["stats"]
        assert st["monochrome"] is True
        assert st["distinct_stroke_colors"] <= 2


class TestShxComments:
    def test_annotation_comments_become_texts(self, open_fixture):
        model = _model(open_fixture, "shx_comments.pdf")
        shx = [t for t in model["texts"] if t["source"] == "shx_comment"]
        assert {t["str"] for t in shx} == {"25'-0\"", "10'-6\"", "SEE NOTE 4"}
        # the dimension-valued comments are picked up as candidates
        values = [d["value_ft"] for d in model["dimensions"]]
        assert 25.0 in values
        assert any(abs(v - 10.5) < 1e-9 for v in values)
