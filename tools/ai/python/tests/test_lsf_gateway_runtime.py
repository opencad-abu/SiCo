"""Native and flat Codex modes both route their actual HTTP access into LSF."""

from types import SimpleNamespace

import pytest
from sico.codex import lsf_gateway, runtime
from sico.codex.settings import CodexSettings
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize("flat", [True, False])
def test_runtime_composes_local_provider_and_closes_in_order(tmp_path, monkeypatch, flat):
    closed = []
    captured = {}

    class Gateway:
        def __init__(self, settings, cwd, evidence, *, auxiliary=False):
            assert settings.endpoint == "https://upstream.example/v1/responses"
            assert settings.api_key == "upstream-private"
            assert auxiliary is False
            self.base_url = "http://127.0.0.1:32101/v1"
            self.token = "relay-private"

        def close(self):
            closed.append("gateway")

    class Wrapper:
        def __init__(self, endpoint, key, **kwargs):
            assert endpoint == "http://127.0.0.1:32101/v1/responses"
            assert key == "relay-private"
            self.base_url = "http://127.0.0.1:32102/v1"
            self.token = "wrapper-private"

        def close(self):
            closed.append("wrapper")

    class App:
        def __init__(self, command, environment, cwd, *, cancelled=None, diagnostic=None):
            assert cancelled is None
            assert diagnostic is not None
            captured.update(command=command, environment=environment)
            self.child = SimpleNamespace(returncode=0)

        def close(self):
            closed.append("app")

    monkeypatch.setattr(lsf_gateway, "LsfGateway", Gateway)
    monkeypatch.setattr(runtime, "ResponsesWrapper", Wrapper)
    monkeypatch.setattr(runtime, "AppServer", App)
    monkeypatch.setattr(runtime.process_monitor, "track", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime.process_monitor, "finish", lambda *_a, **_k: None)
    settings = CodexSettings(
        dict(provider="responses", base_url="https://upstream.example/v1", model="doubao"),
        dict(
            SICO_API_KEY="upstream-private",
            SICO_CODEX_LSF_EXE="bsub",
            SICO_LEGACY_0114="1" if flat else "0",
        ),
    )
    monkeypatch.setattr(settings, "executable", lambda: "/bin/true")
    with SessionJournal(tmp_path, "routing") as journal:
        process = runtime.CodexRuntime(
            settings, journal, SimpleNamespace(port=12, token="mcp", timeout=3)
        )
        process.close()
    assert captured["environment"]["SICO_MODEL_KEY"] == (
        "wrapper-private" if flat else "relay-private"
    )
    assert "upstream-private" not in str(captured)
    assert closed == (["app", "wrapper", "gateway"] if flat else ["app", "gateway"])
