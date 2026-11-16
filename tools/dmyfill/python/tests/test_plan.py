from __future__ import annotations

from pathlib import Path

from dmyfillpy.plan import build_shapes, render_plan, write_plan
from dmyfillpy.profile import FillProfile, LayerRule, RuleTarget
from dmyfillpy.rdb import RdbDocument, RdbShape


def _profile() -> FillProfile:
    return FillProfile(
        path=Path("/tmp/fill.json"),
        deck=Path("/tmp/fill.cal"),
        layers={"M1": LayerRule("M1", "FILL_DM1")},
        rules={"DM1_FILL": RuleTarget("M1", "dummy")},
    )


def test_build_shapes_maps_rule_and_deduplicates() -> None:
    document = RdbDocument(
        "TOP",
        1000,
        (
            RdbShape("DM1_FILL", ((0, 0), (1000, 0), (1000, 1000), (0, 1000))),
            RdbShape("DM1_FILL", ((0, 1000), (1000, 1000), (1000, 0), (0, 0))),
            RdbShape("UNKNOWN", ((0, 0), (1, 0), (0, 1))),
        ),
    )

    shapes = build_shapes(document, _profile())

    assert len(shapes) == 1
    assert shapes[0].layer == "M1"
    assert shapes[0].purpose == "dummy"
    assert shapes[0].points == ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))


def test_render_plan_emits_rect_and_poly() -> None:
    document = RdbDocument(
        "TOP",
        1000,
        (
            RdbShape("DM1_FILL", ((0, 0), (1000, 0), (1000, 1000), (0, 1000))),
            RdbShape("DM1_FILL", ((0, 0), (2000, 0), (1000, 1000))),
        ),
    )
    shapes = build_shapes(document, _profile())

    text = render_plan(shapes, run_id="run1", top_cell="TOP")

    assert text.startswith("# dmyfill-plan v1\n# run run1\n# cell TOP\n")
    assert "RECT M1 dummy 0 0 1 1\n" in text
    assert "POLY M1 dummy 0 0 1 1 2 0\n" in text


def test_write_plan_creates_parent(tmp_path: Path) -> None:
    output = write_plan(
        tmp_path / "nested" / "plan.txt",
        (),
        run_id="run1",
        top_cell="TOP",
    )
    assert output.is_file()
    assert output.read_text().startswith("# dmyfill-plan v1")
