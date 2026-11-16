from __future__ import annotations

import json
from pathlib import Path

import pytest

from aivworkbench.errors import WorkspaceError
from aivworkbench.workspace import (
    allocate_split_run,
    allocate_run,
    resolve_launch_paths,
    resolve_project_db_root,
    sha256_file,
    stable_digest,
    target_directory_name,
    write_json_once,
)
from aivworkbench.manifest import (
    artifact_locator_index,
    publish_split_manifests,
    resolve_indexed_artifact,
    resolve_indexed_locator,
    verify_split_manifests,
)


def test_artifact_root_is_always_cwd_dot_aivw(tmp_path: Path) -> None:
    paths = resolve_launch_paths(tmp_path)
    assert paths.logical_artifact_root == tmp_path / ".aivw"
    assert paths.physical_artifact_root == (tmp_path / ".aivw").resolve()


def test_stable_digest_preserves_historical_json_byte_contract() -> None:
    value = {"中文": "值", "z": 1.5, "a": [True, None]}
    expected = "8a30ba526d94dfc79398536acadc18da02201e984fb524ede7824fb2184febb9"
    assert stable_digest(value) == expected
    assert stable_digest({"a": [True, None], "z": 1.5, "中文": "值"}) == expected
    assert stable_digest(float("nan")) == "d5b592c05dc25b5032553f1b27f4139be95e881f73db33b02b05ab20c3f9981e"


def test_completed_manifest_cannot_be_overwritten(tmp_path: Path) -> None:
    launch = resolve_launch_paths(tmp_path)
    run = allocate_run(launch, "doctor", stable_digest({"test": 1}))
    write_json_once(run.manifest, {"status": "PASS"})
    assert json.loads(run.manifest.read_text(encoding="utf-8"))["status"] == "PASS"
    with pytest.raises(WorkspaceError, match="refusing to replace"):
        write_json_once(run.manifest, {"status": "FAIL"})


def test_split_run_keeps_control_local_and_payload_in_project_db(tmp_path: Path) -> None:
    launch_dir = tmp_path / "launch"
    payload_dir = tmp_path / "project-data"
    launch_dir.mkdir()
    payload_dir.mkdir()
    launch = resolve_launch_paths(launch_dir)
    root = resolve_project_db_root(payload_dir, launch)
    run = allocate_split_run(
        launch,
        root,
        library="lib",
        cell="cell",
        view="systemVerilog",
        kind="recipe",
        request_digest=stable_digest({"test": "split"}),
    )
    assert run.control_root.parent == launch_dir / ".aivw" / "runs"
    assert run.payload_root.parent == payload_dir / "lib.cell.systemVerilog" / "runs"
    assert run.control_root.name == run.payload_root.name == run.run_id


def test_split_run_retries_receive_distinct_isolated_payloads(tmp_path: Path) -> None:
    launch_dir = tmp_path / "launch"
    payload_dir = tmp_path / "project-data"
    launch_dir.mkdir()
    payload_dir.mkdir()
    launch = resolve_launch_paths(launch_dir)
    root = resolve_project_db_root(payload_dir, launch)
    request_digest = stable_digest({"test": "repeat-isolation"})

    first = allocate_split_run(
        launch,
        root,
        library="lib",
        cell="cell",
        view="rnm",
        kind="qualification",
        request_digest=request_digest,
    )
    second = allocate_split_run(
        launch,
        root,
        library="lib",
        cell="cell",
        view="rnm",
        kind="qualification",
        request_digest=request_digest,
    )

    assert first.run_id != second.run_id
    assert first.control_root != second.control_root
    assert first.payload_root != second.payload_root
    assert first.payload_root.parent == second.payload_root.parent


def test_target_directory_percent_encodes_separator_characters() -> None:
    assert target_directory_name("lib.one", "cell/two", "rnm") == "lib%2Eone.cell%2Ftwo.rnm"


def test_project_db_cannot_be_inside_control_root(tmp_path: Path) -> None:
    launch = resolve_launch_paths(tmp_path)
    with pytest.raises(WorkspaceError, match="forbidden root"):
        resolve_project_db_root(tmp_path / ".aivw" / "payload", launch)


