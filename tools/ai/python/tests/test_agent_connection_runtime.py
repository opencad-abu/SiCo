"""Context-only connection setup shared by CLI and GUI."""

import json
import stat
from types import SimpleNamespace

import pytest
from agent_startup_fixture import startup_backend

from sico import cli, interpreter
from sico import installation as app_installation
from sicopaths import IDENTITY_BYTES
from sico.service import connection


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("CAD_AI_CONTEXT", raising=False)
    monkeypatch.delenv("SICO_AI_CONTEXT", raising=False)
    for name in ("SICO_HOME", "CAD_HOME"):
        monkeypatch.delenv(name, raising=False)
    for name in ("bin", "tools/common", "tools/ai", "etc/config"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/config/sico-install.json").write_bytes(IDENTITY_BYTES)
    monkeypatch.setattr(app_installation, "__file__",
                        str(tmp_path / "tools/sico/python/sico/installation.py"))
    monkeypatch.setattr(connection, "agent_command", lambda *args: ["/runtime/bin/sico", *args])
    journal = SimpleNamespace(directory=tmp_path / "session with spaces")
    journal.directory.mkdir()
    broker = SimpleNamespace(address=("127.0.0.1", 12345), token="private-token")
    return journal, broker


def test_preloaded_context_requires_callable_and_private_descriptor(setup):
    journal, broker = setup
    text = connection.connection_instructions(journal, broker, "instance", "generation")
    assert "load(" not in text and ".il" not in text
    assert "loadContext(" not in text
    assert "isCallable('sicoConnect)" in text
    assert text.index("isCallable") < text.index("sicoConnect(\"")
    assert broker.token not in text
    descriptor = journal.directory / "connection.json"
    assert stat.S_IMODE(descriptor.stat().st_mode) == 0o600
    assert json.loads(descriptor.read_text()) == {
        "host": "127.0.0.1", "port": 12345, "token": broker.token,
    }


@pytest.mark.parametrize("mode", ["argument", "environment", "installed"])
def test_declared_context_loads_and_initializes_before_relay(setup, tmp_path, monkeypatch, mode):
    journal, broker = setup
    context = tmp_path / "tools/ai/context/cadAiRunCtx.cxt"
    context.parent.mkdir()
    context.write_bytes(b"private test context")
    argument = None
    if mode == "argument":
        argument = context
        monkeypatch.setenv("SICO_AI_CONTEXT", str(tmp_path / "missing.cxt"))
    elif mode == "environment":
        monkeypatch.setenv("SICO_AI_CONTEXT", str(context))
    text = connection.connection_instructions(
        journal, broker, "instance", "generation", context_file=argument,
    )
    assert f"loadContext({json.dumps(str(context))} t)" in text
    assert 'callInitProc("cadAiRunCtx")' in text
    assert text.index("progn(") < text.index("loadContext") < text.index("callInitProc")
    assert text.index("callInitProc") < text.index("sicoConnect(\"")
    assert "load(" not in text


@pytest.mark.parametrize("filename", ["missing.cxt", "source.il", "directory.cxt"])
def test_invalid_declared_context_fails_before_descriptor_write(setup, tmp_path, filename):
    journal, broker = setup
    path = tmp_path / filename
    if filename == "source.il":
        path.write_text("source must not be loaded")
    elif filename == "directory.cxt":
        path.mkdir()
    with pytest.raises(ValueError, match="available .cxt"):
        connection.connection_instructions(journal, broker, "i", "g", context_file=path)
    assert not list(journal.directory.iterdir())


def test_native_relay_never_falls_back_to_source_or_python_m(tmp_path, monkeypatch):
    monkeypatch.setattr(interpreter, "__file__", str(tmp_path / "python/sico/interpreter.so"))
    monkeypatch.setattr(interpreter, "native_entry", lambda name: None)
    with pytest.raises(ValueError, match="ELF sico"):
        interpreter.agent_command("relay")


def test_connect_option_cannot_be_silently_ignored():
    with pytest.raises(SystemExit) as caught:
        cli.main(["demo", "--context-file", "wrong.cxt"])
    assert caught.value.code == 2


def test_cli_registers_session_before_backend_creation(tmp_path, monkeypatch):
    from sico.providers.mock import ContextDemoProvider

    original = cli.create_backend
    observed = []
    register = cli.ContextBroker.register_session

    def track(broker, session, context):
        result = register(broker, session, context)
        observed.append((session, context))
        return result

    def create(provider, tools, journal, context):
        assert observed == [("cli-bound", context)]
        return original(provider, tools, journal, context)

    monkeypatch.setattr(cli.ContextBroker, "register_session", track)
    monkeypatch.setattr(cli, "create_backend", create)
    monkeypatch.setattr(cli, "load_provider", lambda *a, **kw: ContextDemoProvider())
    assert cli.main(["demo", "--launch-dir", str(tmp_path), "--session", "cli-bound"]) == 0


def test_local_startup_registers_target_before_first_context_task(tmp_path):
    from agent_local_harness import LocalStartup

    args = SimpleNamespace(command="demo", launch_dir=tmp_path, session="binding",
                           provider_config=None, timeout=5, message="", abandon_interrupted=False)
    startup = LocalStartup(args, lambda *_a, **_kw: None, environment={})
    try:
        startup.ready.result(5)
        controller = startup_backend(startup).controller
        assert startup_backend(startup).broker.binding_snapshot("binding")["targets"]
        assert controller.initialize_context()
        assert controller.wait(5)
        events = controller.journal.events()
        assert any(event["kind"] == "task.completed" for event in events)
        assert not any(event["kind"] == "task.failed" for event in events)
    finally:
        startup.close()
        assert startup.wait(5)
