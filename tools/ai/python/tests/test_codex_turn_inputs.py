"""Input snapshots and queued controls preserve identity and never authorize resources."""

import base64
import io
import os
import struct
import wave
import zlib
from types import SimpleNamespace

import pytest
from test_agent_codex_native import CONTEXT

from sico.codex.backend import CodexBackend
from sico.codex.access_policy import default_access
from sico.codex.turn_options import parse_output_schema
from sico.core.tools import ToolRegistry
from sico.service.controller import SessionController
from sico.storage.input_assets import prepare_inputs, read_asset, wire_inputs
from sico.storage.journal import SessionJournal


def png():
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\0\xff\0" * 2)) + chunk(b"IEND", b""))


PNG = png()


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\0\0" * 160)
    return output.getvalue()


def catalog(model="test-model"):
    return {"models": [{"model": model, "displayName": model,
                        "inputModalities": ["text", "image", "audio"],
                        "supportedReasoningEfforts": [{"reasoningEffort": "novel"}],
                        "defaultReasoningEffort": "novel",
                        "serviceTiers": [{"id": "fast", "name": "Fast"}]}],
            "modes": ["default", "plan"], "skills": []}


def backend(journal):
    provider = SimpleNamespace(model="test-model", label="Provider label", options={},
                               environment={})
    loop = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
    loop.turn_options.catalog = catalog()
    return loop


@pytest.mark.parametrize("kind,data,suffix", [("localImage", PNG, ".png"),
                                             ("localAudio", wav(), ".wav")])
def test_frozen_media_survives_original_file_change(tmp_path, kind, data, suffix):
    source = tmp_path / ("selected" + suffix)
    source.write_bytes(data)
    with SessionJournal(tmp_path, "media") as journal:
        inputs = prepare_inputs(journal, [{"type": kind, "path": str(source)}])
        source.write_bytes(b"changed")
        wire = wire_inputs(journal, inputs)
        assert read_asset(journal, inputs[0]["asset"]) == data
        assert wire[0]["path"] != str(source)
        assert os.stat(wire[0]["path"]).st_mode & 0o777 == 0o600
        assert str(journal.attachments) in wire[0]["path"]


@pytest.mark.parametrize("kind", ["mention", "skill"])
def test_reference_changes_fail_before_dispatch(tmp_path, kind):
    path = tmp_path / "SKILL.md"
    path.write_text("approved content")
    part = {"type": kind, "name": "selected", "path": str(path)}
    skills = [{"name": "selected", "path": str(path), "enabled": True}]
    with SessionJournal(tmp_path, "refs") as journal:
        rows = prepare_inputs(journal, [part], skills)
        assert wire_inputs(journal, rows, skills) == [part]
        path.write_text("new content")
        with pytest.raises(ValueError, match="changed"):
            wire_inputs(journal, rows, skills)
        assert read_asset(journal, rows[0]["asset"]) == b"approved content"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:pass@site/x",
                                  "javascript:alert(1)", "https://site/\nsecret", "", None])
def test_invalid_urls_are_rejected(tmp_path, url):
    with SessionJournal(tmp_path, "urls") as journal:
        with pytest.raises(ValueError):
            prepare_inputs(journal, [{"type": "image", "url": url}])


@pytest.mark.parametrize("bad", ["empty", "format", "directory", "symlink", "fifo", "large"])
def test_invalid_files_rejected_without_blocking(tmp_path, bad):
    path = tmp_path / "input"
    if bad == "directory":
        path.mkdir()
    elif bad == "symlink":
        source = tmp_path / "real.png"
        source.write_bytes(PNG)
        path.symlink_to(source)
    elif bad == "fifo":
        os.mkfifo(path)
    elif bad == "large":
        with path.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
    else:
        path.write_bytes(b"" if bad == "empty" else b"not an image")
    with SessionJournal(tmp_path, "invalid") as journal:
        with pytest.raises((OSError, ValueError)):
            prepare_inputs(journal, [{"type": "localImage", "path": str(path)}])


