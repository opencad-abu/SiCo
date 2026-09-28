from __future__ import annotations

import os
from pathlib import Path
from threading import Event
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from cadgui.library_browser_controller import CatalogController  # noqa: E402
from cadview import Catalog, CatalogCell, CatalogLibrary, CatalogView  # noqa: E402


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _catalog(path: Path, name: str = "lib") -> Catalog:
    root = path.parent / name
    view = CatalogView("schematic", root / "cell" / "schematic")
    cell = CatalogCell("cell", root / "cell", (view,))
    return Catalog(path, (CatalogLibrary(name, root, True, (cell,), root),), provider="test")


def _pump(application: QApplication, predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    application.processEvents()


def test_injected_provider_and_environment_snapshot(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="utf-8")
    observed: dict[str, object] = {}

    def provider(path: Path, *, environ=None, cancel_event=None) -> Catalog:
        observed["path"] = path
        observed["environment"] = environ
        observed["cancel_event"] = cancel_event
        return _catalog(path)

    controller = CatalogController(providers={"fixture": provider}, provider="fixture")
    results: list[Catalog] = []
    states: list[bool] = []
    controller.catalogReady.connect(results.append)
    controller.busyChanged.connect(states.append)
    environment = {"PROJECT": "fixture"}
    try:
        assert controller.submit(cds, environment=environment) == 1
        environment["PROJECT"] = "mutated"
        _pump(application, lambda: bool(results))
        assert results[0].library("lib") is not None
        assert observed["path"] == cds.resolve()
        assert observed["environment"] == {"PROJECT": "fixture"}
        assert isinstance(observed["cancel_event"], Event)
        assert states == [True, False]
    finally:
        controller.shutdown()


def test_default_filesystem_provider_is_async_and_reusable(
    application: QApplication, tmp_path: Path
) -> None:
    library = tmp_path / "work"
    (library / "cell" / "schematic").mkdir(parents=True)
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    controller = CatalogController(provider="filesystem")
    results: list[Catalog] = []
    controller.catalogReady.connect(results.append)
    try:
        controller.load(cds)
        _pump(application, lambda: bool(results))
        assert results[0].provider == "filesystem"
        assert results[0].library("work").cells[0].views[0].name == "schematic"
    finally:
        controller.shutdown()


def test_stale_result_and_diagnostic_are_discarded(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="utf-8")

    def provider(path: Path, *, environ=None, cancel_event=None):
        label = environ["LABEL"]
        if label == "slow":
            for _ in range(100):
                if cancel_event.is_set():
                    break
                time.sleep(0.005)
        return _catalog(path, label)

    controller = CatalogController(providers={"fixture": provider}, provider="fixture")
    results: list[Catalog] = []
    controller.catalogReady.connect(results.append)
    try:
        first = controller.submit(cds, environment={"LABEL": "slow"})
        second = controller.submit(cds, environment={"LABEL": "fast"})
        _pump(application, lambda: bool(results))
        assert (first, second) == (1, 2)
        assert [item.libraries[0].name for item in results] == ["fast"]
    finally:
        controller.shutdown()


def test_cancel_and_failure_signals_are_current_request_only(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="utf-8")
    started = Event()

    def blocking(path: Path, *, cancel_event=None):
        started.set()
        while not cancel_event.is_set():
            time.sleep(0.005)
        return _catalog(path)

    controller = CatalogController(providers={"blocking": blocking}, provider="blocking")
    failures: list[str] = []
    cancellations: list[int] = []
    controller.failed.connect(failures.append)
    controller.cancelled.connect(cancellations.append)
    try:
        controller.submit(cds)
        _pump(application, started.is_set)
        controller.cancel()
        _pump(application, lambda: not controller.busy)
        assert cancellations == [1]
        assert not failures

        def broken(_path: Path):
            raise RuntimeError("fixture failure")

        controller.manager._providers["broken"] = broken
        controller.provider = "broken"
        controller.submit(cds)
        _pump(application, lambda: bool(failures))
        assert failures == ["RuntimeError: fixture failure"]
    finally:
        controller.shutdown()


def test_shutdown_rejects_new_requests_and_aliases_are_supported(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="utf-8")
    controller = CatalogController(provider="filesystem")
    controller.shutdown()
    with pytest.raises(RuntimeError, match="shut down"):
        controller.submit(cds)

    other = CatalogController(provider="filesystem")
    try:
        with pytest.raises(ValueError, match="environment or environ"):
            other.submit(cds, environment={}, environ={})
    finally:
        other.shutdown()


def test_internally_created_manager_cannot_be_marked_external(
    application: QApplication,
) -> None:
    with pytest.raises(ValueError, match="injected manager"):
        CatalogController(owns_manager=False)


def test_shutdown_timeout_keeps_worker_and_second_call_waits(
    application: QApplication, tmp_path: Path
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="utf-8")
    started = Event()
    release = Event()

    def provider(path: Path, *, cancel_event=None) -> Catalog:
        started.set()
        # Deliberately delay cooperative termination so shutdown(0) exercises
        # the timed-out path while the manager Future is still running.
        release.wait(2.0)
        return _catalog(path)

    controller = CatalogController(providers={"fixture": provider}, provider="fixture")
    results: list[Catalog] = []
    controller.catalogReady.connect(results.append)
    controller.submit(cds)
    _pump(application, started.is_set)
    try:
        assert controller.shutdown(0) is False
        assert controller._workers
        assert controller.busy is False
        with pytest.raises(RuntimeError, match="shut down"):
            controller.submit(cds)

        release.set()
        assert controller.shutdown(2_000) is True
        assert controller._workers == {}
        # Process any completion queued while waitForDone blocked.  Shutdown's
        # generation invalidation must prevent it from publishing stale data.
        application.processEvents()
        assert results == []
    finally:
        release.set()
        controller.shutdown(2_000)
