from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from cadai import skill_reference_store
from cadai.mcp import McpServer
from cadai.skill_reference import SkillReference, SkillReferenceUnavailable


def _disable_sqlite(monkeypatch) -> None:
    def unavailable():
        raise ModuleNotFoundError("forced missing _sqlite3")

    monkeypatch.setattr(skill_reference_store, "_import_sqlite", unavailable)


def _read_payload(root: Path) -> dict[str, Any]:
    return json.loads(gzip.decompress((root / "skill_api.json.gz").read_bytes()))


def _write_payload(root: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    (root / "skill_api.json.gz").write_bytes(gzip.compress(encoded, mtime=0))


def test_json_fallback_matches_sqlite_results(reference_root: Path, monkeypatch) -> None:
    sqlite_reference = SkillReference(reference_root)
    expected_search = sqlite_reference.search({"query": "open cellview", "limit": 5})
    expected_get = sqlite_reference.get({"name": "SUBSTRING"})
    sqlite_reference.close()

    _disable_sqlite(monkeypatch)
    fallback_reference = SkillReference(reference_root)

    assert fallback_reference.search({"query": "open cellview", "limit": 5}) == expected_search
    assert fallback_reference.get({"name": "SUBSTRING"}) == expected_get
    fallback_reference.close()


def test_json_fallback_works_when_the_database_payload_is_absent(reference_root: Path) -> None:
    expected = SkillReference(reference_root).search({"query": "open cellview", "limit": 5})
    (reference_root / "skill_api.sqlite3").unlink()

    reference = SkillReference(reference_root)

    assert reference.search({"query": "open cellview", "limit": 5}) == expected
    assert reference.backend == "json-fallback"
    reference.close()


def test_bundled_json_contains_every_sqlite_runtime_record() -> None:
    reference = SkillReference()
    payload = _read_payload(reference.root)
    runtime_fields = tuple(payload["functions"][0])

    with skill_reference_store._import_sqlite().connect(reference.database) as connection:
        connection.row_factory = skill_reference_store._import_sqlite().Row
        records = [
            {field: row[field] for field in runtime_fields}
            for row in connection.execute("SELECT * FROM functions ORDER BY reference_id")
        ]
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))

    assert payload["functions"] == records
    assert payload["metadata"] == {
        "function_count": int(metadata["function_count"]),
        "document_count": int(metadata["document_count"]),
        "copyrights": json.loads(metadata["copyrights"]),
    }
    reference.close()


def test_bundled_json_preserves_duplicate_function_names(monkeypatch) -> None:
    reference = SkillReference()
    expected = reference.get({"name": "defun"})
    reference.close()
    _disable_sqlite(monkeypatch)

    fallback = SkillReference()

    assert fallback.get({"name": "defun"}) == expected
    fallback.close()


@pytest.mark.parametrize("use_fallback", [False, True])
def test_reference_can_be_reopened_after_close(
    reference_root: Path, monkeypatch, use_fallback: bool
) -> None:
    if use_fallback:
        _disable_sqlite(monkeypatch)
    reference = SkillReference(reference_root)

    assert reference.get({"name": "substring"})["matches"]
    reference.close()
    assert reference.get({"name": "substring"})["matches"]
    reference.close()


def test_sqlite_corruption_is_not_hidden_by_fallback(
    reference_root: Path,
) -> None:
    (reference_root / "skill_api.sqlite3").write_bytes(b"not a database")
    reference = SkillReference(reference_root)

    with pytest.raises(SkillReferenceUnavailable, match="cannot open SKILL API database"):
        reference.search({"query": "substring"})


def test_fallback_rejects_case_insensitive_duplicate_ids(
    reference_root: Path, monkeypatch
) -> None:
    payload = _read_payload(reference_root)
    duplicate = dict(payload["functions"][0])
    duplicate["reference_id"] = duplicate["reference_id"].upper()
    payload["functions"].append(duplicate)
    payload["metadata"]["function_count"] += 1
    _write_payload(reference_root, payload)
    _disable_sqlite(monkeypatch)

    with pytest.raises(SkillReferenceUnavailable, match="duplicate reference ids"):
        SkillReference(reference_root).search({"query": "substring"})


@pytest.mark.parametrize(
    "fallback_bytes",
    [
        b"not gzip",
        gzip.compress(b'{"schema_version":1}', mtime=0)[:-4],
        gzip.compress(b"not JSON", mtime=0),
    ],
)
def test_bad_fallback_only_disables_reference_tools(
    tmp_path: Path, monkeypatch, fallback_bytes: bytes
) -> None:
    root = tmp_path / "reference"
    root.mkdir()
    (root / "skill_api.json.gz").write_bytes(fallback_bytes)
    _disable_sqlite(monkeypatch)

    class Client:
        def call(self, method: str, arguments: dict[str, Any]):
            return True, {"method": method, "arguments": arguments}

        def close(self) -> None:
            pass

    server = McpServer(Client(), skill_reference=SkillReference(root))
    failed = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_skill_api", "arguments": {"query": "substring"}},
        }
    )
    healthy = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_context", "arguments": {}},
        }
    )

    assert failed["result"]["isError"] is True
    error = json.loads(failed["result"]["content"][0]["text"])
    assert error["code"] == "skill_reference_unavailable"
    assert healthy["result"]["isError"] is False


def test_missing_fallback_reports_both_unavailable_dependencies(
    tmp_path: Path, monkeypatch
) -> None:
    _disable_sqlite(monkeypatch)

    with pytest.raises(SkillReferenceUnavailable) as error:
        SkillReference(tmp_path / "missing").search({"query": "substring"})

    assert "Python sqlite3 is unavailable" in str(error.value)
    assert "cannot open SKILL API fallback" in str(error.value)
    assert "SICO_AI_SKILL_REFERENCE_ROOT" in str(error.value)


def test_fallback_enforces_compressed_and_expanded_limits(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "reference"
    root.mkdir()
    fallback = root / "skill_api.json.gz"
    _disable_sqlite(monkeypatch)
    monkeypatch.setattr(skill_reference_store, "MAX_FALLBACK_COMPRESSED_BYTES", 8)
    fallback.write_bytes(b"123456789")

    with pytest.raises(SkillReferenceUnavailable, match="compressed size limit"):
        SkillReference(root).search({"query": "substring"})

    monkeypatch.setattr(skill_reference_store, "MAX_FALLBACK_COMPRESSED_BYTES", 1_024)
    monkeypatch.setattr(skill_reference_store, "MAX_FALLBACK_BYTES", 8)
    fallback.write_bytes(gzip.compress(b"123456789", mtime=0))

    with pytest.raises(SkillReferenceUnavailable, match="expands beyond the size limit"):
        SkillReference(root).search({"query": "substring"})


def test_fallback_wraps_corrupt_deflate_errors(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "reference"
    root.mkdir()
    fallback = bytearray(gzip.compress(b"a" * 4_096, mtime=0))
    fallback[len(fallback) // 2] ^= 0xFF
    (root / "skill_api.json.gz").write_bytes(fallback)
    _disable_sqlite(monkeypatch)

    with pytest.raises(SkillReferenceUnavailable, match="cannot open SKILL API fallback"):
        SkillReference(root).search({"query": "substring"})
