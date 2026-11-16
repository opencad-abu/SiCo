"""Composer drafts cross real service sockets into the existing durable inbox."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from agent_service_gui_fixtures import remote_window
from test_codex_turn_inputs import PNG, catalog

from sico.codex.backend import CodexBackend
from sico.codex.turn_options import TurnOptions
from sico.codex.access_policy import default_access
from sico.storage.input_assets import read_asset
from sico_ui.turn_attachments import AttachmentDialog
from sico_ui.turn_settings import TurnSettingsDialog


@pytest.fixture
def native_service(monkeypatch):
    monkeypatch.setattr("sico.service.project_sessions.create_backend",
                        lambda *args: CodexBackend(*args))
    monkeypatch.setattr(TurnOptions, "status", lambda self, busy=False:
                        deepcopy(dict(self.catalog, selected=self.selected, busy=busy)))
    original = TurnOptions.__init__

    def initialize(self, *args):
        original(self, *args)
        self.catalog = catalog()

    monkeypatch.setattr(TurnOptions, "__init__", initialize)
    return SimpleNamespace(model="test-model", label="test", base="test", release=None,
                           options={}, environment={})


def test_separate_controls_submit_frozen_media_and_settings_to_service(
        tmp_path, monkeypatch, native_service):
    with remote_window(tmp_path, monkeypatch, provider=native_service) as (service, window):
        controller = service.owner.controllers["live"]
        controller.paused = True
        composer = window.turn_composer
        wait_until(lambda: composer.model.isEnabled())
        composer.effort.setCurrentIndex(composer.effort.findData("novel"))
        composer.settings.click()
        settings = window.findChild(TurnSettingsDialog)
        wait_until(settings.ok.isEnabled)
        settings.mode.setCurrentIndex(settings.mode.findData("plan"))
        settings.schema.setPlainText("false")
        settings.apply()
        path = tmp_path / "diagram.png"
        path.write_bytes(PNG)
        monkeypatch.setattr("sico_ui.turn_attachments.ask_file",
                            lambda *args, **_kwargs: (str(path), True))
        composer.attachments.click()
        dialog = window.findChild(AttachmentDialog)
        wait_until(dialog.ok.isEnabled)
        dialog.add_input()
        dialog.apply()
        window.input.setPlainText("Analyze this attachment")
        extras = dict(inputs=deepcopy(window.input.turn_inputs),
                      turn_options=deepcopy(window.input.turn_options))
        window.steering_input.queue_action.trigger()
        wait_until(lambda: len(controller.inbox.pending) == 1)
        wait_until(lambda: window.input.draft_content() == ("", None, [], None))
        record = controller.inbox.pending[0]
        message = record["message"]
        assert read_asset(controller.journal, message["inputs"][0]["asset"]) == PNG
        assert message["turn_options"] == {
            "model": "test-model", "effort": "novel", "mode": "plan",
            "serviceTier": "default", "outputSchema": False, "access": default_access(),
        }
        address = window.api.address(window.page.session)
        inputs = service.dispatch.frontend.inputs
        path.unlink()
        retried = inputs.submit(address, record["id"], message["text"], message["context"],
                                extras=extras)
        assert retried.outcome == "accepted" and len(controller.inbox.pending) == 1
        extras["turn_options"]["mode"] = "default"
        conflict = inputs.submit(address, record["id"], message["text"], message["context"],
                                 extras=extras)
        assert conflict.code == "operation_conflict"
        assert inputs.submit(address, record["id"], message["text"],
                             message["context"]).code == "operation_conflict"


def test_service_attachment_failure_keeps_complete_draft(tmp_path, monkeypatch, native_service):
    with remote_window(tmp_path, monkeypatch, provider=native_service) as (service, window):
        service.owner.controllers["live"].paused = True
        wait_until(lambda: window.turn_composer.model.isEnabled())
        window.input.setPlainText("Missing file must preserve my draft")
        window.input.turn_inputs = [{"type": "localImage", "path": str(tmp_path / "missing.png")}]
        window.input.turn_options = {"model": "test-model", "mode": "plan"}
        expected = window.input.draft()
        window.actions.submit()
        wait_until(lambda: not window.submissions)
        assert window.input.draft() == expected
        assert not service.owner.controllers["live"].inbox.pending


def test_observer_cannot_read_or_submit_attachment(tmp_path, monkeypatch, native_service):
    with remote_window(tmp_path, monkeypatch, provider=native_service) as (service, window):
        service.owner.controllers["live"].paused = True
        wait_until(lambda: window.turn_composer.model.isEnabled())
        session = window.page.session
        window.api.control.request(session, "release").result(5)
        reads = []
        monkeypatch.setattr("sico.storage.input_assets.file_bytes",
                            lambda path: reads.append(path) or PNG)
        receipt = window.api.command(session, "submit", "Attachment", inputs=[
            {"type": "localImage", "path": str(tmp_path / "diagram.png")}])
        with pytest.raises(ValueError):
            receipt.result(5)
        assert not reads and not service.owner.controllers["live"].inbox.pending
