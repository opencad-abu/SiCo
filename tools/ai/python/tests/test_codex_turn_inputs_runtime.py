"""Original 0.154 public input and settings round trips through a local gateway."""

import base64
import json

import pytest
from sico.codex.backend import CodexBackend
from sico.storage.input_assets import prepare_inputs
from sico.storage.journal import SessionJournal
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
)
from test_codex_turn_inputs import PNG, wav

# Codex upgrade guard: a new request field or reasoning/text control must be
# reviewed for gateway compatibility and registered in responses_compat.py
# before this allowlist is expanded.
REQUEST_FIELDS = {
    "client_metadata", "include", "input", "instructions", "model",
    "parallel_tool_calls", "prompt_cache_key", "reasoning", "service_tier",
    "store", "stream", "text", "tool_choice", "tools",
}


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_original_multimodal_settings_round_trip(
        tmp_path, gateway, native_codex, tool_format):  # noqa: F811
    provider = settings(gateway, native_codex, tool_format)
    path, audio, document = tmp_path / "input.png", tmp_path / "input.wav", tmp_path / "notes.txt"
    path.write_bytes(PNG)
    audio.write_bytes(wav())
    document.write_text("User-supplied project notes for controlled testing.")
    gateway.responses.put(answer('{"result":"received"}'))
    with SessionJournal(tmp_path, "native-input") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            status = backend.turn_options.status()
            calls = []
            original = backend.runtime.rpc.request
            original_next = backend.runtime.rpc._next

            def received(timeout):
                message = original_next(timeout)
                if "error" in message:
                    calls.append({"error": message["error"]})
                return message

            backend.runtime.rpc._next = received

            def request(method, params, **kwargs):
                calls.append({"method": method, "params": params})
                return original(method, params, **kwargs)

            backend.runtime.rpc.request = request
            model = next(m for m in status["models"]
                         if m["supportedReasoningEfforts"])
            options = backend.turn_options.freeze({
                "model": model["model"], "mode": "plan",
                "effort": model["supportedReasoningEfforts"][0]["reasoningEffort"],
                "serviceTier": "priority", "serviceTierForTurn": "default",
                "outputSchema": {"type": "object", "properties": {"result": {"type": "string"}},
                                 "required": ["result"], "additionalProperties": False},
            })
            skill = next(s for s in status["skills"] if s["enabled"])
            parts = [
                {"type": "localImage", "path": str(path), "detail": "high"},
                {"type": "image", "url": "data:image/png;base64," +
                 base64.b64encode(PNG).decode(), "detail": "original"},
                {"type": "mention", "name": "notes", "path": str(document)},
                {"type": "skill", "name": skill["name"], "path": skill["path"]},
            ]
            inputs = prepare_inputs(journal, parts, status["skills"])
            events = list(backend.run("Inspect the supplied inputs.", inputs=inputs,
                                      turn_options=options, input_id="with-inputs"))
            proof = {"catalog": status, "rpc": calls, "gateway": gateway.requests,
                     "events": events}
            (tmp_path / "turn-input-proof.json").write_text(json.dumps(proof, indent=2))
            assert events[-1]["kind"] == "task.completed", calls[-3:]
            request_body = gateway.requests[-1]
            assert set(request_body) <= REQUEST_FIELDS
            assert set(request_body.get("reasoning", {})) <= {"effort"}
            assert set(request_body.get("text") or {}) <= {"format"}
            assert set(request_body.get("include", [])) <= {"reasoning.encrypted_content"}
            assert request_body["model"] == model["model"]
            assert request_body["reasoning"]["effort"] == options["effort"]
            assert "summary" not in request_body["reasoning"]
            assert "verbosity" not in request_body["text"]
            assert request_body.get("service_tier") != "priority"
            assert request_body["text"]["format"]["schema"] == options["outputSchema"]
            content = [part for item in request_body["input"] for part in item.get("content", [])
                       if isinstance(part, dict)]
            assert len([p for p in content if p["type"] == "input_image"]) == 2
            assert any(str(document) in p.get("text", "") for p in content)
            assert any("<skill>" in p.get("text", "") for p in content)
            starts = [c for c in calls if c.get("method") == "turn/start"]
            assert len(starts) == 1
            assert [r["type"] for r in starts[0]["params"]["input"]] == [
                "text", *[p["type"] for p in parts]]
            assert backend.provider.model == "cad-copilot-test"
            assert not any(c["method"].startswith("account/") for c in calls)
            assert not (journal.root / "codex" / "auth.json").exists()
            native = backend.runtime.rpc.request("thread/read", {
                "threadId": backend.thread_id, "includeTurns": True})
            assert "localImage" in json.dumps(native) or "image" in json.dumps(native)
            proof["history"] = native
            gateway.responses.put(answer("ordinary second response"))
            second = list(backend.run("Continue with the same settings."))
            assert second[-1]["kind"] == "task.completed"
            second_start = [c for c in calls if c.get("method") == "turn/start"][-1]["params"]
            assert "serviceTierForTurn" not in second_start and "outputSchema" not in second_start
            assert second_start["collaborationMode"]["mode"] == "plan"
            assert gateway.requests[-1]["service_tier"] == "priority"
            backend.close()
        finally:
            backend.close()
    with SessionJournal(tmp_path, "native-input") as journal:
        restored = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            before = len(gateway.requests)
            restored.resume_thread()
            assert len(gateway.requests) == before
            assert restored.turn_options.selected["model"] == model["model"]
            restored.turn_options.status()
            gateway.responses.put(answer("explicit default response"))
            defaults = restored.turn_options.freeze({"model": model["model"], "mode": "default"})
            events = list(restored.run("Use the selected defaults.", turn_options=defaults))
            assert events[-1]["kind"] == "task.completed"
            assert gateway.requests[-1]["model"] == model["model"]
            assert gateway.requests[-1]["reasoning"]["effort"] == model["defaultReasoningEffort"]
            assert "summary" not in gateway.requests[-1]["reasoning"]
            assert "verbosity" not in (gateway.requests[-1].get("text") or {})
            proof["restored"] = events
        finally:
            restored.close()
    (tmp_path / "turn-input-proof.json").write_text(json.dumps(proof, indent=2))


def test_original_runtime_rejects_remote_image_urls(
        tmp_path, gateway, native_codex):  # noqa: F811
    from sico.codex.rpc import RpcRejected

    with SessionJournal(tmp_path, "remote-input") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]),
                               journal, CONTEXT)
        try:
            backend._ensure_runtime()
            with pytest.raises(RpcRejected):
                backend.runtime.rpc.request("turn/start", {
                    "threadId": backend.thread_id, "input": [
                        {"type": "image", "url": "https://example.invalid/user-media"}]})
            assert not gateway.requests
        finally:
            backend.close()


def test_audio_requires_actual_advertised_support(tmp_path, gateway, native_codex):  # noqa: F811
    from sico.service.controller import SessionController

    with SessionJournal(tmp_path, "audio-input") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]),
                               journal, CONTEXT)
        try:
            catalog = backend.turn_options.status()
            assert all("audio" not in row["inputModalities"] for row in catalog["models"])
            controller = SessionController(backend)
            controller.paused = True
            with pytest.raises(ValueError, match="advertise audio"):
                controller.submit("Listen", inputs=[{"type": "localAudio", "path": "/unused"}])
            assert not controller.inbox.pending and not gateway.requests
        finally:
            backend.close()
