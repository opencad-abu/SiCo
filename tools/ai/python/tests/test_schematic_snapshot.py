from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cadai.mcp import McpServer
from cadai.schematic_snapshot import SchematicSnapshotStore, SnapshotError


def _jsonl() -> bytes:
    records = [
        {
            "record_type": "header",
            "schema_version": "cad.schematic.snapshot.v1",
            "kind": "schematic",
            "cellview": {
                "lib": "work", "cell": "top", "view_name": "schematic",
                "oa_view_type": "schematic",
                "oa_view_type_source": "platform_api",
            },
            "modified": False,
            "hierarchy_included": False,
        },
        {
            "record_type": "instance",
            "data": {
                "name": "M0",
                "lib": "analogLib",
                "cell": "nmos4",
                "view_name": "symbol",
                "term_count": 2,
                "terminals": [
                    {"instance": "M0", "name": "D", "net": "out"},
                    {"instance": "M0", "name": "G", "net": "in"},
                ],
            },
        },
        {
            "record_type": "instance",
            "data": {
                "name": "M1",
                "lib": "analogLib",
                "cell": "pmos4",
                "view_name": "symbol",
                "term_count": 1,
                "terminals": [{"instance": "M1", "name": "D", "net": "out"}],
            },
        },
        {
            "record_type": "net",
            "data": {
                "name": "out",
                "bits": 1,
                "signal_type": "signal",
                "global": False,
                "instance_terminal_count": 2,
            },
        },
        {
            "record_type": "terminal",
            "data": {
                "name": "OUT",
                "direction": "output",
                "bits": 1,
                "pin_count": 1,
                "pins": [{"net": "out"}],
            },
        },
        {
            "record_type": "footer",
            "counts": {"instances": 2, "nets": 1, "terminals": 1},
            "truncated": False,
        },
    ]
    return b"".join(
        (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        for record in records
    )


def _private_spool(tmp_path: Path) -> Path:
    spool = tmp_path / "spool"
    spool.mkdir(mode=0o700)
    return spool


def _artifact(spool: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = _jsonl() if payload is None else payload
    path = spool / "schematic-snapshot-test.jsonl"
    path.write_bytes(data)
    path.chmod(0o600)
    return {
        "path": str(path),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def test_snapshot_store_pages_filters_gets_and_exports(tmp_path: Path) -> None:
    spool = _private_spool(tmp_path)
    store = SchematicSnapshotStore(spool)
    registered = store.register(_artifact(spool))
    snapshot_id = registered["snapshot_id"]
    assert registered["summary"]["counts"] == {
        "instances": 2,
        "nets": 1,
        "terminals": 1,
    }
    assert registered["summary"]["complete"] is True
    assert registered["summary"]["hierarchy_included"] is False
    assert registered["summary"]["modified"] is False

    first = store.query(snapshot_id, entity="instance", limit=1)
    assert [item["name"] for item in first["items"]] == ["M0"]
    assert first["next_cursor"]
    second = store.query(
        snapshot_id, entity="instance", limit=1, cursor=first["next_cursor"]
    )
    assert [item["name"] for item in second["items"]] == ["M1"]
    assert second["next_cursor"] is None

    connected = store.query(snapshot_id, net="out", limit=10)
    assert [item["name"] for item in connected["items"]] == ["M0", "M1", "out", "OUT"]
    exact = store.get_item(snapshot_id, entity="instance", name="M0")
    assert exact["found"] is True
    assert exact["item"]["hierarchy_path"] == ""
    assert store.get_item(snapshot_id, entity="net", name="missing")["found"] is False

    exported = store.export_text(snapshot_id, entity="instance", name_prefix="M")
    text_path = Path(exported["artifact"]["path"])
    assert text_path.parent == spool
    text = text_path.read_text(encoding="utf-8")
    assert "instance\tM0" in text and "instance\tM1" in text
    assert "\tnet\tout" not in text
    assert exported["artifact"]["sha256"] == hashlib.sha256(text_path.read_bytes()).hexdigest()
    store.close()
    assert not list(spool.glob(".*.sqlite3"))


def test_snapshot_store_queries_from_another_thread(tmp_path: Path) -> None:
    spool = _private_spool(tmp_path)
    store = SchematicSnapshotStore(spool)
    snapshot_id = store.register(_artifact(spool))["snapshot_id"]
    outcomes = []

    def query():
        try:
            outcomes.append(store.query(snapshot_id, entity="instance")["count"])
        except BaseException as exc:
            outcomes.append(exc)

    worker = threading.Thread(target=query)
    worker.start()
    worker.join()
    assert outcomes == [2]
    store.close()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda lines: lines[:-1], "missing its footer"),
        (
            lambda lines: [
                *lines[:-1],
                {**lines[-1], "counts": {"instances": 99, "nets": 1, "terminals": 1}},
            ],
            "counts do not match",
        ),
        (lambda lines: [{**lines[0], "schema_version": "unknown"}, *lines[1:]], "schema"),
        (lambda lines: [*lines[:-1], {**lines[-1], "truncated": True}], "truncated"),
    ],
)
def test_snapshot_store_rejects_incomplete_or_inconsistent_jsonl(
    tmp_path: Path, mutate, message: str
) -> None:
    spool = _private_spool(tmp_path)
    lines = [json.loads(line) for line in _jsonl().decode().splitlines()]
    payload = b"".join(
        (json.dumps(value, separators=(",", ":")) + "\n").encode()
        for value in mutate(lines)
    )
    store = SchematicSnapshotStore(spool)
    with pytest.raises(SnapshotError, match=message):
        store.register(_artifact(spool, payload))


