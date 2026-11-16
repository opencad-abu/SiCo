from __future__ import annotations

from pathlib import Path

import pytest

from dmyfillpy.deck import DeckError, DeckRequest, rewrite_deck
from dmyfillpy.profile import FillProfile, LayerRule, RuleTarget


SOURCE = """\
LAYOUT PATH "old.gds"
LAYOUT PRIMARY "OLD"
DRC RESULTS DATABASE "old.gds" GDSII
DRC SUMMARY REPORT "old.sum"
DRC MAXIMUM RESULTS ALL
//#DEFINE ChipWindowUsed
VARIABLE xLB 0.0
VARIABLE yLB 0.0
VARIABLE xRT 100.0
VARIABLE yRT 100.0
//#DEFINE dmOnCorner
#DEFINE FILL_DM1
#DEFINE FILL_DM2
//#DEFINE 9K_THICK_M8
DRC CHECK MAP DM1_FILL 31 1 AUTOREF
DRC CHECK MAP DM2_FILL 32 1 AUTOREF
#DECRYPT opaque
//#DEFINE FILL_DM1
#ENDCRYPT
"""


def _profile() -> FillProfile:
    return FillProfile(
        path=Path("/tmp/fill.json"),
        deck=Path("/tmp/fill.cal"),
        layers={
            "M1": LayerRule("M1", "FILL_DM1"),
            "M2": LayerRule("M2", "FILL_DM2"),
            "M8": LayerRule("M8", "FILL_DM8", "9K_THICK_M8"),
        },
        rules={
            "DM1_FILL": RuleTarget("M1", "dummy"),
            "DM2_FILL": RuleTarget("M2", "dummy"),
        },
    )


def test_rewrite_selects_layers_region_and_ascii_output(tmp_path: Path) -> None:
    result = rewrite_deck(
        _profile(),
        SOURCE,
        DeckRequest(
            layout_path=tmp_path / "input.gds",
            top_cell="TOP",
            output_rdb=tmp_path / "fill.rdb",
            summary_path=tmp_path / "fill.sum",
            selected_layers=("M1",),
            region=(1.0, 2.0, 30.0, 40.0),
        ),
    )

    text = result.text
    assert 'LAYOUT PATH "' + str(tmp_path / "input.gds") + '"' in text
    assert 'LAYOUT PRIMARY "TOP"' in text
    assert 'ASCII' in text
    assert '#DEFINE FILL_DM1' in text
    assert '//#DEFINE FILL_DM2' in text
    assert '#DEFINE ChipWindowUsed' in text
    assert '#DEFINE dmOnCorner' in text
    assert 'VARIABLE xLB 1' in text
    assert 'VARIABLE yRT 40' in text
    assert 'DRC CHECK MAP DM1_FILL' not in text
    assert 'DRC CHECK MAP DM2_FILL' not in text
    assert result.removed_check_maps == 2
    assert result.encrypted_sha256
    assert '//#DEFINE FILL_DM1\n#ENDCRYPT' in text


def test_rewrite_requires_selected_fill_switch(tmp_path: Path) -> None:
    source = SOURCE.replace("#DEFINE FILL_DM1\n", "")
    with pytest.raises(DeckError, match="missing layer switch"):
        rewrite_deck(
            _profile(),
            source,
            DeckRequest(
                layout_path=tmp_path / "input.gds",
                top_cell="TOP",
                output_rdb=tmp_path / "fill.rdb",
                summary_path=tmp_path / "fill.sum",
                selected_layers=("M1",),
            ),
        )
