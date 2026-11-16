"""Credential entry and parent shutdown work during Qt's nested modal event loop."""

from __future__ import annotations

import json
import os
import queue
import threading
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from agent_local_startup_fixtures import local_setup as local_setup
from agent_startup_fixture import startup_backend

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QLineEdit
from sico import PRODUCT_NAME
from sico.providers.config import load_provider
from sico.service.startup_result import StartupFailure
from sico_ui.credentials import CredentialCancelled, credential_environment
from sico_ui.lifecycle import DesktopControl
from sico_ui.si_prompt import notice


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.delenv("SICO_TEST_KEY", raising=False)
    path = tmp_path / "model.json"
    path.write_text(
        json.dumps(
            {
                "provider": "anthropic",
                "base_url": "https://gateway.example/v1",
                "model": "test-model",
                "api_key_env": "SICO_TEST_KEY",
            }
        )
    )
    return path


def test_key_prompt_masks_and_keeps_secret_only_in_returned_memory(config):
    app = QApplication.instance() or QApplication([])
    masked = []
    titles = []

    def enter():
        dialog = app.activeModalWidget()
        titles.append(dialog.title_bar.title.text())
        masked.append(dialog.field.echoMode() == QLineEdit.Password)
        dialog.field.setText("  in-memory-test-secret  ")
        dialog.pick("accept")

    QTimer.singleShot(25, enter)
    env = credential_environment("SICO_TEST_KEY")
    assert titles == [f"{PRODUCT_NAME} API 密钥"]
    assert masked == [True]
    assert env["SICO_TEST_KEY"] == "in-memory-test-secret"
    assert "SICO_TEST_KEY" not in os.environ
    assert "in-memory-test-secret" not in config.read_text()
    assert load_provider(config, environment=env).endpoint == "https://gateway.example/v1/messages"


def test_inherited_key_never_opens_prompt(config, monkeypatch):
    monkeypatch.setenv("SICO_TEST_KEY", "existing-secret")

    def unexpected(*args):
        pytest.fail("Prompt opened for an inherited credential")

    monkeypatch.setattr("sico_ui.credentials.ask_text", unexpected)
    assert credential_environment("SICO_TEST_KEY")["SICO_TEST_KEY"] == "existing-secret"


def test_cancel_prompt_is_normal_cancellation(config):
    app = QApplication.instance() or QApplication([])
    QTimer.singleShot(25, lambda: app.activeModalWidget().reject())
    with pytest.raises(CredentialCancelled):
        credential_environment("SICO_TEST_KEY")


def test_invalid_configuration_rejected_before_key_prompt(config, local_setup, monkeypatch):
    config.write_text('{"provider":"anthropic","api_key":"do-not-store"}')
    monkeypatch.setattr("sico_ui.credentials.ask_text",
                        lambda *args, **kwargs: pytest.fail("Unexpected prompt"))
    monkeypatch.setattr("sico_ui.startup.notice", lambda *args, **kwargs: None)
    startup, view = local_setup.create(provider_config=config)
    wait_until(lambda: view.control.closing)
    with pytest.raises(StartupFailure):
        startup.ready.result()
    assert startup.wait(3) and startup.phase == "config"
    assert not local_setup.journals and not local_setup.loops


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_environment_desktop_credentials_without_config_file(local_setup, monkeypatch, protocol):
    monkeypatch.delenv("SICO_PROVIDER_CONFIG", raising=False)
    monkeypatch.delenv("SICO_API_KEY", raising=False)
    monkeypatch.setenv("SICO_BACKEND", "python")
    monkeypatch.setenv("SICO_PROVIDER", protocol)
    monkeypatch.setenv("SICO_API_URL", "https://gateway.example/v1")
    monkeypatch.setenv("SICO_MODEL", "backend-model")
    calls = []

    def enter(*args, **kwargs):
        calls.append((args, kwargs))
        return " in-memory-secret ", True

    monkeypatch.setattr("sico_ui.credentials.ask_text", enter)
    def provider_from_startup():
        startup, view = local_setup.create(environment=dict(os.environ))
        wait_until(lambda: view.window is not None or view.control.closing)
        try:
            return startup_backend(startup).controller.loop.provider
        finally:
            view.close()
            assert startup.wait(3)

    provider = provider_from_startup()
    assert provider.api_key == "in-memory-secret" and provider.model == "backend-model"
    assert calls[0][1]["password"] is True
    assert "SICO_API_KEY" not in os.environ
    for journal in local_setup.journals:
        assert "in-memory-secret" not in (journal.directory / "events.jsonl").read_text()
    monkeypatch.setenv("SICO_API_KEY", "inherited-secret")
    assert provider_from_startup().api_key == "inherited-secret"
    assert len(calls) == 1
    monkeypatch.delenv("SICO_MODEL")
    monkeypatch.setattr("sico_ui.startup.notice", lambda *args, **kwargs: None)
    with pytest.raises(StartupFailure):
        provider_from_startup()
    assert len(calls) == 1


@pytest.mark.parametrize("shutdown", ["eof", "command"])
@pytest.mark.parametrize("dialog", ["credential", "config_error"])
def test_owner_shutdown_closes_modal_startup_dialog(config, shutdown, dialog):
    app = QApplication.instance() or QApplication([])
    reader = SimpleNamespace(
        closed=threading.Event(), lines=queue.Queue(), close=lambda **kwargs: None,
    )
    control = DesktopControl(reader)
    seen = []

    def disconnect():
        seen.append(type(app.activeModalWidget()).__name__)
        if shutdown == "eof":
            reader.closed.set()
        else:
            reader.lines.put({"kind": "shutdown"})

    escape = QTimer()
    escape.setSingleShot(True)
    escape.timeout.connect(
        lambda: app.activeModalWidget().close() if app.activeModalWidget() else None
    )
    escape.start(3000)
    QTimer.singleShot(25, disconnect)
    try:
        if dialog == "credential":
            with pytest.raises(CredentialCancelled):
                credential_environment("SICO_TEST_KEY")
            assert seen == ["SiPrompt"]
        else:
            notice(None, "Configuration", "Invalid configuration")
            assert seen == ["SiConfirm"]
        assert control.closing
    finally:
        escape.stop()
        control.close()
