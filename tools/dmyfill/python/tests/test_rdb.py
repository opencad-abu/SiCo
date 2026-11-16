from __future__ import annotations

import pytest

from dmyfillpy.rdb import RdbError, parse_rdb_text


SAMPLE = """\
INV25B 1000
DM1_FILL
3 6 1 Oct 29 21:00:00 2026
Rule File Pathname: fill.cal
p 1 4
1000 2000
3000 2000
3000 4000
1000 4000
RM1_FILL
1 6 1 Oct 29 21:00:00 2026
r 2 0 0 500 500
"""


def test_parse_polygon_and_rectangle_records() -> None:
    document = parse_rdb_text(SAMPLE, ("DM1_FILL", "RM1_FILL"))

    assert document.top_cell == "INV25B"
    assert document.precision == 1000
    assert len(document.shapes) == 2
    assert document.shapes[0].rule == "DM1_FILL"
    assert document.shapes[0].points[0] == (1000, 2000)
    assert document.shapes[1].rule == "RM1_FILL"
    assert document.shapes[1].points == (
        (0, 0),
        (500, 0),
        (500, 500),
        (0, 500),
    )
    assert document.user_points(document.shapes[0])[1] == (3.0, 2.0)


def test_parse_ignores_unknown_sections() -> None:
    document = parse_rdb_text(
        "TOP 100\nOTHER\n1 1 1 now\np 1 3\n0 0\n1 0\n0 1\n",
        ("DM1_FILL",),
    )
    assert document.shapes == ()


def test_parse_rejects_truncated_polygon() -> None:
    with pytest.raises(RdbError, match="expected 4 points"):
        parse_rdb_text(
            "TOP 1000\nDM1_FILL\np 1 4\n0 0\n1 0\n1 1\n",
            ("DM1_FILL",),
        )

