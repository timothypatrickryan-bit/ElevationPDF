import pytest

from elevation_extract.dimensions import parse_dim_text
from elevation_extract.scale import flag_half_size, parse_scale_text


@pytest.mark.parametrize(
    ("text", "ppf"),
    [
        ('1/8" = 1\'-0"', 9.0),
        ('SCALE: 1/8" = 1\'-0"', 9.0),
        ('3/16"=1\'-0"', 13.5),
        ('1/2" = 1\'-0"', 36.0),
        ('1" = 20\'', 3.6),
        ('1" = 30\'', 2.4),
        ('1" = 100\'', 0.72),
        ("1 in = 8 ft", 9.0),
        ("1/4 inch = 1 foot", 18.0),
        ('1 1/2" = 1\'-0"', 108.0),
        ("1:100", 8.64),
        ("SCALE 1 : 50", 17.28),
        # unicode typography
        ("1⁄2?", None),  # not a scale at all
        ('1/8″ = 1′-0″', 9.0),
    ],
)
def test_parse_scale_text_ppf(text, ppf):
    parsed = parse_scale_text(text)
    if ppf is None:
        assert parsed is None
    else:
        assert parsed is not None, text
        assert parsed.ppf == pytest.approx(ppf, rel=1e-6)


@pytest.mark.parametrize("text", ["N.T.S.", "NTS", "NOT TO SCALE", "SCALE: NTS"])
def test_parse_nts(text):
    parsed = parse_scale_text(text)
    assert parsed is not None
    assert parsed.kind == "nts"
    assert parsed.ppf is None


@pytest.mark.parametrize("text", ["AS NOTED", "SCALE: AS SHOWN"])
def test_parse_as_noted(text):
    parsed = parse_scale_text(text)
    assert parsed is not None
    assert parsed.kind == "as_noted"


def test_half_size_flag():
    assert flag_half_size(18.0, 9.0) is not None  # title 2x dimension fit
    assert flag_half_size(4.5, 9.0) is not None
    assert flag_half_size(9.2, 9.0) is None


@pytest.mark.parametrize(
    ("text", "feet"),
    [
        ("25'-0\"", 25.0),
        ("25' - 0\"", 25.0),
        ("3'-6 1/2\"", 3.0 + 6.5 / 12),
        ("120'", 120.0),
        ("12.5'", 12.5),
        ("6\"", 0.5),
        ("4 1/2\"", 4.5 / 12),
        ("100'-0\"", 100.0),
        ('1/8" = 1\'-0"', None),  # scale strings are not dimensions
        ("SCALE: NTS", None),
        ("SEE NOTE 4", None),
        ("C-101", None),
    ],
)
def test_parse_dim_text(text, feet):
    got = parse_dim_text(text)
    if feet is None:
        assert got is None
    else:
        assert got == pytest.approx(feet, rel=1e-9)
