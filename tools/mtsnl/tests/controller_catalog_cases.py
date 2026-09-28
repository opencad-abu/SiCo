"""controller catalog cases regressions."""

from __future__ import annotations
from pathlib import Path
import time
from mtsnetlistor.gui.controller import MtsController
from controller_fixtures import (
    _wait,
)


def test_controller_discards_stale_catalog_result(tmp_path: Path, monkeypatch) -> None:
    import mtsnetlistor.gui.controller as module

    class Result:
        def __init__(self, name: str) -> None:
            self.name = name

    def fake_catalog(path, **kwargs):
        if str(path).endswith("old.cds.lib"):
            time.sleep(0.08)
        return Result(str(path))

    monkeypatch.setattr(module, "load_source_catalog", fake_catalog)
    states = []
    controller = MtsController(on_state=states.append)
    try:
        old = tmp_path / "old.cds.lib"
        new = tmp_path / "new.cds.lib"
        old.write_text("DEFINE old ./old\n")
        new.write_text("DEFINE new ./new\n")
        controller.refresh_source_catalog(old, dbaccess=None)
        controller.refresh_source_catalog(new, dbaccess=None)
        _wait(controller, lambda state: state.stage == "source_catalog_ready")
        assert controller.state.source_catalog.name == str(new)
        assert all(
            state.source_catalog is None or state.source_catalog.name != str(old)
            for state in states
        )
    finally:
        controller.close()


def test_controller_forwards_source_cache_controls(tmp_path: Path, monkeypatch) -> None:
    import mtsnetlistor.gui.controller as module

    source = tmp_path / "source.cds.lib"
    source.write_text("DEFINE source ./source\n", encoding="utf-8")
    captured = {}

    def fake_catalog(path, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(module, "load_source_catalog", fake_catalog)
    controller = MtsController()
    try:
        controller.refresh_source_catalog(
            source,
            dbaccess=None,
            cache_ttl=12.5,
            force_refresh=True,
        )
        _wait(controller, lambda state: state.stage == "source_catalog_ready")
        assert captured["cache_ttl"] == 12.5
        assert captured["force_refresh"] is True
        assert callable(captured["output_callback"])
    finally:
        controller.close()


def test_controller_forwards_current_catalog_worker_output(
    tmp_path: Path, monkeypatch
) -> None:
    import mtsnetlistor.gui.controller as module

    source = tmp_path / "source.cds.lib"
    source.write_text("DEFINE source ./source\n", encoding="utf-8")
    received: list[str] = []

    def fake_catalog(_path, **kwargs):
        kwargs["output_callback"]("PDK initialization started\n")
        return object()

    monkeypatch.setattr(module, "load_source_catalog", fake_catalog)
    controller = MtsController(on_log=received.append)
    try:
        controller.refresh_source_catalog(source)
        _wait(controller, lambda state: state.stage == "source_catalog_ready")
        assert received == [
            module.CATALOG_LOG_PREFIX + "PDK initialization started\n"
        ]
    finally:
        controller.close()


def test_controller_cancel_marks_busy_operation_canceled(tmp_path: Path, monkeypatch) -> None:
    import mtsnetlistor.gui.controller as module

    def fake_catalog(*_args, **_kwargs):
        time.sleep(0.2)
        return object()

    monkeypatch.setattr(module, "load_source_catalog", fake_catalog)
    controller = MtsController()
    try:
        source = tmp_path / "source.cds.lib"
        source.write_text("DEFINE source ./source\n")
        controller.refresh_source_catalog(source, dbaccess=None)
        controller.cancel()
        _wait(controller, lambda state: state.stage == "canceled" and not state.busy)
    finally:
        controller.close()


def test_controller_keeps_previous_catalog_while_refresh_is_busy(
    tmp_path: Path, monkeypatch
) -> None:
    import mtsnetlistor.gui.controller as module

    class Result:
        def __init__(self, name: str) -> None:
            self.name = name

    started = False

    def fake_catalog(path, **kwargs):
        nonlocal started
        if str(path).endswith("new.cds.lib"):
            started = True
            time.sleep(0.15)
        return Result(str(path))

    monkeypatch.setattr(module, "load_source_catalog", fake_catalog)
    controller = MtsController()
    try:
        old = tmp_path / "old.cds.lib"
        new = tmp_path / "new.cds.lib"
        old.write_text("DEFINE old ./old\n")
        new.write_text("DEFINE new ./new\n")
        controller.refresh_source_catalog(old, dbaccess=None)
        _wait(controller, lambda state: state.stage == "source_catalog_ready")
        previous = controller.state.source_catalog
        assert previous is not None

        controller.refresh_source_catalog(new, dbaccess=None)
        _wait(controller, lambda _state: started)
        assert controller.state.busy is True
        assert controller.state.source_catalog is previous
    finally:
        controller.close()


def test_controller_keeps_generation_result_while_new_catalog_refresh_runs(
    tmp_path: Path, monkeypatch
) -> None:
    import mtsnetlistor.gui.controller as module

    class Result:
        def __init__(self, name: str) -> None:
            self.name = name

    generation = object()

    def fake_generate(*_args, **_kwargs):
        return generation

    def fake_catalog(*_args, **_kwargs):
        time.sleep(0.12)
        return Result("catalog")

    monkeypatch.setattr(module, "generate", fake_generate)
    monkeypatch.setattr(module, "load_source_catalog", fake_catalog)
    controller = MtsController()
    try:
        source = tmp_path / "source.cds.lib"
        source.write_text("DEFINE source ./source\n")
        controller.generate(object())  # fake_generate does not inspect request
        _wait(controller, lambda state: state.stage == "generation_ready")
        assert controller.state.generation is generation
        controller.refresh_source_catalog(source, dbaccess=None)
        assert controller.state.generation is generation
    finally:
        controller.close()
