from __future__ import annotations

import shutil
import os
import time
from pathlib import Path

import pytest

from rcepy.dspf import (
    DspfRepository,
    IndexCancelled,
    build_index,
    default_cache_dir,
    index_path_for,
)
from rcepy.dspf.schema import connect_database, get_metadata, validate_database


FIXTURES = Path(__file__).with_name("dspf_fixtures")


def test_default_cache_dir_uses_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RCE_DSPF_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "legacy-xdg"))

    assert default_cache_dir() == tmp_path / ".sico" / "rce" / "dspf"


def test_configured_cache_dir_still_overrides_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured-cache"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RCE_DSPF_CACHE_DIR", str(configured))

    assert default_cache_dir() == configured


def test_default_cache_dir_honors_frontend_temp_root_across_cwd_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch_temp = tmp_path / "launch" / ".sico"
    launch_temp.parent.mkdir()
    changed = tmp_path / "run"
    changed.mkdir()
    monkeypatch.chdir(changed)
    monkeypatch.delenv("RCE_DSPF_CACHE_DIR", raising=False)
    monkeypatch.setenv("SICO_TEMP_DIR", str(launch_temp))

    assert default_cache_dir() == launch_temp / "rce" / "dspf"


def test_bulk_connection_defers_foreign_key_checks(tmp_path: Path) -> None:
    bulk = connect_database(tmp_path / "bulk.sqlite3", bulk_load=True)
    regular = connect_database(tmp_path / "regular.sqlite3")
    try:
        assert bulk.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        assert bulk.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert regular.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        bulk.close()
        regular.close()


def test_full_foreign_key_scan_is_explicit_on_completed_indexes(
    tmp_path: Path,
) -> None:
    result = build_index(FIXTURES / "qrc_1_0.dspf", cache_dir=tmp_path / "cache")
    connection = connect_database(result.index_path, bulk_load=True)
    try:
        connection.execute(
            "INSERT INTO port(net_id,node_id,source_line) VALUES (999999,999999,1)"
        )
        connection.commit()

        validate_database(connection)
        with pytest.raises(ValueError, match="foreign-key failures"):
            validate_database(connection, verify_foreign_keys=True)
    finally:
        connection.close()


def test_element_references_preserve_authoritative_node_source_lines(
    tmp_path: Path,
) -> None:
    source = tmp_path / "node-lines.dspf"
    source.write_text(
        "*|DSPF 1.0\n"
        ".SUBCKT top P 0\n"
        "*|GROUND_NET 0\n"
        "*|NET P 0\n"
        "*|P (P I 0 0 0)\n"
        "*|I (X1:A X1 A I 0 1 0)\n"
        "*|S (P:1 2 0)\n"
        "R1 P P:1 1\n"
        "R2 P X1:A 2\n"
        "C1 P 0 1P\n"
        ".ENDS top\n",
        encoding="ascii",
    )

    result = build_index(source, cache_dir=tmp_path / "cache")
    connection = connect_database(result.index_path, read_only=True)
    try:
        source_lines = dict(connection.execute(
            "SELECT name,source_line FROM node"
        ))
    finally:
        connection.close()

    assert source_lines == {"0": 3, "P": 5, "X1:A": 6, "P:1": 7}


def test_index_builds_atomically_and_reuses_unchanged_source(tmp_path: Path) -> None:
    source = tmp_path / "top.dspf"
    shutil.copyfile(FIXTURES / "qrc_1_0.dspf", source)
    progress = []

    first = build_index(source, cache_dir=tmp_path / "cache", progress=progress.append)
    second = build_index(source, cache_dir=tmp_path / "cache")

    assert first.status == "complete"
    assert first.counts["resistor"] == 2
    assert first.counts["capacitor"] == 3
    assert not first.reused
    assert second.reused
    assert second.index_path == first.index_path
    assert progress[-1].bytes_read == source.stat().st_size
    assert not list((tmp_path / "cache").glob("*.lock"))
    assert not list((tmp_path / "cache").glob("*.tmp"))


def test_source_change_invalidates_cache_key(tmp_path: Path) -> None:
    source = tmp_path / "top.dspf"
    shutil.copyfile(FIXTURES / "qrc_1_0.dspf", source)
    first = build_index(source, cache_dir=tmp_path / "cache")

    source.write_text(source.read_text() + "\n", encoding="utf-8")
    second = build_index(source, cache_dir=tmp_path / "cache")

    assert second.index_path != first.index_path
    assert not second.reused


