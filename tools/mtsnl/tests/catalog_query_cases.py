"""catalog query cases regressions."""

from __future__ import annotations
import hashlib
from pathlib import Path
import pytest
from mtsnetlistor.catalog import (
    clear_source_catalog_cache,
    load_source_catalog,
    load_target_catalog,
)
from mtsnetlistor.environment import SessionDescriptor
from catalog_fixtures import _write_dbaccess, _counting_dbaccess, _source_fixture


def test_source_catalog_does_not_return_cleaned_overlay_path(tmp_path: Path) -> None:
    clear_source_catalog_cache()
    source_cds_lib = tmp_path / "source.cds.lib"
    source_library = tmp_path / "source-lib"
    source_library.mkdir()
    source_cds_lib.write_text("DEFINE source ./source-lib\n", encoding="utf-8")
    dbaccess = _write_dbaccess(tmp_path / "dbAccess", "source", source_library)

    result = load_source_catalog(source_cds_lib, dbaccess=str(dbaccess))

    assert result.authoritative is True
    assert result.catalog.cds_library_file == source_cds_lib.resolve()
    assert result.catalog.cds_library_file.is_file()
    assert not any(tmp_path.glob("mts-netlistor-catalog-*/source-overlay.cds.lib"))


def test_target_catalog_does_not_return_cleaned_overlay_path(tmp_path: Path) -> None:
    clear_source_catalog_cache()
    target_cds_lib = tmp_path / "target.cds.lib"
    target_library = tmp_path / "target-lib"
    target_library.mkdir()
    target_cds_lib.write_text("DEFINE target ./target-lib\n", encoding="utf-8")
    dbaccess = _write_dbaccess(tmp_path / "dbAccess", "target", target_library)
    session = SessionDescriptor(
        owner_pid=1,
        owner_start_time="test",
        target_cds_lib=target_cds_lib,
        target_cds_lib_digest=hashlib.sha256(target_cds_lib.read_bytes()).hexdigest(),
        target_library_paths={"target": str(target_library)},
    )

    result = load_target_catalog(session, dbaccess=str(dbaccess))

    assert result.authoritative is True
    assert result.catalog.cds_library_file == target_cds_lib.resolve()
    assert result.catalog.cds_library_file.is_file()
    assert not any(tmp_path.glob("mts-netlistor-target-catalog-*/target-overlay.cds.lib"))


def test_source_catalog_forwards_cold_provider_output_only(tmp_path: Path) -> None:
    clear_source_catalog_cache()
    source, included, counter = _source_fixture(tmp_path)
    dbaccess = _counting_dbaccess(tmp_path / "dbAccess", counter)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}
    received: list[str] = []

    load_source_catalog(
        source,
        dbaccess=str(dbaccess),
        environment=environment,
        output_callback=received.append,
    )
    cold_output = "".join(received)
    load_source_catalog(
        source,
        dbaccess=str(dbaccess),
        environment=environment,
        output_callback=received.append,
    )

    assert '"schema_version"' in cold_output
    assert "".join(received) == cold_output


@pytest.mark.parametrize("ttl", [float("nan"), float("inf"), -float("inf")])
def test_source_catalog_rejects_nonfinite_cache_ttl(tmp_path: Path, ttl: float) -> None:
    clear_source_catalog_cache()
    source, _included, _counter = _source_fixture(tmp_path)
    with pytest.raises(ValueError, match="cache_ttl"):
        load_source_catalog(source, dbaccess=None, cache_ttl=ttl)


@pytest.mark.parametrize(
    "timeout", [0, -1, float("nan"), float("inf"), -float("inf"), "bad"]
)
def test_source_catalog_rejects_invalid_timeout(tmp_path: Path, timeout: object) -> None:
    clear_source_catalog_cache()
    source, _included, _counter = _source_fixture(tmp_path)
    with pytest.raises(ValueError, match="timeout"):
        load_source_catalog(source, dbaccess=None, timeout=timeout)  # type: ignore[arg-type]
