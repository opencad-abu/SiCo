"""Real Codex turns reuse one resident LSF worker across session requests."""

from sico.codex import lsf_gateway
from sico.codex.backend import CodexBackend
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
)
from test_agent_codex_native import (
    settings as provider_settings,
)
from test_lsf_gateway import PipeJob


def test_real_codex_turns_reuse_one_resident_worker(
        tmp_path, gateway, monkeypatch, native_codex):  # noqa: F811
    PipeJob.instances.clear()
    gateway.responses.put(answer("OK"))
    gateway.responses.put(answer("OK again"))
    provider = provider_settings(gateway, native_codex, "switch")
    provider.environment["SICO_CODEX_LSF_EXE"] = "/bin/true"
    original = lsf_gateway.LsfGateway
    monkeypatch.setattr(
        lsf_gateway, "LsfGateway",
        lambda *args, **kwargs: original(*args, job_factory=PipeJob, **kwargs),
    )
    with SessionJournal(tmp_path, "gateway-success") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        try:
            events = list(backend.run("Reply OK.", input_id="ok"))
            assert events[-1]["kind"] == "task.completed"
            events = list(backend.run("Reply again.", input_id="ok2"))
            assert events[-1]["kind"] == "task.completed"
            assert len(PipeJob.instances) == 1
            assert len(gateway.requests) == 2
        finally:
            backend.close()
    assert PipeJob.instances[0].closed
