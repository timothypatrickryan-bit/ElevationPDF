import csv

from elevation_extract.feasibility import COLUMNS, main, run_folder, size_class, summarize


def test_size_class():
    assert size_class(2592, 1728) == "ARCH D"
    assert size_class(1728, 2592) == "ARCH D"  # orientation-agnostic
    assert size_class(612, 792) == "ANSI A"
    assert size_class(1224, 792) == "ANSI B"
    assert size_class(500, 500) == "custom"


def test_run_folder_rows(fixture_dir):
    rows = run_folder(fixture_dir)
    assert len(rows) == 8
    by_file = {r["file"]: r for r in rows}

    r = by_file["simple_scaled.pdf"]
    assert r["size_class"] == "ARCH D"
    assert r["predicted_method"] == 2  # title block
    assert r["monochrome"] is False
    assert r["is_raster"] is False
    assert float(r["title_block_ppf"]) == 9.0

    assert by_file["measure_dict.pdf"]["predicted_method"] == 1
    assert by_file["measure_dict.pdf"]["has_measure_dict"] is True

    r = by_file["nts_dims.pdf"]
    assert r["predicted_method"] == 3
    assert abs(float(r["dim_ppf_median"]) - 9.0) < 0.1
    assert r["dim_paired_count"] >= 4

    assert by_file["raster_scan.pdf"]["is_raster"] is True
    assert by_file["raster_scan.pdf"]["predicted_method"] == 4

    assert by_file["monochrome.pdf"]["monochrome"] is True
    assert by_file["with_ocgs.pdf"]["ocg_count"] == 2
    assert "E-FIBER" in by_file["with_ocgs.pdf"]["ocg_names"]
    assert by_file["shx_comments.pdf"]["shx_comment_count"] == 3
    assert by_file["rotated90.pdf"]["rotation"] == 90


def test_summary_mentions_gates(fixture_dir):
    rows = run_folder(fixture_dir)
    text = summarize(rows)
    assert "vector sheets" in text
    assert "decision gates" in text
    assert "worst-case sheets" in text


def test_cli_writes_csv(fixture_dir, tmp_path, capsys):
    out = tmp_path / "report.csv"
    rc = main([str(fixture_dir), "--out", str(out)])
    assert rc == 0
    with open(out) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 8
    assert list(rows[0].keys()) == COLUMNS
    captured = capsys.readouterr()
    assert "Feasibility summary" in captured.out


def test_cli_json_dump(fixture_dir, tmp_path):
    out = tmp_path / "report.csv"
    json_dir = tmp_path / "models"
    rc = main([str(fixture_dir), "--out", str(out), "--json-dir", str(json_dir)])
    assert rc == 0
    dumped = list(json_dir.glob("*.sheetmodel.json"))
    assert len(dumped) == 8
