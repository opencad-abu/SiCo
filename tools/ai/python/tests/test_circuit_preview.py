"""File preview correctness, failure isolation, and no SVG in model context."""

import copy
import json
from pathlib import Path

import pytest

from cadai.circuit_preview import MAX_SVG_BYTES, attach_svg_preview, read_svg_artifact
from sico.transport.circuit import literal_call


def receipt():
    return dict(ok=True, saved=True, readback_verified=True, circuit_ref="created:svg",
                target=["work", "design", "schematic"])


class Client:
    def __init__(self, raw, failure=None):
        self.raw, self.failure, self.calls, self.retained = raw, failure, [], None

    def call(self, method, args):
        assert method == "eval_skill_native"
        function, values = literal_call(args["code"])
        assert function == "aiCrExportSvg" and values[0] == "created:svg"
        self.calls.append(values)
        if self.failure == "exception":
            raise RuntimeError("connection lost")
        if self.failure == "bridge":
            return False, {"message": "bridge unavailable"}
        if self.failure == "export":
            return True, {"value": json.dumps(dict(ok=False, message="export failed"))}
        if self.retained is None:
            path = Path(values[1])
            path.write_bytes(self.raw)
            size = len(self.raw) + (1 if self.failure == "size" else 0)
            self.retained = dict(ok=True, status="ready", path=str(path), size_bytes=size)
        return True, {"value": json.dumps(self.retained)}


def svg(text):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 200">'
            '<text x="10" y="20">' + text + '</text></svg>').encode()


def test_file_only_unicode_cadence_expressions_and_repeat(tmp_path):
    raw = svg('cdsParam(1) cdsTerm("G") cdsName() &amp; ' + "换行说明" * 3000)
    client = Client(raw)
    result = attach_svg_preview(receipt(), client, tmp_path)
    preview = result["svg_preview"]
    assert result["ok"] and result["saved"] and result["readback_verified"]
    assert preview["status"] == "ready" and "data" not in preview and "context" not in preview
    assert raw.decode() not in json.dumps(result, ensure_ascii=False)
    assert preview["acceptance_blocking"] is False
    assert preview["cadence_expressions"] == "preserved_as_exported"
    artifact = preview["artifact"]
    path = Path(artifact["path"])
    assert path.is_relative_to(tmp_path / ".sico/ai/circuit_previews")
    assert read_svg_artifact(artifact, tmp_path) == raw
    stamp = path.stat().st_mtime_ns
    assert attach_svg_preview(receipt(), client, tmp_path) == result
    assert path.stat().st_mtime_ns == stamp
    # Native inspection and durable reports supply metadata; neither calls export again.
    calls = len(client.calls)
    retained = {**receipt(), "svg_preview": client.retained}
    assert attach_svg_preview(retained, client, tmp_path) == result
    assert attach_svg_preview(copy.deepcopy(result), client, tmp_path) == result
    assert len(client.calls) == calls
    # Same creation request in a new Virtuoso session cannot use an old file.
    other = attach_svg_preview(receipt(), Client(svg("different")), tmp_path)
    assert other["svg_preview"]["artifact"]["path"] != str(path)
    path.write_bytes(raw.replace(b"cdsParam", b"CDSPARAM"))
    assert attach_svg_preview(receipt(), client, tmp_path)["svg_preview"]["status"] != "ready"
    with pytest.raises(ValueError, match="checksum"):
        read_svg_artifact(artifact, tmp_path)


def test_missing_cached_file_does_not_export_or_change_saved_result(tmp_path):
    client = Client(svg("saved"))
    result = attach_svg_preview(receipt(), client, tmp_path)
    Path(result["svg_preview"]["artifact"]["path"]).unlink()
    repeated = attach_svg_preview({**receipt(), "svg_preview": client.retained}, client, tmp_path)
    assert repeated["svg_preview"]["status"] == "export_failed"
    assert repeated["saved"] and repeated["readback_verified"]
    assert len(client.calls) == 1


def test_preview_directory_cannot_redirect_outside_workspace(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / ".sico").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    client = Client(svg("ok"))
    result = attach_svg_preview(receipt(), client, tmp_path)
    assert result["svg_preview"]["status"] == "export_failed"
    assert not client.calls


def test_preview_directory_follows_an_aliased_workspace_prefix(tmp_path):
    """站点别名（如 /workarea 软链）不是重定向；导出路径用 simplifyFilename 拼写。"""
    mount = tmp_path / "mount"
    (mount / "proj").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(mount, target_is_directory=True)
    workspace = alias / "proj"
    client = Client(svg("aliased"))
    result = attach_svg_preview(receipt(), client, workspace)
    preview = result["svg_preview"]
    assert preview["status"] == "ready", preview["message"]
    artifact = preview["artifact"]
    # The native handshake records the simplified project directory and demands
    # a simplified destination; the alias stays out of the exported spelling.
    assert Path(artifact["path"]).is_relative_to(mount / "proj")
    exported = client.calls[0][1]
    assert exported == str(Path(exported).resolve())
    assert read_svg_artifact(artifact, workspace) == svg("aliased")
    # The same workspace through its real path names the same preview file.
    assert read_svg_artifact(artifact, mount / "proj") == svg("aliased")


def test_read_rejects_a_redirected_preview_directory(tmp_path):
    """工作区内部的重定向（.sico 之下）读回时必须拒绝。"""
    result = attach_svg_preview(receipt(), Client(svg("moved")), tmp_path)
    artifact = result["svg_preview"]["artifact"]
    root = tmp_path / ".sico/ai/circuit_previews"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root.rename(root.with_name("previews-real"))
    root.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="inside the workspace"):
        read_svg_artifact(artifact, tmp_path)


@pytest.mark.parametrize("failure", ["export", "bridge", "exception", "xml", "size", "budget"])
def test_preview_failures_preserve_successful_creation(failure, tmp_path):
    raw = b"not svg" if failure == "xml" else svg("cdsParam(1)")
    if failure == "budget":
        raw = svg("x" * MAX_SVG_BYTES)
    before = receipt()
    output = attach_svg_preview(copy.deepcopy(before), Client(raw, failure), tmp_path)
    assert output["svg_preview"]["status"] == "export_failed"
    assert output["svg_preview"]["acceptance_blocking"] is False
    assert {k: v for k, v in output.items() if k != "svg_preview"} == before


def test_missing_workspace_or_prepared_result(tmp_path):
    client = Client(svg("ok"))
    assert attach_svg_preview(receipt(), client)["svg_preview"]["status"] == "export_failed"
    result = dict(ok=True, saved=False)
    assert attach_svg_preview(copy.deepcopy(result), client, tmp_path) == result
    assert not client.calls


def test_display_requires_workspace_path_checksum_and_self_contained_svg(tmp_path):
    result = attach_svg_preview(receipt(), Client(svg("ok")), tmp_path)
    artifact = result["svg_preview"]["artifact"]
    with pytest.raises(ValueError, match="outside"):
        read_svg_artifact(artifact, tmp_path / "elsewhere")
    unsafe = svg('<image href="file:///private"/>')
    result = attach_svg_preview(receipt(), Client(unsafe), tmp_path)
    assert result["svg_preview"]["status"] == "export_failed"
