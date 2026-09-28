"""catalog cache cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from mtsnetlistor.catalog import (
    clear_source_catalog_cache,
    load_source_catalog,
    source_catalog_cache_info,
)
from catalog_fixtures import _counting_dbaccess, _source_fixture


def test_source_catalog_cache_hit_skips_provider_and_reports_timing(tmp_path: Path) -> None:
    clear_source_catalog_cache()
    source, included, counter = _source_fixture(tmp_path)
    dbaccess = _counting_dbaccess(tmp_path / "dbAccess", counter)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}

    first = load_source_catalog(source, dbaccess=str(dbaccess), environment=environment)
    second = load_source_catalog(source, dbaccess=str(dbaccess), environment=environment)

    assert counter.read_text(encoding="utf-8") == "1"
    assert first.catalog == second.catalog
    assert any(item == "source_catalog_cache=miss-stored" for item in first.diagnostics)
    assert any(item == "source_catalog_cache=hit" for item in second.diagnostics)
    assert any(item.startswith("source_fingerprint_ms=") for item in second.diagnostics)
    assert any(item.startswith("source_provider_ms=") for item in second.diagnostics)


def test_source_catalog_force_refresh_bypasses_completed_cache(tmp_path: Path) -> None:
    clear_source_catalog_cache()
    source, included, counter = _source_fixture(tmp_path)
    dbaccess = _counting_dbaccess(tmp_path / "dbAccess", counter)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}

    load_source_catalog(source, dbaccess=str(dbaccess), environment=environment)
    refreshed = load_source_catalog(
        source,
        dbaccess=str(dbaccess),
        environment=environment,
        force_refresh=True,
    )

    assert counter.read_text(encoding="utf-8") == "11"
    assert any(
        item == "source_catalog_cache=miss-stored"
        for item in refreshed.diagnostics
    )


def test_source_catalog_cache_ttl_zero_disables_storage(tmp_path: Path) -> None:
    clear_source_catalog_cache()
    source, _included, counter = _source_fixture(tmp_path)
    dbaccess = _counting_dbaccess(tmp_path / "dbAccess", counter)
    environment = {"CATALOG_INCLUDE": str(tmp_path / "included.cds.lib"), "PATH": str(tmp_path)}
    first = load_source_catalog(source, dbaccess=str(dbaccess), environment=environment, cache_ttl=0)
    second = load_source_catalog(source, dbaccess=str(dbaccess), environment=environment, cache_ttl=0)
    assert counter.read_text(encoding="utf-8") == "11"
    assert source_catalog_cache_info()["entries"] == 0
    assert any(item == "source_catalog_cache=miss-uncached" for item in first.diagnostics)
    assert any(item == "source_catalog_cache=miss-uncached" for item in second.diagnostics)


def test_source_catalog_cache_invalidates_recursive_include_and_environment(
    tmp_path: Path,
) -> None:
    clear_source_catalog_cache()
    source, included, counter = _source_fixture(tmp_path)
    dbaccess = _counting_dbaccess(tmp_path / "dbAccess", counter)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}

    load_source_catalog(source, dbaccess=str(dbaccess), environment=environment)
    included.write_text("DEFINE work ./library\n# changed\n", encoding="utf-8")
    load_source_catalog(source, dbaccess=str(dbaccess), environment=environment)
    changed_environment = dict(environment, CATALOG_INCLUDE=str(tmp_path / "other.cds.lib"))
    # The unresolved include still forms a distinct key, so the provider is
    # invoked again rather than reusing the old project environment's result.
    load_source_catalog(source, dbaccess=str(dbaccess), environment=changed_environment)

    assert counter.read_text(encoding="utf-8") == "111"


def test_source_catalog_cache_separates_forbidden_target_context_without_reading_target(
    tmp_path: Path,
) -> None:
    clear_source_catalog_cache()
    source, included, counter = _source_fixture(tmp_path)
    dbaccess = _counting_dbaccess(tmp_path / "dbAccess", counter)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}
    target_a = tmp_path / "target-a.cds.lib"
    target_b = tmp_path / "target-b.cds.lib"

    load_source_catalog(
        source,
        dbaccess=str(dbaccess),
        environment=environment,
        forbidden_target_cds_lib=target_a,
    )
    load_source_catalog(
        source,
        dbaccess=str(dbaccess),
        environment=environment,
        forbidden_target_cds_lib=target_b,
    )

    assert counter.read_text(encoding="utf-8") == "11"


def test_source_catalog_cache_does_not_retain_failed_provider(tmp_path: Path) -> None:
    clear_source_catalog_cache()
    source, included, counter = _source_fixture(tmp_path)
    dbaccess = _counting_dbaccess(tmp_path / "dbAccess", counter, fail=True)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}
    with pytest.raises(Exception):
        load_source_catalog(source, dbaccess=str(dbaccess), environment=environment)
    with pytest.raises(Exception):
        load_source_catalog(source, dbaccess=str(dbaccess), environment=environment)
    assert counter.read_text(encoding="utf-8") == "11"
    assert source_catalog_cache_info()["entries"] == 0