def test_split_manifests_bind_and_verify_indexed_artifacts(tmp_path: Path) -> None:
    launch_dir = tmp_path / "launch"
    payload_dir = tmp_path / "project-data"
    launch_dir.mkdir()
    payload_dir.mkdir()
    launch = resolve_launch_paths(launch_dir)
    run = allocate_split_run(
        launch,
        resolve_project_db_root(payload_dir, launch),
        library="lib",
        cell="cell",
        view="rnm",
        kind="recipe",
        request_digest=stable_digest({"test": "manifest"}),
    )
    request = run.control_root / "request.json"
    payload = run.payload_root / "xcelium" / "compile.log"
    payload.parent.mkdir()
    request.write_text("{}\n", encoding="utf-8")
    payload.write_text("compile PASS\n", encoding="utf-8")
    publish_split_manifests(
        run,
        request_digest=stable_digest({"test": "manifest"}),
        status="PASS",
        control_details={"scope": "test"},
        payload_details={"scope": "test"},
        control_artifacts=(request,),
        payload_artifacts=(payload,),
    )
    result = verify_split_manifests(run.control_manifest, run.payload_manifest)
    assert result["status"] == "PASS"
    assert result["artifact_counts"] == {"control": 1, "payload": 1}
    resolved = resolve_indexed_artifact(run.control_manifest, "xcelium/compile.log")
    assert resolved.path == payload.resolve()
    assert resolved.storage_role == "payload"
    assert resolved.sha256 == sha256_file(payload)


def test_split_manifest_artifact_tamper_fails_closed(tmp_path: Path) -> None:
    launch_dir = tmp_path / "launch"
    payload_dir = tmp_path / "project-data"
    launch_dir.mkdir()
    payload_dir.mkdir()
    launch = resolve_launch_paths(launch_dir)
    run = allocate_split_run(
        launch,
        resolve_project_db_root(payload_dir, launch),
        library="lib",
        cell="cell",
        view="rnm",
        kind="recipe",
        request_digest=stable_digest({"test": "tamper"}),
    )
    artifact = run.control_root / "request.json"
    artifact.write_text("before\n", encoding="utf-8")
    publish_split_manifests(
        run,
        request_digest=stable_digest({"test": "tamper"}),
        status="PASS",
        control_details={},
        payload_details={},
        control_artifacts=(artifact,),
    )
    artifact.write_text("after\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="artifact hash mismatch"):
        verify_split_manifests(run.control_manifest, run.payload_manifest)


def test_split_manifest_supports_bounded_file_and_directory_locators(tmp_path: Path) -> None:
    launch_dir = tmp_path / "launch"
    payload_dir = tmp_path / "project-data"
    launch_dir.mkdir()
    payload_dir.mkdir()
    run = allocate_split_run(
        resolve_launch_paths(launch_dir),
        resolve_project_db_root(payload_dir, resolve_launch_paths(launch_dir)),
        library="lib",
        cell="cell",
        view="rnm",
        kind="recipe",
        request_digest=stable_digest({"test": "locator"}),
    )
    coverage = run.payload_root / "evidence" / "coverage.ucis"
    waves = run.payload_root / "evidence" / "waves.shm"
    coverage.parent.mkdir()
    coverage.write_text("ucis\n", encoding="utf-8")
    waves.mkdir()
    (waves / "index").write_text("index\n", encoding="utf-8")
    publish_split_manifests(
        run,
        request_digest=stable_digest({"test": "locator"}),
        status="PASS",
        control_details={},
        payload_details={},
        payload_locators=(
            {"path": "evidence/coverage.ucis", "kind": "file", "exists": True, "producer": "xcelium.coverage"},
            {"path": "evidence/waves.shm", "kind": "directory", "exists": True, "producer": "xcelium.waveform"},
        ),
    )
    verified = verify_split_manifests(run.control_manifest, run.payload_manifest)
    assert verified["locator_counts"] == {"control": 0, "payload": 2}
    resolved = resolve_indexed_locator(run.control_manifest, "evidence/waves.shm")
    assert resolved.kind == "directory"
    assert resolved.producer == "xcelium.waveform"
    assert resolved.path == waves.resolve()
    (waves / "new-entry").write_text("later\n", encoding="utf-8")
    assert resolve_indexed_locator(run.control_manifest, "evidence/waves.shm").path == waves.resolve()


@pytest.mark.parametrize(
    "locator, pattern",
    [
        ({"path": "../outside", "kind": "directory", "exists": True, "producer": "xcelium"}, "unsafe"),
        ({"path": "/tmp/outside", "kind": "directory", "exists": True, "producer": "xcelium"}, "unsafe"),
        ({"path": "missing", "kind": "directory", "exists": True, "producer": "xcelium"}, "missing"),
    ],
)
def test_artifact_locator_index_rejects_unsafe_or_missing_paths(
    tmp_path: Path, locator: dict[str, object], pattern: str
) -> None:
    with pytest.raises(WorkspaceError, match=pattern):
        artifact_locator_index(tmp_path, (locator,))


def test_artifact_locator_index_rejects_symlink_and_type_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(WorkspaceError, match="symlink"):
        artifact_locator_index(
            tmp_path,
            ({"path": "link", "kind": "file", "exists": True, "producer": "xcelium"},),
        )
    with pytest.raises(WorkspaceError, match="type mismatch"):
        artifact_locator_index(
            tmp_path,
            ({"path": "target", "kind": "directory", "exists": True, "producer": "xcelium"},),
        )