@pytest.mark.parametrize("version_name", ["PARSER_VERSION", "SCHEMA_VERSION"])
def test_parser_and_schema_versions_change_cache_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version_name: str,
) -> None:
    import rcepy.dspf.indexer as indexer_module

    source = FIXTURES / "qrc_1_0.dspf"
    first = index_path_for(source, tmp_path)
    monkeypatch.setattr(indexer_module, version_name, "next-version")

    assert index_path_for(source, tmp_path) != first


def test_index_path_cannot_overwrite_source_or_unrelated_explicit_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "top.dspf"
    shutil.copyfile(FIXTURES / "qrc_1_0.dspf", source)
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep me", encoding="utf-8")

    with pytest.raises(ValueError, match="must differ"):
        build_index(source, index_path=source, force=True)
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        build_index(source, index_path=unrelated)
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        build_index(source, index_path=unrelated, force=True)

    assert source.read_text().startswith("*|DSPF")
    assert unrelated.read_text() == "keep me"


def test_corrupt_derived_cache_is_rebuilt(tmp_path: Path) -> None:
    source = FIXTURES / "starrc_1_3.spf"
    target = index_path_for(source, tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not sqlite")

    result = build_index(source, cache_dir=tmp_path)

    assert result.index_path == target
    connection = connect_database(target, read_only=True)
    try:
        assert get_metadata(connection)["status"] == "complete"
    finally:
        connection.close()


def test_logically_incomplete_derived_cache_is_rebuilt(tmp_path: Path) -> None:
    source = FIXTURES / "starrc_1_3.spf"
    first = build_index(source, cache_dir=tmp_path)
    connection = connect_database(first.index_path)
    try:
        connection.execute("DROP TABLE layer")
        connection.commit()
    finally:
        connection.close()

    second = build_index(source, cache_dir=tmp_path)

    assert second.index_path == first.index_path
    assert second.reused is False
    connection = connect_database(second.index_path, read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='layer'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_cache_with_missing_query_index_is_rebuilt(tmp_path: Path) -> None:
    source = FIXTURES / "starrc_1_3.spf"
    first = build_index(source, cache_dir=tmp_path)
    connection = connect_database(first.index_path)
    try:
        connection.execute("DROP INDEX idx_capacitor_node1")
        connection.commit()
    finally:
        connection.close()

    second = build_index(source, cache_dir=tmp_path)

    assert second.index_path == first.index_path
    assert second.reused is False
    connection = connect_database(second.index_path, read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type='index' AND name='idx_capacitor_node1'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_cancellation_removes_temporary_database_and_lock(tmp_path: Path) -> None:
    source = FIXTURES / "qrc_1_0.dspf"
    target = index_path_for(source, tmp_path)

    with pytest.raises(IndexCancelled):
        build_index(source, cache_dir=tmp_path, cancelled=lambda: True)

    assert not target.exists()
    assert not list(tmp_path.glob("*.lock"))
    assert not list(tmp_path.glob("*.tmp"))


def test_cancellation_interrupts_secondary_index_creation(tmp_path: Path) -> None:
    source = tmp_path / "large-enough.dspf"
    records = ["*|DSPF 1.0", ".SUBCKT top A", "*|NET A 0"]
    records.extend(f"R{number} A A:{number} 1" for number in range(2_000))
    records.append(".ENDS top")
    source.write_text("\n".join(records) + "\n", encoding="ascii")
    cancel_after_read = False

    def mark_source_read(item) -> None:
        nonlocal cancel_after_read
        cancel_after_read = item.bytes_read == item.total_bytes

    with pytest.raises(IndexCancelled):
        build_index(
            source,
            cache_dir=tmp_path / "cache",
            progress=mark_source_read,
            cancelled=lambda: cancel_after_read,
        )

    assert not list((tmp_path / "cache").glob("*.sqlite3"))
    assert not list((tmp_path / "cache").glob("*.lock"))
    assert not list((tmp_path / "cache").glob("*.tmp"))


def test_compressed_input_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "top.dspf.gz"
    source.write_bytes(b"\x1f\x8b\x08payload")

    with pytest.raises(ValueError, match="Compressed DSPF"):
        build_index(source, cache_dir=tmp_path / "cache")


def test_missing_ends_is_a_recoverable_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "truncated.dspf"
    source.write_text(
        "*|DSPF 1.0\n.SUBCKT top A\n*|NET A 0\nR1 A A:1 1\n",
        encoding="utf-8",
    )

    result = build_index(source, cache_dir=tmp_path / "cache")

    assert result.status == "complete"
    assert result.warning_count == 1


def test_malformed_net_boundary_isolates_index_data_and_diagnostics(
    tmp_path: Path,
) -> None:
    source = tmp_path / "malformed-net-boundary.dspf"
    source.write_text(
        ".SUBCKT top OLD NEXT 0\n"
        "*|GROUND_NET 0\n"
        "*|NET OLD 1P\n"
        "*|P (OLD I 0 0 0)\n"
        "*|S (OLD:1 1 0)\n"
        "Rold OLD OLD:1 1\n"
        "Cold OLD:1 0 1P\n"
        "*|NET\n"
        "*|S (LEAK:1 2 0)\n"
        "Rleak OLD:1 LEAK:1 2\n"
        "Cleak LEAK:1 0 2P\n"
        "*|NET NEXT 3P\n"
        "*|P (NEXT I 0 3 0)\n"
        "*|S (NEXT:1 4 0)\n"
        "Rnext NEXT NEXT:1 3\n"
        "Cnext NEXT:1 0 -4P\n"
        ".ENDS top\n",
        encoding="ascii",
    )

    result = build_index(source, cache_dir=tmp_path / "cache")
    with DspfRepository(result.index_path) as repository:
        old_net = repository.get_net("OLD")
        next_net = repository.get_net("NEXT")
        old_resistors = repository.list_resistors("OLD")
        old_capacitors = repository.list_capacitors("OLD")
        next_resistors = repository.list_resistors("NEXT")
        next_capacitors = repository.list_capacitors("NEXT")
        old_path = repository.resistance_path("OLD", "OLD", "OLD:1")
        leaked_path = repository.resistance_path("OLD", "OLD", "LEAK:1")
        next_path = repository.resistance_path("NEXT", "NEXT", "NEXT:1")
        diagnostics = repository.list_diagnostics()
        old_diagnostics = repository.list_diagnostics("OLD")
        next_diagnostics = repository.list_diagnostics("NEXT")
    connection = connect_database(result.index_path, read_only=True)
    try:
        model_devices = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM device_instance ORDER BY source_line"
            )
        }
    finally:
        connection.close()

    assert result.counts["resistor"] == 2
    assert result.counts["capacitor"] == 2
    assert result.counts["device_instance"] == 2
    assert model_devices == {"Rleak", "Cleak"}
    assert old_net["line_end"] == 7
    assert {item["name"] for item in old_resistors} == {"Rold"}
    assert {item["name"] for item in old_capacitors} == {"Cold"}
    assert {item["name"] for item in next_resistors} == {"Rnext"}
    assert {item["name"] for item in next_capacitors} == {"Cnext"}
    assert old_path.status == "ok"
    assert old_path.resistors == ["Rold"]
    assert leaked_path.status == "node_not_found"
    assert next_path.status == "ok"
    assert next_path.resistors == ["Rnext"]
    assert old_diagnostics == []
    assert [item["code"] for item in next_diagnostics] == ["negative_capacitor"]
    assert [(item["code"], item["net_id"]) for item in diagnostics] == [
        ("malformed_net", None),
        ("connection_outside_net", None),
        ("model_device_ignored", None),
        ("model_device_ignored", None),
        ("negative_capacitor", next_net["id"]),
    ]


def test_stale_build_lock_is_recovered_and_reported(tmp_path: Path) -> None:
    source = FIXTURES / "qrc_1_0.dspf"
    target = index_path_for(source, tmp_path)
    lock = Path(str(target) + ".lock")
    lock.write_text("stale", encoding="ascii")
    old = time.time() - 24 * 60 * 60
    os.utime(lock, (old, old))

    result = build_index(source, cache_dir=tmp_path)

    assert result.warning_count == 3
    assert not lock.exists()


def test_source_change_during_build_discards_index(tmp_path: Path) -> None:
    source = tmp_path / "changing.dspf"
    shutil.copyfile(FIXTURES / "qrc_1_0.dspf", source)
    changed = False

    def mutate_source(_progress) -> None:
        nonlocal changed
        if not changed:
            with source.open("a", encoding="utf-8") as stream:
                stream.write("* changed during indexing\n")
            changed = True

    with pytest.raises(RuntimeError, match="changed while"):
        build_index(source, cache_dir=tmp_path / "cache", progress=mutate_source)

    assert not list((tmp_path / "cache").glob("*.sqlite3"))
