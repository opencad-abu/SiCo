"""Preview catalog: registered and manual products, alias resolution, containment."""

import hashlib
import json
import os
from dataclasses import replace

import pytest

from cadai.circuit_preview_catalog import (
    list_preview_artifacts,
    read_preview_artifact,
)
from cadai.circuit_preview_files import preview_directory


def svg(text="ok"):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 200">'
            '<text x="10" y="20">' + text + '</text></svg>').encode()


def registered(workspace, name, raw, target="work/design/schematic"):
    directory = preview_directory(workspace, create=True)
    path = directory / name
    path.write_bytes(raw)
    manifest = dict(path=str(path), media_type="image/svg+xml", size_bytes=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest(), target=target)
    path.with_suffix(".json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_catalog_lists_registered_target_and_manual_file(tmp_path):
    registered(tmp_path, "auto.svg", svg("auto"))
    manual = tmp_path / ".sico/ai/circuit_previews/tb_inv_p3_g7.svg"
    manual.write_bytes(svg("manual"))
    os.utime(manual, (2_000_000_000, 2_000_000_000))
    catalog = list_preview_artifacts(tmp_path)
    assert [row.name for row in catalog.rows] == ["tb_inv_p3_g7.svg", "auto.svg"]
    assert not catalog.truncated
    automatic = catalog.rows[1]
    assert automatic.registered and automatic.target == "work/design/schematic"
    assert automatic.size == len(svg("auto")) and len(automatic.sha256) == 64
    listed = catalog.rows[0]
    assert not listed.registered and listed.notice == "" and listed.sha256 == ""
    assert listed.size == len(svg("manual"))


def test_catalog_tolerates_absent_partial_and_mismatched_manifests(tmp_path):
    directory = preview_directory(tmp_path, create=True)
    (directory / "no-manifest.svg").write_bytes(svg("plain"))
    (directory / "partial.svg").write_bytes(svg("partial"))
    (directory / "partial.json").write_text("{}", encoding="utf-8")
    (directory / "garbage.svg").write_bytes(svg("garbage"))
    (directory / "garbage.json").write_text("not json", encoding="utf-8")
    (directory / "stale.svg").write_bytes(svg("stale"))
    (directory / "stale.json").write_text(json.dumps(dict(
        path=str(directory / "stale.svg"), media_type="image/svg+xml",
        size_bytes=11, sha256="0" * 64)),
        encoding="utf-8")
    rows = {row.name: row for row in list_preview_artifacts(tmp_path).rows}
    assert set(rows) == {"no-manifest.svg", "partial.svg", "garbage.svg", "stale.svg"}
    assert not rows["no-manifest.svg"].registered and rows["no-manifest.svg"].notice == ""
    assert not rows["partial.svg"].registered and rows["partial.svg"].notice == ""
    assert not rows["garbage.svg"].registered and rows["garbage.svg"].notice == ""
    # The file disagrees with the manifest: listed, but not registered.
    assert not rows["stale.svg"].registered
    assert "大小" in rows["stale.svg"].notice
    assert rows["stale.svg"].size == len(svg("stale"))


def test_read_verifies_manifest_and_keeps_manual_notice(tmp_path):
    registered(tmp_path, "auto.svg", svg("auto"))
    manual = tmp_path / ".sico/ai/circuit_previews/tb_inv_p3_g7.svg"
    manual.write_bytes(svg("manual"))
    artifact = read_preview_artifact(tmp_path, "auto.svg")
    assert artifact.data == svg("auto") and artifact.registered and artifact.notice == ""
    assert artifact.target == "work/design/schematic"
    assert artifact.digest == hashlib.sha256(svg("auto")).hexdigest()
    listed = read_preview_artifact(tmp_path, "tb_inv_p3_g7.svg")
    assert listed.data == svg("manual") and not listed.registered
    assert "未登记" in listed.notice


def test_read_rejects_checksum_change_and_unlisted_names(tmp_path):
    path = registered(tmp_path, "auto.svg", svg("auto"))
    path.write_bytes(svg("tampered"))
    with pytest.raises(ValueError, match="checksum"):
        read_preview_artifact(tmp_path, "auto.svg")
    # A stale listing (the file changes between list and read) is refused.
    path.write_bytes(svg("auto")[:10])
    import cadai.circuit_preview_catalog as catalog_module

    stale = list_preview_artifacts(tmp_path)
    wider = replace(stale, rows=(replace(stale.rows[0], size=500),))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(catalog_module, "list_preview_artifacts", lambda _workspace: wider)
        with pytest.raises(ValueError, match="size changed"):
            catalog_module.read_preview_artifact(tmp_path, "auto.svg")
    path.write_bytes(svg("auto"))
    for name in ("missing.svg", "../auto.svg", "auto.json", "", "auto.txt"):
        with pytest.raises(ValueError):
            read_preview_artifact(tmp_path, name)


def test_read_rejects_unregistered_bytes_outside_the_svg_dialect(tmp_path):
    directory = preview_directory(tmp_path, create=True)
    (directory / "script.svg").write_bytes(
        svg().replace(b"<text", b"<script>alert(1)</script><text"))
    with pytest.raises(ValueError, match="active or external"):
        read_preview_artifact(tmp_path, "script.svg")
    assert list_preview_artifacts(tmp_path).rows[0].name == "script.svg"


def test_catalog_follows_an_aliased_workspace_prefix(tmp_path):
    mount = tmp_path / "mount"
    (mount / "proj").mkdir(parents=True)
    registered(mount / "proj", "auto.svg", svg("aliased"))
    alias = tmp_path / "alias"
    alias.symlink_to(mount, target_is_directory=True)
    rows = list_preview_artifacts(alias / "proj").rows
    assert [row.name for row in rows] == ["auto.svg"]
    assert read_preview_artifact(alias / "proj", "auto.svg").data == svg("aliased")


def test_catalog_requires_an_available_workspace_and_lists_without_one(tmp_path):
    assert list_preview_artifacts(tmp_path).rows == ()
    with pytest.raises(ValueError, match="requires a workspace"):
        list_preview_artifacts(None)
    (tmp_path / ".sico").mkdir(mode=0o700)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / ".sico/ai").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(ValueError):
        list_preview_artifacts(tmp_path)
    with pytest.raises(ValueError):
        read_preview_artifact(tmp_path, "auto.svg")