def test_skill_requires_enabled_session_inventory(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("skill content")
    part = {"type": "skill", "name": "s", "path": str(path)}
    with SessionJournal(tmp_path, "skill") as journal:
        for skills in ([], [{"name": "s", "path": str(path), "enabled": False}]):
            with pytest.raises(ValueError, match="inventory"):
                prepare_inputs(journal, [part], skills)
        rows = prepare_inputs(journal, [part], [{**part, "enabled": True}])
        with pytest.raises(ValueError, match="inventory"):
            wire_inputs(journal, rows, [])


@pytest.mark.parametrize("change", ["digest", "foreign_session", "bytes", "size", "symlink"])
def test_archive_identity_integrity(tmp_path, change):
    path = tmp_path / "a.png"
    path.write_bytes(PNG)
    with SessionJournal(tmp_path, "archive") as journal:
        row = prepare_inputs(journal, [{"type": "localImage", "path": str(path)}])[0]
        asset = dict(row["asset"])
        saved = journal.root / asset["path"]
        if change == "digest":
            asset["sha256"] = "0" * 64
        elif change == "foreign_session":
            asset["path"] = asset["path"].replace("/archive/", "/other/")
        elif change == "size":
            asset["size"] += 1
        elif change == "bytes":
            saved.write_bytes(b"tampered")
        else:
            saved.unlink()
            saved.symlink_to(path)
        with pytest.raises((OSError, ValueError)):
            read_asset(journal, asset)


@pytest.mark.parametrize("options", [
    {"approvalPolicy": "never"}, {"model": "unadvertised"}, {"effort": "xhigh"},
    {"mode": "proactive"}, {"serviceTier": "unknown"}, {"outputSchema": []},
    {"outputSchema": {"x": "a" * 32769}}, {"cwd": "/tmp"}, {"permissions": "all"},
    {"mode": []},
])
def test_settings_do_not_expand_policy_or_guess_capabilities(tmp_path, options):
    with SessionJournal(tmp_path, "settings") as journal:
        loop = backend(journal)
        with pytest.raises(ValueError):
            loop.turn_options.freeze(options)


def test_queue_freezes_controls_and_recovery_never_replays(tmp_path):
    with SessionJournal(tmp_path, "queue") as journal:
        loop = backend(journal)
        controller = SessionController(loop)
        controller.paused = True
        selected = {"model": "test-model", "effort": "novel", "mode": "plan",
                    "serviceTier": "fast", "serviceTierForTurn": "default",
                    "outputSchema": {"type": "object"}}
        first = controller.submit("first task", turn_options=selected)
        selected["mode"] = "default"
        controller.submit("second task", turn_options=selected)
        first_row, second_row = controller.inbox.pending
        assert first_row["message"]["turn_options"]["mode"] == "plan"
        assert second_row["message"]["turn_options"]["mode"] == "default"
        assert controller.history.rows[first]["turn_options"]["effort"] == "novel"
        params = loop.turn_options.params(first_row["message"]["turn_options"])
        assert params["collaborationMode"]["settings"]["reasoning_effort"] == "novel"
        assert params["collaborationMode"]["settings"]["developer_instructions"] is None
        assert controller.label == "Provider label" and controller.display_name == "queue"
    with SessionJournal(tmp_path, "queue") as journal:
        controller = SessionController(backend(journal))
        assert not controller.inbox.pending
        assert len(controller.history.rows) == 2
        assert all(r["status"] == "not_started" for r in controller.history.rows.values())


def test_only_accepted_settings_become_sticky(tmp_path):
    with SessionJournal(tmp_path, "sticky") as journal:
        loop = backend(journal)
        selected = loop.turn_options.freeze({"mode": "plan", "effort": "novel",
                                             "outputSchema": False,
                                             "serviceTierForTurn": "fast"})
        assert loop.turn_options.selected["mode"] == "default"
        loop._event("codex.turn.settings", {"options": selected, "status": "accepted"})
        loop.turn_options.accepted(selected)
        assert loop.turn_options.freeze() == {"model": "test-model", "mode": "plan",
                                              "effort": "novel", "serviceTier": "default",
                                              "access": default_access()}
    with SessionJournal(tmp_path, "sticky") as journal:
        assert backend(journal).turn_options.selected["mode"] == "plan"


@pytest.mark.parametrize("origin", ["chat", "quick"])
def test_default_queue_snapshot_does_not_inherit_an_earlier_queued_change(tmp_path, origin):
    with SessionJournal(tmp_path, "defaults") as journal:
        loop = backend(journal)
        controller = SessionController(loop)
        controller.paused = True
        controller.submit("custom task", turn_options={"mode": "plan", "serviceTier": "fast"})
        message = {"kind": "submit", "id": CONTEXT.target_id, "text": "default task",
                   "context": CONTEXT.record()}
        if origin == "quick":
            assert controller.accept(message)
        else:
            controller.submit("default task")
        first_row, second_row = controller.inbox.pending
        first = first_row["message"]["turn_options"]
        second = second_row.get("turn_options", second_row["message"].get("turn_options"))
        loop.turn_options.accepted(first)
        assert second == {"model": "test-model", "mode": "default", "serviceTier": "default",
                          "access": default_access()}
        assert loop.turn_options.params(second)["collaborationMode"]["mode"] == "default"
        assert not loop.turn_options.needs_catalog(second)
        if origin == "quick":
            assert not controller.accept(message)
            assert len(controller.inbox.pending) == 2
            assert controller.history.rows[CONTEXT.target_id]["turn_options"] == second
            seen = []

            def run(_text, _cancelled, **kwargs):
                seen.append(kwargs["turn_options"])
                loop.state.task["status"] = "completed"
                return iter(())

            loop.run = run
            controller.paused = False
            controller._run()
            assert seen == [first, second]


@pytest.mark.parametrize("extra", ["inputs", "turn_options", "attachments"])
def test_quick_input_cannot_inject_unprepared_extras(tmp_path, extra):
    with SessionJournal(tmp_path, "quick-boundary") as journal:
        controller = SessionController(backend(journal))
        message = {"kind": "submit", "id": CONTEXT.target_id, "text": "default task",
                   "context": CONTEXT.record(), extra: []}
        with pytest.raises(ValueError, match="envelope"):
            controller.accept(message)
        assert not controller.inbox.pending


@pytest.mark.parametrize("text", ['false,"extra":1', '{"x":1,"x":2}', '{"x":NaN}',
                                  '{"x":1e999}', 'true false', '[]', '"schema"'])
def test_schema_rejects_non_single_and_non_finite_json(text):
    with pytest.raises(ValueError):
        parse_output_schema(text)


@pytest.mark.parametrize("text,value", [("false", False), ("true", True), ("{}", {}), ("", None)])
def test_schema_boolean_and_empty_are_distinct(text, value):
    assert parse_output_schema(text) == value


@pytest.mark.parametrize("part", [None, "image", 1, [], {"type": "localAudio"}, {"type": []}])
def test_invalid_or_unsupported_input_has_actionable_error(tmp_path, part):
    with SessionJournal(tmp_path, "invalid-record") as journal:
        loop = backend(journal)
        loop.turn_options.catalog["models"][0]["inputModalities"] = ["text", "image"]
        with pytest.raises(ValueError):
            SessionController(loop).submit("Read input", inputs=[part])


def test_inline_media_is_archived_and_bounded(tmp_path, monkeypatch):
    from sico.storage import input_assets

    part = {"type": "image", "url": "data:image/png;base64," + base64.b64encode(PNG).decode()}
    with SessionJournal(tmp_path, "inline") as journal:
        rows = prepare_inputs(journal, [part])
        assert "url" not in rows[0] and wire_inputs(journal, rows) == [part]
        monkeypatch.setattr(input_assets, "MAX_INLINE", len(PNG))
        with pytest.raises(ValueError, match="combined RPC"):
            prepare_inputs(journal, [part, part])
        with pytest.raises(ValueError, match="combined RPC"):
            wire_inputs(journal, rows * 2)


@pytest.mark.parametrize("boundary", ["cancel", "close", "target"])
def test_slow_input_read_cannot_enqueue_after_invalidation(tmp_path, monkeypatch, boundary):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sico.storage import input_assets

    entered, release = Event(), Event()
    original = input_assets.file_bytes
    path = tmp_path / "photo.png"
    path.write_bytes(PNG)

    def slow(path):
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr(input_assets, "file_bytes", slow)
    with SessionJournal(tmp_path, "read-boundary") as journal, ThreadPoolExecutor(1) as pool:
        controller = SessionController(backend(journal))
        controller.paused = True
        future = pool.submit(controller.submit, "Image task",
                             inputs=[{"type": "localImage", "path": str(path)}])
        try:
            assert entered.wait(5)
            if boundary == "close":
                controller.request_close()
            elif boundary == "cancel":
                controller.interrupt()
            else:
                controller.bindings.events.state = {"invalidated": {CONTEXT.target_id: True}}
        finally:
            release.set()
        with pytest.raises(ValueError):
            future.result(5)
        assert not controller.inbox.pending and controller.loop.runtime is None


@pytest.mark.parametrize("fault", ["cursor", "oversized", "timeout", "shape"])
def test_failed_inventory_never_publishes_partial_catalog(tmp_path, monkeypatch, fault):
    with SessionJournal(tmp_path, "inventory") as journal:
        loop = backend(journal)
        previous = loop.turn_options.status(busy=True)
        calls = []

        def request(method, params, **kwargs):
            calls.append(method)
            assert 0 < kwargs["timeout"] <= 3
            if fault == "timeout":
                raise TimeoutError("Timed out")
            if fault == "cursor":
                return {"data": [], "nextCursor": "same"}
            if fault == "oversized":
                return {"data": [{}] * 101}
            return {"data": "invalid"}

        monkeypatch.setattr(loop, "_ensure_runtime", lambda: None)
        loop.runtime = SimpleNamespace(rpc=SimpleNamespace(request=request))
        with pytest.raises((ValueError, TimeoutError)):
            loop.turn_options.status()
        assert loop.turn_options.status(busy=True) == previous
        assert len(calls) == (2 if fault == "cursor" else 1)
