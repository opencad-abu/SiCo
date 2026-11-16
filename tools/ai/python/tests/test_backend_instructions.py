"""Codex guidance preserves observed application boundary facts."""

from sico.codex.backend_instructions import INSTRUCTIONS


def test_library_manager_is_opened_without_window_list_polling():
    assert 'eval_skill("ddsOpenLibManager()")' in INSTRUCTIONS
    assert "返回 t 即表示已打开" in INSTRUCTIONS
    assert "不在 list_windows/hiGetWindowList 中" in INSTRUCTIONS
    assert "不要先搜索 SKILL API" not in INSTRUCTIONS
