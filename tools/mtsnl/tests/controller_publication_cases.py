"""controller publication cases regressions."""

from __future__ import annotations
from pathlib import Path
from mtsnetlistor.gui.controller import MtsController
from mtsnetlistor.publish import PublicationBundleResult
from controller_fixtures import (
    _wait,
    _publication_fixture,
)


def test_publish_many_validates_all_artifacts_before_any_publication(
    tmp_path: Path, monkeypatch
) -> None:
    import mtsnetlistor.gui.controller as module

    session, items = _publication_fixture(tmp_path)
    validations = []
    mutations = []
    monkeypatch.setattr(module, "preflight_publication", lambda request, *_args, **_kwargs: type(
        "Preflight", (), {
            "target_library": request.target.library,
            "target_cell": request.target.cell,
        }
    )())

    def validate(request, *_args, **_kwargs):
        validations.append(request.source.cell)
        if request.source.cell == "two":
            raise ValueError("injected second artifact failure")

    monkeypatch.setattr(module, "validate_run_artifact", validate)
    monkeypatch.setattr(
        module,
        "publish_bundle",
        lambda request, *_args, **_kwargs: mutations.append(request.source.cell),
    )
    controller = MtsController(session=session)
    try:
        controller.publish_many(items)
        _wait(controller, lambda state: not state.busy)
        assert validations == ["one", "two"]
        assert mutations == []
        assert "injected second artifact failure" in controller.state.error
    finally:
        controller.close()


def test_publish_many_stops_after_first_non_success_result(
    tmp_path: Path, monkeypatch
) -> None:
    import mtsnetlistor.gui.controller as module

    session, items = _publication_fixture(tmp_path)
    calls = []
    monkeypatch.setattr(module, "preflight_publication", lambda request, *_args, **_kwargs: type(
        "Preflight", (), {
            "target_library": request.target.library,
            "target_cell": request.target.cell,
        }
    )())
    monkeypatch.setattr(module, "validate_run_artifact", lambda *_args, **_kwargs: None)

    def publish(request, *_args, **_kwargs):
        calls.append(request.source.cell)
        return PublicationBundleResult(
            "manual_cleanup_required",
            "target",
            request.target.cell or request.source.cell,
            message="injected publication failure",
        )

    monkeypatch.setattr(module, "publish_bundle", publish)
    controller = MtsController(session=session)
    try:
        controller.publish_many(items)
        _wait(controller, lambda state: state.stage == "publication_ready")
        assert calls == ["one"]
        assert isinstance(controller.state.publication, tuple)
        assert len(controller.state.publication) == 1
        assert controller.state.publication[0].status == "manual_cleanup_required"
    finally:
        controller.close()


def test_cancelled_publication_keeps_late_manual_cleanup_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    """Cancel must not hide a publication result that may include OA changes."""
    import mtsnetlistor.gui.controller as module

    session, items = _publication_fixture(tmp_path)
    started = False

    def publish(request, *_args, **kwargs):
        nonlocal started
        started = True
        # Simulate a wrapper that observed cancellation only after the
        # mutating stage and returned its conservative evidence object.
        kwargs["cancel_event"].set()
        return PublicationBundleResult(
            "manual_cleanup_required",
            "target",
            request.target.cell or request.source.cell,
            message="late OA mutation evidence",
            affected_views=(f"target/{request.source.cell}/spectreText",),
        )

    monkeypatch.setattr(module, "preflight_publication", lambda request, *_args, **_kwargs: type(
        "Preflight", (), {
            "target_library": request.target.library,
            "target_cell": request.target.cell,
        }
    )())
    monkeypatch.setattr(module, "validate_run_artifact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "publish_bundle", publish)
    controller = MtsController(session=session)
    try:
        controller.publish_many((items[0],))
        _wait(controller, lambda state: started)
        # The worker itself signals cancellation after entering publication;
        # explicitly cancel to advance the public token and exercise the late
        # callback path.
        controller.cancel()
        _wait(
            controller,
            lambda state: state.stage == "canceled"
            and state.publication is not None,
        )
        assert controller.state.publication[0].status == "manual_cleanup_required"
        assert "late OA mutation evidence" in controller.state.publication[0].message
    finally:
        controller.close()


def test_cancelled_batch_returns_completed_publication_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    """A batch canceled between cells keeps the results already returned."""
    import mtsnetlistor.gui.controller as module

    session, items = _publication_fixture(tmp_path)
    calls = []
    controller_ref = {}

    monkeypatch.setattr(module, "preflight_publication", lambda request, *_args, **_kwargs: type(
        "Preflight", (), {
            "target_library": request.target.library,
            "target_cell": request.target.cell,
        }
    )())
    monkeypatch.setattr(module, "validate_run_artifact", lambda *_args, **_kwargs: None)

    def publish(request, *_args, **kwargs):
        calls.append(request.source.cell)
        if len(calls) == 1:
            controller_ref["controller"].cancel()
        return PublicationBundleResult(
            "succeeded", "target", request.target.cell or request.source.cell
        )

    monkeypatch.setattr(module, "publish_bundle", publish)
    controller = MtsController(session=session)
    controller_ref["controller"] = controller
    try:
        controller.publish_many(items)
        _wait(
            controller,
            lambda state: state.stage == "canceled"
            and state.publication is not None,
        )
        assert calls == ["one"]
        assert isinstance(controller.state.publication, tuple)
        assert len(controller.state.publication) == 1
        assert controller.state.publication[0].status == "succeeded"
    finally:
        controller.close()


def test_late_cancelled_publication_cannot_overwrite_new_operation(
    tmp_path: Path, monkeypatch
) -> None:
    """A new request owns state; an old canceled worker must stay invisible."""
    import mtsnetlistor.gui.controller as module

    session, items = _publication_fixture(tmp_path)
    release = __import__("threading").Event()
    controller = MtsController(session=session)
    try:
        monkeypatch.setattr(module, "preflight_publication", lambda request, *_args, **_kwargs: type(
            "Preflight", (), {
                "target_library": request.target.library,
                "target_cell": request.target.cell,
            }
        )())
        monkeypatch.setattr(module, "validate_run_artifact", lambda *_args, **_kwargs: None)

        def publish(request, *_args, **kwargs):
            release.wait(1.0)
            kwargs["cancel_event"].set()
            return PublicationBundleResult(
                "manual_cleanup_required", "target", request.target.cell or request.source.cell
            )

        monkeypatch.setattr(module, "publish_bundle", publish)
        controller.publish_many((items[0],))
        controller.cancel()
        controller.refresh_source_catalog(tmp_path / "missing.cds.lib", dbaccess=None)
        release.set()
        _wait(controller, lambda state: state.stage != "publishing")
        assert controller.state.publication is None
        assert controller.state.stage != "publication_ready"
    finally:
        release.set()
        controller.close()
