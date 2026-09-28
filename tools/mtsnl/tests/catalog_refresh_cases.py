"""catalog refresh cases regressions."""

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
from catalog_fixtures import _source_fixture


def test_source_catalog_force_refresh_waits_for_inflight_then_starts_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor import catalog_source_provider as module
    from mtsnetlistor import catalog_cache as cache

    clear_source_catalog_cache()
    source, included, _counter = _source_fixture(tmp_path)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}
    first_started = Event()
    release_first = Event()
    second_started = Event()
    release_second = Event()
    calls = 0
    concurrent = 0
    maximum_concurrent = 0

    def fake_dbaccess(cds_library_file, **kwargs):
        nonlocal calls, concurrent, maximum_concurrent
        calls += 1
        call_number = calls
        concurrent += 1
        maximum_concurrent = max(maximum_concurrent, concurrent)
        try:
            if call_number == 1:
                first_started.set()
                assert release_first.wait(2)
            elif call_number == 2:
                second_started.set()
                assert release_second.wait(2)
            else:  # pragma: no cover - assertion below reports the regression
                pytest.fail(f"unexpected provider call {call_number}")
            return Catalog(
                Path(cds_library_file),
                (),
                authoritative=True,
                provider=f"dbAccess-{call_number}",
            )
        finally:
            concurrent -= 1

    monkeypatch.setattr(module, "dbaccess_catalog", fake_dbaccess)
    owner_results: list[object] = []
    refresh_results: list[object] = []

    owner_thread = Thread(
        target=lambda: owner_results.append(
            load_source_catalog(source, dbaccess="dbAccess", environment=environment)
        )
    )
    owner_thread.start()
    assert first_started.wait(2)

    refresh_thread = Thread(
        target=lambda: refresh_results.append(
            load_source_catalog(
                source,
                dbaccess="dbAccess",
                environment=environment,
                force_refresh=True,
            )
        )
    )
    refresh_thread.start()
    deadline = time.monotonic() + 2
    refresh_registered = False
    while time.monotonic() < deadline:
        with cache._CACHE._lock:
            flights = tuple(cache._CACHE._flights.values())
            refresh_registered = bool(flights and flights[0].refresh_requested)
        if refresh_registered:
            break
        time.sleep(0.01)
    assert refresh_registered

    release_first.set()
    assert second_started.wait(2)
    assert maximum_concurrent == 1
    release_second.set()
    owner_thread.join(timeout=5)
    refresh_thread.join(timeout=5)

    assert len(owner_results) == 1
    assert len(refresh_results) == 1
    assert owner_results[0].catalog.provider == "dbAccess-1"
    assert refresh_results[0].catalog.provider == "dbAccess-2"
    assert calls == 2
    assert maximum_concurrent == 1
    assert source_catalog_cache_info() == {"entries": 1, "inflight": 0}


def test_concurrent_force_refresh_requests_share_one_refresh_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor import catalog_source_provider as module

    clear_source_catalog_cache()
    source, included, _counter = _source_fixture(tmp_path)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}
    provider_started = Event()
    release_provider = Event()
    calls = 0

    def fake_dbaccess(cds_library_file, **kwargs):
        nonlocal calls
        calls += 1
        provider_started.set()
        assert release_provider.wait(2)
        return Catalog(
            Path(cds_library_file), (), authoritative=True, provider="dbAccess"
        )

    monkeypatch.setattr(module, "dbaccess_catalog", fake_dbaccess)
    barrier = Barrier(2)
    results: list[object] = []

    def refresh() -> None:
        barrier.wait()
        results.append(
            load_source_catalog(
                source,
                dbaccess="dbAccess",
                environment=environment,
                force_refresh=True,
            )
        )

    threads = [Thread(target=refresh) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert provider_started.wait(2)
    release_provider.set()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 2
    assert calls == 1
    assert source_catalog_cache_info() == {"entries": 1, "inflight": 0}


def test_force_refresh_join_during_refresh_cleanup_preserves_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor import catalog_source_provider as module
    from mtsnetlistor import catalog_cache as cache

    clear_source_catalog_cache()
    source, included, _counter = _source_fixture(tmp_path)
    environment = {"CATALOG_INCLUDE": str(included), "PATH": str(tmp_path)}
    cleanup_started = Event()
    release_cleanup = Event()
    waiter_joined = Event()
    calls = 0

    class TrackingFlightEvent:
        def __init__(self) -> None:
            self._event = Event()

        def wait(self, timeout=None):
            waiter_joined.set()
            return self._event.wait(timeout)

        def set(self) -> None:
            self._event.set()

    class SlowTemporaryDirectory:
        def __init__(self, *, prefix: str) -> None:
            self.name = str(tmp_path / f"{prefix}slow")
            Path(self.name).mkdir()

        def cleanup(self) -> None:
            cleanup_started.set()
            assert release_cleanup.wait(2)

    def fake_dbaccess(cds_library_file, **kwargs):
        nonlocal calls
        calls += 1
        return Catalog(
            Path(cds_library_file), (), authoritative=True, provider="dbAccess"
        )

    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", SlowTemporaryDirectory)
    monkeypatch.setattr(cache, "Event", TrackingFlightEvent)
    monkeypatch.setattr(module, "dbaccess_catalog", fake_dbaccess)
    results: list[object] = []
    owner_thread = Thread(
        target=lambda: results.append(
            load_source_catalog(
                source,
                dbaccess="dbAccess",
                environment=environment,
                force_refresh=True,
            )
        )
    )
    owner_thread.start()
    assert cleanup_started.wait(2)

    waiter_thread = Thread(
        target=lambda: results.append(
            load_source_catalog(
                source,
                dbaccess="dbAccess",
                environment=environment,
                force_refresh=True,
            )
        )
    )
    waiter_thread.start()
    assert waiter_joined.wait(2)
    release_cleanup.set()
    owner_thread.join(timeout=5)
    waiter_thread.join(timeout=5)

    assert len(results) == 2
    assert calls == 1
    assert source_catalog_cache_info() == {"entries": 1, "inflight": 0}
    load_source_catalog(source, dbaccess="dbAccess", environment=environment)
    assert calls == 1
