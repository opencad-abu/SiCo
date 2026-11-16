"""Opt-in terminal output evidence is append-only and preserves chunk boundaries."""

from __future__ import annotations

from cad_ai_terminal.output_capture import _append_test_output


def test_append_test_output_creates_parents_and_appends_utf8_verbatim(tmp_path):
    path = tmp_path / "nested" / "terminal.log"
    _append_test_output(path, "第一行\n")
    _append_test_output(path, "second\r\n")
    with path.open(encoding="utf-8", newline="") as stream:
        assert stream.read() == "第一行\nsecond\r\n"
    assert path.parent.is_dir()


def test_append_test_output_preserves_empty_chunks_without_rewriting(tmp_path):
    path = tmp_path / "terminal.log"
    path.write_text("existing", encoding="utf-8")
    _append_test_output(path, "")
    assert path.read_text(encoding="utf-8") == "existing"
