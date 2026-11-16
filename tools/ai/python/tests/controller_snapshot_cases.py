"""Schematic snapshot artifacts and authenticated gate-output binding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from cadai.controller import RequestDispatcher, _snapshot_binding_from_aivw
from cadai.runtime import RuntimePaths
from cadai.socket_server import RequestFailure
from controller_transport_fixture import FakeTransport


def test_snapshot_materializes_private_jsonl_artifact(
    runtime: RuntimePaths, tmp_path: Path
) -> None:
    class SnapshotTransport(FakeTransport):
        def request(self, payload: dict[str, Any], timeout: float):
            self.requests.append(dict(payload))
            source_path = Path(payload["source_path"])
            self.sources.append(source_path.read_bytes())
            code = source_path.read_text(encoding="utf-8")
            assert "aiSnapshotSchematic" in code
            snapshot_path = Path(code.split('"')[-2])
            snapshot_path.write_text(
                '\n'.join(
                    (
                        '{"record_type":"header"}',
                        '{"record_type":"footer"}',
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            snapshot_path.chmod(0o600)
            result = {
                "ok": True,
                "value": json.dumps(
                    {
                        "ok": True,
                        "kind": "schematic_snapshot",
                        "artifact_path": str(snapshot_path),
                    }
                ),
                "output": "*WARNING* SNAPSHOT_WARNING\n",
                "output_truncated": False,
                "output_capture": "skill_ports",
            }
            Path(payload["output_path"]).write_text(json.dumps(result), encoding="utf-8")
            return True, {"result_path": payload["output_path"]}

    transport = SnapshotTransport()
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    result = dispatcher(
        "5" * 32,
        "snapshot_schematic",
        {"lib": "work", "cell": "top", "include_placement": True},
    )

    artifact = Path(result["artifact"]["path"])
    assert result["output"] == "*WARNING* SNAPSHOT_WARNING\n"
    assert result["output_capture"] == "skill_ports" and not result["output_truncated"]
    assert artifact.parent == runtime.spool
    assert artifact.suffix == ".jsonl"
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert result["artifact"]["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert list(runtime.virtuoso_spool.iterdir()) == []


def test_snapshot_rejects_virtuoso_artifact_outside_spool(
    runtime: RuntimePaths, tmp_path: Path
) -> None:
    outside = tmp_path / "snapshot-data-outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    outside.chmod(0o600)

    class OutsideSnapshotTransport(FakeTransport):
        def request(self, payload: dict[str, Any], timeout: float):
            self.requests.append(dict(payload))
            result = {
                "ok": True,
                "value": json.dumps({"ok": True, "artifact_path": str(outside)}),
                "output": "",
            }
            Path(payload["output_path"]).write_text(json.dumps(result), encoding="utf-8")
            return True, {"result_path": payload["output_path"]}

    dispatcher = RequestDispatcher(OutsideSnapshotTransport(), runtime, tmp_path)
    with pytest.raises(RequestFailure, match="escaped the session spool"):
        dispatcher(
            "4" * 32,
            "snapshot_schematic",
            {"lib": "work", "cell": "top"},
        )
    assert outside.is_file()


def test_snapshot_rejects_symlink_without_deleting_its_target(
    runtime: RuntimePaths, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-snapshot.jsonl"
    outside.write_text('{"must":"survive"}\n', encoding="utf-8")
    outside.chmod(0o600)
    snapshot_path: Path | None = None

    class SymlinkSnapshotTransport(FakeTransport):
        def request(self, payload: dict[str, Any], timeout: float):
            nonlocal snapshot_path
            self.requests.append(dict(payload))
            source_path = Path(payload["source_path"])
            self.sources.append(source_path.read_bytes())
            code = source_path.read_text(encoding="utf-8")
            snapshot_path = Path(code.split('"')[-2])
            snapshot_path.symlink_to(outside)
            result = {
                "ok": True,
                "value": json.dumps({"ok": True, "artifact_path": str(snapshot_path)}),
                "output": "",
            }
            Path(payload["output_path"]).write_text(json.dumps(result), encoding="utf-8")
            return True, {"result_path": payload["output_path"]}

    dispatcher = RequestDispatcher(SymlinkSnapshotTransport(), runtime, tmp_path)
    with pytest.raises(RequestFailure, match="bounded regular file") as caught:
        dispatcher(
            "6" * 32,
            "snapshot_schematic",
            {"lib": "work", "cell": "top"},
        )

    assert caught.value.code == "invalid_result"
    assert outside.read_text(encoding="utf-8") == '{"must":"survive"}\n'
    assert snapshot_path is not None and not snapshot_path.is_symlink()
    assert list(runtime.virtuoso_spool.iterdir()) == []
    assert list(runtime.spool.iterdir()) == []


def test_snapshot_skill_failure_cleans_partial_artifact(
    runtime: RuntimePaths, tmp_path: Path
) -> None:
    class FailedSnapshotTransport(FakeTransport):
        def request(self, payload: dict[str, Any], timeout: float):
            self.requests.append(dict(payload))
            source_path = Path(payload["source_path"])
            self.sources.append(source_path.read_bytes())
            code = source_path.read_text(encoding="utf-8")
            snapshot_path = Path(code.split('"')[-2])
            snapshot_path.write_text('{"partial":true}\n', encoding="utf-8")
            result = {"ok": False, "error": "snapshot write failed"}
            Path(payload["output_path"]).write_text(json.dumps(result), encoding="utf-8")
            return False, {"message": "snapshot write failed"}

    dispatcher = RequestDispatcher(FailedSnapshotTransport(), runtime, tmp_path)
    with pytest.raises(RequestFailure, match="snapshot write failed"):
        dispatcher(
            "7" * 32,
            "snapshot_schematic",
            {"lib": "work", "cell": "top"},
        )

    assert list(runtime.virtuoso_spool.iterdir()) == []
    assert list(runtime.spool.iterdir()) == []


@pytest.mark.parametrize(
    ("metadata", "expected_code", "message"),
    [
        (
            {"ok": False, "error": "snapshot metadata failed"},
            "skill_error",
            "snapshot metadata failed",
        ),
        ({"ok": True}, "invalid_result", "did not return an artifact"),
    ],
)
def test_snapshot_invalid_inner_metadata_cleans_partial_artifact(
    runtime: RuntimePaths,
    tmp_path: Path,
    metadata: dict[str, Any],
    expected_code: str,
    message: str,
) -> None:
    class InvalidMetadataSnapshotTransport(FakeTransport):
        def request(self, payload: dict[str, Any], timeout: float):
            self.requests.append(dict(payload))
            source_path = Path(payload["source_path"])
            self.sources.append(source_path.read_bytes())
            code = source_path.read_text(encoding="utf-8")
            snapshot_path = Path(code.split('"')[-2])
            snapshot_path.write_text('{"partial":true}\n', encoding="utf-8")
            result = {
                "ok": True,
                "value": json.dumps(metadata),
                "output": "",
            }
            Path(payload["output_path"]).write_text(
                json.dumps(result), encoding="utf-8"
            )
            return True, {"result_path": payload["output_path"]}

    dispatcher = RequestDispatcher(
        InvalidMetadataSnapshotTransport(), runtime, tmp_path
    )
    with pytest.raises(RequestFailure, match=message) as caught:
        dispatcher(
            "8" * 32,
            "snapshot_schematic",
            {"lib": "work", "cell": "top"},
        )

    assert caught.value.code == expected_code
    assert list(runtime.virtuoso_spool.iterdir()) == []
    assert list(runtime.spool.iterdir()) == []


def test_snapshot_binding_reads_the_serialized_gate_outputs_contract() -> None:
    generation = "a" * 64
    value = {
        "details": {
            "gate_results": {
                "snapshot": {
                    "executor": "virtuoso.snapshot",
                    "status": "PASS",
                    "summary": {
                        "source_generation": generation,
                        "target": {"library": "old", "cell": "old", "module": "old"},
                    },
                    "outputs": {
                        "source_generation": generation,
                        "target": {
                            "library": "lib",
                            "cell": "cell",
                            "module": "module",
                        },
                        "view_identity": {
                            "library": "lib",
                            "cell": "cell",
                            "view_name": "schematic",
                            "oa_view_type": "schematic",
                            "oa_view_type_source": "platform_api",
                            "kind": "schematic",
                        },
                    },
                }
            }
        }
    }

    assert _snapshot_binding_from_aivw(value) == {
        "source_generation": generation,
        "target": {"library": "lib", "cell": "cell", "module": "module"},
        "view_identity": {
            "library": "lib",
            "cell": "cell",
            "view_name": "schematic",
            "oa_view_type": "schematic",
            "oa_view_type_source": "platform_api",
            "kind": "schematic",
        },
    }


def test_snapshot_binding_rejects_summary_only_identity() -> None:
    generation = "b" * 64
    assert _snapshot_binding_from_aivw(
        {
            "details": {
                "gate_results": {
                    "snapshot": {
                        "executor": "virtuoso.snapshot",
                        "status": "PASS",
                        "summary": {
                            "source_generation": generation,
                            "target": {"library": "lib", "cell": "cell", "module": "module"},
                            "view_identity": {
                                "library": "lib",
                                "cell": "cell",
                                "view_name": "schematic",
                                "oa_view_type": "schematic",
                                "oa_view_type_source": "platform_api",
                                "kind": "schematic",
                            },
                        },
                    }
                }
            }
        }
    ) is None
