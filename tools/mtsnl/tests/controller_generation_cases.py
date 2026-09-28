"""controller generation cases regressions."""

from __future__ import annotations
from pathlib import Path
from mtsnetlistor.environment import SessionDescriptor
from mtsnetlistor.gui.controller import MtsController
from mtsnetlistor.defaults import SourceDefaults
from mtsnetlistor.model import SourceDesign
from controller_fixtures import (
    _wait,
)


def test_controller_forwards_session_target_cds_lib_as_source_forbidden_path(
    tmp_path: Path, monkeypatch
) -> None:
    import mtsnetlistor.gui.controller as module

    source = tmp_path / "source.cds.lib"
    source.write_text("DEFINE source ./source\n", encoding="utf-8")
    target = tmp_path / "target.cds.lib"
    target.write_text("DEFINE target ./target\n", encoding="utf-8")
    session = SessionDescriptor(
        owner_pid=1,
        owner_start_time="test",
        target_cds_lib=target,
        target_cds_lib_digest=__import__("hashlib").sha256(target.read_bytes()).hexdigest(),
        target_library_paths={},
    )
    captured = {}

    def fake_catalog(path, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(module, "load_source_catalog", fake_catalog)
    controller = MtsController(session=session)
    try:
        controller.refresh_source_catalog(source, dbaccess=None)
        _wait(controller, lambda state: state.stage == "source_catalog_ready")
        assert captured["path"] == source
        assert captured["forbidden_target_cds_lib"] == target
    finally:
        controller.close()


def test_controller_generate_many_uses_batch_worker(monkeypatch) -> None:
    import mtsnetlistor.gui.controller as module

    result = object()
    captured = {}

    def fake_generate_many(request, **kwargs):
        captured["request"] = request
        captured["cancel"] = kwargs["cancel_event"]
        return result

    monkeypatch.setattr(module, "generate_many", fake_generate_many)
    controller = MtsController()
    try:
        request = object()
        controller.generate_many(request)  # type: ignore[arg-type]
        _wait(controller, lambda state: state.stage == "generation_ready")
        assert captured["request"] is request
        assert captured["cancel"] is not None
        assert controller.state.generation is result
    finally:
        controller.close()


def test_controller_forwards_worker_logs_to_on_log(monkeypatch) -> None:
    import mtsnetlistor.gui.controller as module

    result = object()
    received: list[str] = []

    def fake_generate(_request, **kwargs):
        callback = kwargs["output_callback"]
        callback("OCEAN: createNetlist\n")
        return result

    monkeypatch.setattr(module, "generate", fake_generate)
    controller = MtsController(on_log=received.append)
    try:
        controller.generate(object())  # type: ignore[arg-type]
        _wait(controller, lambda state: state.stage == "generation_ready")
        assert received == ["OCEAN: createNetlist\n"]
        assert controller.state.generation is result
    finally:
        controller.close()


def test_controller_read_source_defaults_returns_typed_result(
    tmp_path: Path, monkeypatch
) -> None:
    import mtsnetlistor.gui.controller as module

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    source = SourceDesign(cds, "source", "inv")
    result = SourceDefaults(
        "asi_initialization", "spectre", source, (), (), "27.000", "1e-6"
    )
    monkeypatch.setattr(module, "workflow_read_source_defaults", lambda *_args, **_kwargs: result)
    controller = MtsController()
    try:
        controller.read_source_defaults(source)
        _wait(controller, lambda state: state.stage == "defaults_ready")
        assert controller.state.defaults is result
        assert isinstance(controller.state.defaults, SourceDefaults)
    finally:
        controller.close()
