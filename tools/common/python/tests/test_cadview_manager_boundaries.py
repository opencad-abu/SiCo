"""Provider outcomes across the manager's result and cancellation boundary."""

from concurrent.futures import CancelledError
from threading import Event

import pytest

from cadview import (
    Catalog,
    CatalogCancelled,
    CatalogDiagnostics,
    CatalogError,
    CatalogResult,
    LibraryManager,
)
from cadview.manager_provider import invoke_provider
from cadview_manager_fixtures import _cds


def test_provider_result_preserves_its_diagnostic_evidence(tmp_path):
    cds = _cds(tmp_path)
    catalog = Catalog(cds, provider="site", authoritative=True)
    result = CatalogResult(
        catalog,
        CatalogDiagnostics(cds, "site", True, ("partial category metadata",)),
    )
    with LibraryManager(providers={"custom": lambda path: result}) as manager:
        assert manager.load(cds, provider="custom") is result
        assert manager.submit(cds, provider="custom").future.result(timeout=2) is result
    assert result.diagnostics.messages == ("partial category metadata",)


@pytest.mark.parametrize("wrapped", [False, True])
def test_provider_result_is_rejected_after_cooperative_cancellation(tmp_path, wrapped):
    cds = _cds(tmp_path)
    event = Event()

    def provider(path, *, cancel_event):
        assert cancel_event is event
        catalog = Catalog(path, provider="site")
        cancel_event.set()
        if wrapped:
            return CatalogResult(catalog, CatalogDiagnostics(path, "site", False))
        return catalog

    with LibraryManager() as manager:
        with pytest.raises(CatalogCancelled, match="cancelled"):
            manager.load(cds, provider=provider, cancel_event=event)


@pytest.mark.parametrize("loaded", [None, {}, "not a catalog"])
def test_invalid_provider_result_does_not_fall_back_to_filesystem(tmp_path, loaded):
    cds = _cds(tmp_path)
    calls = []

    def invalid(path):
        calls.append(path)
        return loaded

    with LibraryManager(providers={"invalid": invalid}) as manager:
        with pytest.raises(CatalogError, match="invalid Catalog"):
            manager.load(cds, provider="invalid")
    assert calls == [cds.resolve()]


@pytest.mark.parametrize("signature_error", [TypeError, ValueError])
def test_opaque_provider_receives_context_once_and_propagates_failure(tmp_path, signature_error):
    calls = []
    failure = TypeError("failure inside provider")
    context = {"environ": {"PROJECT": "one"}, "cancel_event": Event()}

    class OpaqueProvider:
        @property
        def __signature__(self):
            raise signature_error("signature unavailable")

        def __call__(self, path, **kwargs):
            calls.append((path, kwargs))
            raise failure

    with pytest.raises(TypeError) as caught:
        invoke_provider(OpaqueProvider(), tmp_path, context)
    assert caught.value is failure
    assert calls == [(tmp_path, context)]
    assert calls[0][1]["cancel_event"] is context["cancel_event"]


def test_queued_request_cancellation_never_calls_provider(tmp_path):
    cds = _cds(tmp_path)
    started = Event()
    release = Event()
    calls = []

    def provider(path, *, environ, cancel_event):
        calls.append(environ["label"])
        if environ["label"] == "first":
            started.set()
            assert release.wait(3)
        return Catalog(path, provider="fixture")

    manager = LibraryManager(providers={"fixture": provider}, max_workers=1)
    try:
        first = manager.submit(cds, provider="fixture", environ={"label": "first"})
        assert started.wait(2)
        queued = manager.submit(cds, provider="fixture", environ={"label": "queued"})
        assert first.cancel_requested
        assert queued.cancel()
        with pytest.raises(CancelledError):
            queued.future.result(timeout=2)
        release.set()
        with pytest.raises(CatalogCancelled):
            first.future.result(timeout=2)
        assert calls == ["first"]
    finally:
        release.set()
        manager.close()


def test_cancel_handle_observes_the_providers_event(tmp_path):
    cds = _cds(tmp_path)
    entered = Event()
    released = Event()
    event = Event()

    def provider(path, *, cancel_event):
        assert cancel_event is event
        entered.set()
        assert released.wait(3)
        return Catalog(path)

    manager = LibraryManager()
    try:
        request = manager.submit(cds, provider=provider, cancel_event=event)
        assert entered.wait(2)
        event.set()
        assert request.cancelled and request.cancel_requested
        # A running Future remains running until the provider returns. The
        # request exposes cooperative cancellation without copying that state.
        assert not request.future.cancelled()
        assert not request.done
        released.set()
        with pytest.raises(CatalogCancelled):
            request.future.result(timeout=2)
    finally:
        released.set()
        manager.close()