def test_snapshot_store_rejects_artifact_outside_private_spool(tmp_path: Path) -> None:
    spool = _private_spool(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(_jsonl())
    outside.chmod(0o600)
    store = SchematicSnapshotStore(spool)
    with pytest.raises(SnapshotError, match="outside"):
        store.register(
            {
                "path": str(outside),
                "size": outside.stat().st_size,
                "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
            }
        )


def test_snapshot_store_rejects_symlinked_artifact(tmp_path: Path) -> None:
    spool = _private_spool(tmp_path)
    target = spool / "target.jsonl"
    target.write_bytes(_jsonl())
    target.chmod(0o600)
    link = spool / "schematic-snapshot-link.jsonl"
    link.symlink_to(target)
    store = SchematicSnapshotStore(spool)
    with pytest.raises(SnapshotError, match="private regular file"):
        store.register(
            {
                "path": str(link),
                "size": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )


def test_snapshot_store_requires_explicit_non_truncated_footer(tmp_path: Path) -> None:
    spool = _private_spool(tmp_path)
    lines = [json.loads(line) for line in _jsonl().decode().splitlines()]
    del lines[-1]["truncated"]
    payload = b"".join(
        (json.dumps(value, separators=(",", ":")) + "\n").encode()
        for value in lines
    )
    store = SchematicSnapshotStore(spool)
    with pytest.raises(SnapshotError, match="explicitly declare"):
        store.register(_artifact(spool, payload))


def test_snapshot_store_requires_explicit_direct_cellview_scope(tmp_path: Path) -> None:
    spool = _private_spool(tmp_path)
    lines = [json.loads(line) for line in _jsonl().decode().splitlines()]
    del lines[0]["hierarchy_included"]
    payload = b"".join(
        (json.dumps(value, separators=(",", ":")) + "\n").encode()
        for value in lines
    )
    store = SchematicSnapshotStore(spool)
    with pytest.raises(SnapshotError, match="hierarchy_included=false"):
        store.register(_artifact(spool, payload))


def test_snapshot_store_rejects_malformed_entity_records(tmp_path: Path) -> None:
    spool = _private_spool(tmp_path)
    lines = [json.loads(line) for line in _jsonl().decode().splitlines()]
    lines[1]["data"]["term_count"] = 99
    payload = b"".join(
        (json.dumps(value, separators=(",", ":")) + "\n").encode()
        for value in lines
    )
    store = SchematicSnapshotStore(spool)
    with pytest.raises(SnapshotError, match="terminal count does not match"):
        store.register(_artifact(spool, payload))


def test_snapshot_store_reports_missing_sqlite_only_when_created(
    tmp_path: Path, monkeypatch
) -> None:
    import cadai.schematic_snapshot as snapshot_module

    def missing_sqlite():
        raise ModuleNotFoundError("forced missing _sqlite3")

    monkeypatch.setattr(snapshot_module, "_import_sqlite", missing_sqlite)
    with pytest.raises(SnapshotError, match="require Python sqlite3 support"):
        SchematicSnapshotStore(_private_spool(tmp_path))


class _SnapshotClient:
    def __init__(self, spool: Path):
        self.runtime = SimpleNamespace(spool=spool)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.artifact = _artifact(spool)

    def call(self, method: str, arguments: dict[str, Any]):
        self.calls.append((method, arguments))
        return True, {"ok": True, "artifact": self.artifact}

    def close(self) -> None:
        pass


def _call(server: McpServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": name,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    return response["result"]


def test_mcp_snapshot_pipeline_routes_once_then_queries_locally(tmp_path: Path) -> None:
    spool = _private_spool(tmp_path)
    client = _SnapshotClient(spool)
    server = McpServer(client)
    created = _call(
        server,
        "snapshot_schematic",
        {"lib": "work", "cell": "top", "include_placement": False},
    )
    assert created["isError"] is False
    snapshot_id = json.loads(created["content"][0]["text"])["snapshot_id"]
    queried = _call(
        server,
        "query_schematic",
        {"snapshot_id": snapshot_id, "entity": "instance", "limit": 1},
    )
    assert queried["isError"] is False
    item = _call(
        server,
        "get_schematic_item",
        {"snapshot_id": snapshot_id, "entity": "instance", "name": "M0"},
    )
    assert item["isError"] is False
    exported = _call(server, "export_schematic_text", {"snapshot_id": snapshot_id})
    assert exported["isError"] is False
    assert client.calls == [
        (
            "snapshot_schematic",
            {"lib": "work", "cell": "top", "include_placement": False},
        )
    ]


def test_mcp_snapshot_rejects_bad_query_before_sql(tmp_path: Path) -> None:
    spool = _private_spool(tmp_path)
    server = McpServer(_SnapshotClient(spool))
    result = _call(
        server,
        "query_schematic",
        {"snapshot_id": "not-a-snapshot", "limit": "50"},
    )
    assert result["isError"] is True
    detail = json.loads(result["content"][0]["text"])
    assert detail["code"] == "snapshot_error"
