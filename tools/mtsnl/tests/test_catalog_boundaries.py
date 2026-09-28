"""Source cache lifecycle regressions at the provider/request boundary."""

from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

import pytest

from cadview.catalog import Catalog
from mtsnetlistor import catalog, catalog_cache, catalog_result, catalog_source, catalog_target
from mtsnetlistor import catalog_source_provider as provider
from mtsnetlistor.errors import CatalogError


@pytest.fixture
def source(tmp_path):
    catalog.clear_source_catalog_cache()
    cds = tmp_path / "cds.lib"
    cds.write_text("")
    yield cds
    catalog.clear_source_catalog_cache()


def fake_result(path):
    return Catalog(Path(path), (), authoritative=True, provider="dbAccess")


def test_clear_during_provider_prevents_late_cache_insertion(source, monkeypatch):
    started, release = Event(), Event()
    calls, results = [], []

    def read(path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            started.set()
            assert release.wait(3)
        return fake_result(path)

    monkeypatch.setattr(provider, "dbaccess_catalog", read)
    owner = Thread(target=lambda: results.append(catalog.load_source_catalog(source)))
    owner.start()
    try:
        assert started.wait(3)
        assert catalog.clear_source_catalog_cache(source) == 0
    finally:
        release.set()
        owner.join(3)
    assert not owner.is_alive()
    assert len(results) == 1
    assert "source_catalog_cache=miss-uncached" in results[0].diagnostics
    assert catalog.source_catalog_cache_info() == {"entries": 0, "inflight": 0}
    catalog.load_source_catalog(source)
    assert len(calls) == 2


@pytest.mark.parametrize("reason", ["canceled", "timed out"])
def test_waiter_failure_leaves_owner_running(source, monkeypatch, reason):
    started, release, waiter_joined, cancel = Event(), Event(), Event(), Event()
    owner_results, waiter_errors = [], []
    calls = []

    class TrackingEvent:
        def __init__(self):
            self._event = Event()

        def wait(self, timeout=None):
            waiter_joined.set()
            return self._event.wait(timeout)

        def set(self):
            self._event.set()

    def read(path, **kwargs):
        calls.append(path)
        started.set()
        assert release.wait(3)
        return fake_result(path)

    def waiter():
        try:
            catalog.load_source_catalog(source, cancel_event=cancel, timeout=0.1)
        except CatalogError as exc:
            waiter_errors.append(str(exc))

    monkeypatch.setattr(catalog_cache, "Event", TrackingEvent)
    monkeypatch.setattr(provider, "dbaccess_catalog", read)
    owner = Thread(target=lambda: owner_results.append(catalog.load_source_catalog(source)))
    subscriber = Thread(target=waiter)
    owner.start()
    try:
        assert started.wait(3)
        subscriber.start()
        assert waiter_joined.wait(3)
        if reason == "canceled":
            cancel.set()
        subscriber.join(3)
        assert not subscriber.is_alive()
        assert owner.is_alive()
        assert len(calls) == 1
        assert len(waiter_errors) == 1 and reason in waiter_errors[0]
    finally:
        release.set()
        owner.join(3)
        if subscriber.ident is not None:
            subscriber.join(3)
    assert len(owner_results) == 1
    assert catalog.source_catalog_cache_info() == {"entries": 1, "inflight": 0}


def test_provider_error_wins_over_cleanup_error(source, monkeypatch, tmp_path):
    class BrokenCleanup:
        def __init__(self, **kwargs):
            self.name = str(tmp_path / "work")
            Path(self.name).mkdir()

        def cleanup(self):
            raise OSError("cleanup failed")

    def broken_provider(*args, **kwargs):
        raise ValueError("provider failed")

    monkeypatch.setattr(provider.tempfile, "TemporaryDirectory", BrokenCleanup)
    monkeypatch.setattr(provider, "dbaccess_catalog", broken_provider)
    with pytest.raises(CatalogError, match="provider failed"):
        catalog.load_source_catalog(source)
    assert catalog.source_catalog_cache_info() == {"entries": 0, "inflight": 0}


@pytest.mark.parametrize("fallback", [False, True])
def test_preview_or_incomplete_include_is_never_cached(source, monkeypatch, fallback):
    calls = []

    def read(path, **kwargs):
        calls.append(path)
        if fallback:
            raise RuntimeError("license unavailable")
        return fake_result(path)

    if not fallback:
        source.write_text("INCLUDE $MISSING_INCLUDE\n")
    monkeypatch.setattr(provider, "dbaccess_catalog", read)
    monkeypatch.setattr(provider, "filesystem_catalog", lambda path, **kwargs: replace(fake_result(path), authoritative=False))
    for _ in range(2):
        result = catalog.load_source_catalog(source, environment={}, allow_fallback=fallback)
        assert "source_catalog_cache=miss-uncached" in result.diagnostics
        assert result.authoritative is not fallback
    assert len(calls) == 2
    assert catalog.source_catalog_cache_info() == {"entries": 0, "inflight": 0}


def test_catalog_compatibility_exports_are_identical():
    for owner, names in (
        (catalog_source, ("load_source_catalog",)),
        (catalog_target, ("load_target_catalog", "target_library_or_error")),
        (catalog_result, ("CatalogResult",)),
        (catalog_cache, ("clear_source_catalog_cache", "source_catalog_cache_info")),
    ):
        for name in names:
            assert getattr(catalog, name) is getattr(owner, name)
    assert not any(name in vars(catalog) for name in ("_SOURCE_CATALOG_CACHE", "_SOURCE_CACHE_LOCK", "_CACHE"))
