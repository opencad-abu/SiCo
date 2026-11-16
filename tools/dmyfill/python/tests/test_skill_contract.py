from __future__ import annotations

from pathlib import Path


SKILL = Path(__file__).resolve().parents[2] / "skill/SICO_dmyfill.il"


def test_apply_is_one_direct_shape_command() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "procedure(SICO_dmyfillApplyPlan" in text
    assert "dbEnableUndo()" in text
    assert "hiGetUndoLimit()" in text
    assert "dbCreateRect(" in text
    assert "dbCreatePolygon(" in text
    assert "dbSave(" not in text
    assert "dbCopyFig(" not in text
    assert "hiUndo(" not in text


def test_scope_resolution_covers_three_targets() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "SICO_dmyfillCellBox" in text
    assert "hiGetViewBBox(win)" in text
    assert "geGetSelSet(win)" in text
    assert '("cell" "current cell")' in text
    assert '("window" "current window")' in text
    assert '("selected" "selected box")' in text
