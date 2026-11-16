"""Session naming against actual Codex 0.154 and an isolated Responses gateway."""

from __future__ import annotations

import time

import pytest
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
)

from sico.codex.backend import CodexBackend
from sico.service.controller import SessionController
from sico.storage.history import session_catalog
from sico.storage.journal import SessionJournal
from sico.storage.names import session_names


def read_thread(backend):
    return backend.runtime.rpc.request("thread/read", {
        "threadId": backend.thread_id, "includeTurns": False,
    })["thread"]


def test_first_conversation_auto_names_and_manual_name_survives_more_turns(
    tmp_path,
    native_codex,  # noqa: F811
    gateway,  # noqa: F811
):
    provider = settings(gateway, native_codex, "native")
    gateway.responses.put(answer("Ready for the design review."))
    gateway.responses.put(answer("Reviewed the requested output."))
    with SessionJournal(tmp_path, "auto-name") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        controller = SessionController(backend)
        label = controller.label
        try:
            startup = list(backend.initialize_context(
                lambda: False, context=CONTEXT, input_id="init",
            ))
            assert startup[-1]["kind"] == "task.completed"
            assert not gateway.requests
            assert controller.display_name == "auto-name"
            assert read_thread(backend)["name"] is None

            text = "Review LDO_A7 output ripple"
            events = list(backend.run(text, input_id="first"))
            assert events[-1]["kind"] == "task.completed"
            assert len(gateway.requests) == 1  # No separate model title-generation request.
            assert controller.display_name == text
            thread = read_thread(backend)
            assert thread["preview"] == text and thread["name"] == text
            row = session_catalog(journal.root)[0]
            assert row["name"] == text and row["title"] == text
            assert row["name_source"] == "codex.preview"

            controller.rename("A7 ripple review")
            events = list(backend.run("Now check its supply rejection", input_id="second"))
            assert events[-1]["kind"] == "task.completed"
            assert len(gateway.requests) == 2
            assert controller.label == label
            assert controller.display_name == "A7 ripple review"
            assert read_thread(backend)["name"] == "A7 ripple review"
            names = [e["payload"] for e in journal.events() if e["kind"] == "codex.thread.name"]
            assert [e["name"] for e in names] == [text, "A7 ripple review"]
            assert names[-1]["source"] == "user"
        finally:
            backend.close()


@pytest.mark.parametrize("native_name", [None, "External review name"])
def test_resume_collects_legacy_native_name_and_preview_without_model_turn(
    tmp_path,
    native_codex,  # noqa: F811
    gateway,  # noqa: F811
    monkeypatch,
    native_name,
):
    provider = settings(gateway, native_codex, "native")
    gateway.responses.put(answer("Legacy conversation recorded."))
    text = "Review amplifier B9 noise"
    with SessionJournal(tmp_path, "legacy") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            # Reproduce the old client's completed turn without metadata collection.
            monkeypatch.setattr(backend.names, "after_turn", lambda rpc: None)
            assert list(backend.run(text))[-1]["kind"] == "task.completed"
            if native_name:
                backend.runtime.rpc.notification_handler = None
                backend.runtime.rpc.request("thread/name/set", {
                    "threadId": backend.thread_id, "name": native_name,
                })
            assert session_names(journal.events())["name"] == ""
            assert session_names(journal.events())["title"] == ""
            thread_id = backend.thread_id
        finally:
            backend.close()

    with SessionJournal(tmp_path, "legacy") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            backend.resume_thread()
            controller = SessionController(backend)
            assert backend.thread_id == thread_id
            assert controller.display_name == (native_name or text)
            assert len(gateway.requests) == 1
            row = session_catalog(journal.root)[0]
            assert row["name"] == (native_name or "")
            assert row["title"] == text
        finally:
            backend.close()


def test_idle_native_name_notification_is_collected_without_new_turn(
    tmp_path,
    native_codex,  # noqa: F811
    gateway,  # noqa: F811
):
    provider = settings(gateway, native_codex, "native")
    with SessionJournal(tmp_path, "idle") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        controller = SessionController(backend)
        try:
            list(backend.initialize_context(lambda: False, context=CONTEXT, input_id="init"))
            rpc = backend.runtime.rpc
            rpc.notification_handler = None
            rpc.request("thread/name/set", {
                "threadId": backend.thread_id, "name": "Idle native update",
            })
            assert controller.display_name == "idle"
            rpc.notification_handler = backend.names.notification
            deadline = time.monotonic() + 3
            while controller.display_name != "Idle native update" and time.monotonic() < deadline:
                controller.poll_metadata()
                controller.updates.read_updates()
                time.sleep(0.01)
            assert controller.display_name == "Idle native update"
            assert not gateway.requests
            assert session_catalog(journal.root)[0]["name"] == "Idle native update"
            events = [e for e in journal.events() if e["kind"] == "codex.thread.name"]
            assert len(events) == 1
        finally:
            backend.close()
