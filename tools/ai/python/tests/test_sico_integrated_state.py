"""New template/PDK flows preserve SiCo state ownership and legacy read-only boundaries."""

from pathlib import Path

import pytest

from cadai.circuit_preview import attach_svg_preview
from cadai.pdk_standard.updates import Updates
from pdk_standard_fixture import package, replace
from test_circuit_preview import Client, receipt, svg


def test_svg_uses_private_current_state_without_a_second_root(tmp_path):
    result = attach_svg_preview(receipt(), Client(svg("source")), tmp_path)
    artifact = Path(result["svg_preview"]["artifact"]["path"])
    assert artifact.is_relative_to(tmp_path / ".sico/ai/circuit_previews")
    for directory in (tmp_path / ".sico", artifact.parent):
        assert directory.stat().st_mode & 0o077 == 0
    assert not (tmp_path / ".cad").exists()


def test_svg_cannot_write_an_unmigrated_legacy_workspace(tmp_path):
    legacy = tmp_path / ".cad"
    legacy.mkdir(mode=0o700)
    client = Client(svg("source"))
    result = attach_svg_preview(receipt(), client, tmp_path)
    assert result["svg_preview"]["status"] == "export_failed"
    assert "read-only" in result["svg_preview"]["message"]
    assert not client.calls and not list(legacy.iterdir())
    assert not (tmp_path / ".sico").exists()


def test_standard_updates_read_legacy_but_cannot_publish_into_it(tmp_path):
    effective, _, _, _ = package(tmp_path)
    (tmp_path / ".sico").rename(tmp_path / ".cad")
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in (tmp_path / ".cad").rglob("*") if p.is_file()}
    updates = Updates(tmp_path, {})
    with pytest.raises(ValueError, match="read-only"):
        updates.prepare({"library": "synthetic", "revision": effective.revision,
                         "changes": [{"file": "device.json", "ops": [
                             replace("/items/cell0/use/circuit", "allow")]}]})
    after = {str(p.relative_to(tmp_path)): p.read_bytes()
             for p in (tmp_path / ".cad").rglob("*") if p.is_file()}
    assert after == before and not (tmp_path / ".sico").exists()
