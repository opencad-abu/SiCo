"""Asynchronous manager cancellation and close contracts."""

from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

import pytest

from cadview import Catalog, CatalogRequest, LibraryManager
from cadview_manager_fixtures import _cds


def test_submit_returns_request_and_supersedes_older_request(tmp_path: Path) -> None:
    cds = _cds(tmp_path)
    started = Event()
    release = Event()

    def provider(path: Path, *, environ=None) -> Catalog:
        if environ and environ.get("BLOCK"):
            started.set()
            release.wait(2.0)
        return Catalog(path, (), provider="test")

    with LibraryManager(providers={"test": provider}, max_workers=2) as manager:
        first = manager.submit(cds, provider="test", environ={"BLOCK": "1"})
        assert started.wait(1.0)
        second = manager.submit(cds, provider="test", environ={"BLOCK": ""})
        assert second.future.result(timeout=2).provider == "test"
        assert first.cancelled
        assert first.cancel_requested
        release.set()
        # The first worker may finish after cooperative cancellation; callers
        # must inspect its Future rather than mistaking it for the latest one.
        with pytest.raises(Exception):
            first.future.result(timeout=2)


def test_completed_request_is_not_marked_cancelled_by_later_submission(
    tmp_path: Path,
) -> None:
    cds = _cds(tmp_path)

    def provider(path: Path) -> Catalog:
        return Catalog(path, (), provider="test")

    with LibraryManager(providers={"test": provider}) as manager:
        first = manager.submit(cds, provider="test")
        assert first.future.result(timeout=2).provider == "test"
        assert not first.cancel_requested
        second = manager.submit(cds, provider="test")
        assert second.future.result(timeout=2).provider == "test"
        assert not first.cancel_requested


def test_close_wait_false_can_be_followed_by_wait_true(tmp_path: Path) -> None:
    cds = _cds(tmp_path)
    started = Event()
    release = Event()

    def provider(path: Path, *, cancel_event=None) -> Catalog:
        started.set()
        release.wait(2.0)
        return Catalog(path, (), provider="test")

    manager = LibraryManager(providers={"test": provider})
    request = manager.submit(cds, provider="test")
    assert started.wait(1.0)
    manager.close(wait=False)
    assert request.cancel_requested
    release.set()
    # This second close must join the worker left behind by wait=False.
    manager.close(wait=True)
    assert request.done


def test_concurrent_submit_cannot_escape_latest_request_cancellation(
    tmp_path: Path,
) -> None:
    cds = _cds(tmp_path)
    first_scheduling = Event()
    release_scheduling = Event()
    release_provider = Event()
    started = Event()

    def provider(path: Path, *, environ=None, cancel_event=None) -> Catalog:
        if environ and environ.get("BLOCK"):
            started.set()
            release_provider.wait(2.0)
        return Catalog(path, provider="test")

    manager = LibraryManager(providers={"test": provider}, max_workers=2)
    original_submit = manager._executor.submit
    submit_calls = 0

    def delayed_submit(*args, **kwargs):
        nonlocal submit_calls
        submit_calls += 1
        if submit_calls == 1:
            first_scheduling.set()
            assert release_scheduling.wait(2.0)
        return original_submit(*args, **kwargs)

    manager._executor.submit = delayed_submit  # type: ignore[method-assign]
    requests: dict[str, CatalogRequest] = {}
    errors: list[BaseException] = []

    def submit(name: str) -> None:
        try:
            kwargs = {"environ": {"BLOCK": "1"}} if name == "first" else {}
            requests[name] = manager.submit(cds, provider="test", **kwargs)
        except BaseException as exc:  # pragma: no cover - diagnostic guard
            errors.append(exc)

    first_thread = Thread(target=submit, args=("first",))
    second_thread = Thread(target=submit, args=("second",))
    try:
        first_thread.start()
        assert first_scheduling.wait(1.0)
        # The first submit owns the manager lock while its scheduling hook is
        # paused.  Once released, let that provider enter its blocking section
        # before allowing the newer submission to supersede it.
        release_scheduling.set()
        assert started.wait(1.0)
        second_thread.start()
        first_thread.join(2.0)
        second_thread.join(2.0)

        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        assert errors == []
        first = requests["first"]
        second = requests["second"]
        assert first.cancel_requested
        assert second.future.result(timeout=2).provider == "test"
    finally:
        release_scheduling.set()
        release_provider.set()
        first_thread.join(2.0)
        second_thread.join(2.0)
        manager.close()


def test_cancel_cannot_miss_a_request_during_submit_registration(
    tmp_path: Path,
) -> None:
    cds = _cds(tmp_path)
    scheduling = Event()
    release_submit = Event()
    manager = LibraryManager(
        providers={"test": lambda path: Catalog(path, provider="test")}
    )
    original_submit = manager._executor.submit

    def delayed_submit(*args, **kwargs):
        scheduling.set()
        assert release_submit.wait(2.0)
        return original_submit(*args, **kwargs)

    manager._executor.submit = delayed_submit  # type: ignore[method-assign]
    requests: list[CatalogRequest] = []
    submit_thread = Thread(
        target=lambda: requests.append(manager.submit(cds, provider="test"))
    )
    cancel_thread = Thread(target=manager.cancel)
    try:
        submit_thread.start()
        assert scheduling.wait(1.0)
        cancel_thread.start()
        release_submit.set()
        submit_thread.join(2.0)
        cancel_thread.join(2.0)

        assert not submit_thread.is_alive()
        assert not cancel_thread.is_alive()
        assert len(requests) == 1
        assert requests[0].cancel_requested
    finally:
        release_submit.set()
        submit_thread.join(2.0)
        cancel_thread.join(2.0)
        manager.close()


def test_cancel_event_is_honored_before_provider_invocation(tmp_path: Path) -> None:
    cds = _cds(tmp_path)
    event = Event()
    event.set()
    called = False

    def provider(path: Path, *, environ=None) -> Catalog:
        nonlocal called
        called = True
        return Catalog(path, (), provider="test")

    with LibraryManager(providers={"test": provider}) as manager:
        with pytest.raises(Exception, match="cancel"):
            manager.load(cds, provider="test", cancel_event=event)
    assert called is False


def test_close_is_idempotent_and_rejects_new_submissions(tmp_path: Path) -> None:
    manager = LibraryManager()
    cds = _cds(tmp_path)
    manager.close()
    manager.close()
    with pytest.raises(RuntimeError, match="closed"):
        manager.submit(cds, provider="filesystem")
    with pytest.raises(RuntimeError, match="closed"):
        manager.load(cds, provider="filesystem")
