"""catalog sharing cases regressions."""

from __future__ import annotations
from pathlib import Path
from threading import Barrier, Event, Thread
import time
import pytest
from cadview.catalog import Catalog
from mtsnetlistor.catalog import (
    clear_source_catalog_cache,
    load_source_catalog,
    source_catalog_cache_info,
)
from catalog_fixtures import _counting_dbaccess, _source_fixture


def test_source_catalog_cache_single_flight_for_same_key(tmp_path: Path) -> None:
    clear_source_catalog_cache()
    source, included, counter = _source_fixture(tmp_path)
    dbaccess = _counting_dbaccess(tmp_path / "dbAccess", counter, delay=0.15)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}
    barrier = Barrier(2)
    results: list[object] = []

    def worker() -> None:
        barrier.wait()
        results.append(load_source_catalog(source, dbaccess=str(dbaccess), environment=environment))

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert len(results) == 2
    assert counter.read_text(encoding="utf-8") == "1"
    assert any("shared-wait" in item for item in results[1].diagnostics) or any(
        "shared-wait" in item for item in results[0].diagnostics
    )


def test_source_catalog_single_flight_broadcasts_output_to_waiters(
    tmp_path: Path,
) -> None:
    clear_source_catalog_cache()
    source, included, counter = _source_fixture(tmp_path)
    dbaccess = _counting_dbaccess(tmp_path / "dbAccess", counter, delay=0.15)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}
    first_started = Event()
    first_output: list[str] = []
    second_output: list[str] = []
    results: list[object] = []

    def first_callback(text: str) -> None:
        first_output.append(text)
        if "Starting detached source dbAccess" in text:
            first_started.set()

    first = Thread(
        target=lambda: results.append(
            load_source_catalog(
                source,
                dbaccess=str(dbaccess),
                environment=environment,
                output_callback=first_callback,
            )
        )
    )
    first.start()
    assert first_started.wait(2)
    second = Thread(
        target=lambda: results.append(
            load_source_catalog(
                source,
                dbaccess=str(dbaccess),
                environment=environment,
                output_callback=second_output.append,
            )
        )
    )
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert len(results) == 2
    assert counter.read_text(encoding="utf-8") == "1"
    # The provider's protocol line is emitted near process completion, after
    # the waiter subscribed, and therefore reaches both process tabs.
    assert '"schema_version"' in "".join(first_output)
    assert '"schema_version"' in "".join(second_output)


def test_source_catalog_waiter_retries_after_owner_is_canceled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor import catalog_source_provider as module

    clear_source_catalog_cache()
    source, included, _counter = _source_fixture(tmp_path)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}
    owner_cancel = Event()
    owner_started = Event()
    release_owner = Event()
    calls = 0

    def fake_dbaccess(cds_library_file, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            owner_started.set()
            assert release_owner.wait(2)
            owner_cancel.set()
            raise ValueError("catalog request cancelled")
        return Catalog(
            Path(cds_library_file), (), authoritative=True, provider="dbAccess"
        )

    monkeypatch.setattr(module, "dbaccess_catalog", fake_dbaccess)
    owner_errors: list[BaseException] = []
    waiter_results: list[object] = []

    def owner() -> None:
        try:
            load_source_catalog(
                source,
                dbaccess="dbAccess",
                environment=environment,
                cancel_event=owner_cancel,
            )
        except BaseException as exc:
            owner_errors.append(exc)

    def waiter() -> None:
        waiter_results.append(
            load_source_catalog(source, dbaccess="dbAccess", environment=environment)
        )

    owner_thread = Thread(target=owner)
    owner_thread.start()
    assert owner_started.wait(2)
    waiter_thread = Thread(target=waiter)
    waiter_thread.start()
    deadline = time.monotonic() + 2
    while source_catalog_cache_info()["inflight"] != 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    release_owner.set()
    owner_thread.join(timeout=5)
    waiter_thread.join(timeout=5)

    assert len(owner_errors) == 1
    assert len(waiter_results) == 1
    assert waiter_results[0].authoritative is True
    assert calls == 2
    assert source_catalog_cache_info() == {"entries": 1, "inflight": 0}


def test_source_catalog_cleanup_failure_is_consistent_and_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor import catalog_source_provider as module

    clear_source_catalog_cache()
    source, included, _counter = _source_fixture(tmp_path)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}
    provider_started = Event()
    release_provider = Event()

    class FailingTemporaryDirectory:
        _counter = 0

        def __init__(self, *, prefix: str) -> None:
            type(self)._counter += 1
            self.name = str(tmp_path / f"{prefix}{type(self)._counter}")
            Path(self.name).mkdir()

        def cleanup(self) -> None:
            raise OSError("cleanup failed")

    def fake_dbaccess(cds_library_file, **kwargs):
        provider_started.set()
        assert release_provider.wait(2)
        return Catalog(
            Path(cds_library_file), (), authoritative=True, provider="dbAccess"
        )

    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", FailingTemporaryDirectory)
    monkeypatch.setattr(module, "dbaccess_catalog", fake_dbaccess)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            load_source_catalog(source, dbaccess="dbAccess", environment=environment)
        except BaseException as exc:
            errors.append(exc)

    owner_thread = Thread(target=worker)
    owner_thread.start()
    assert provider_started.wait(2)
    waiter_thread = Thread(target=worker)
    waiter_thread.start()
    release_provider.set()
    owner_thread.join(timeout=5)
    waiter_thread.join(timeout=5)

    assert len(errors) == 2
    assert all(
        isinstance(exc, OSError) and str(exc) == "cleanup failed" for exc in errors
    )
    assert source_catalog_cache_info() == {"entries": 0, "inflight": 0}
