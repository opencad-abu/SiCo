"""M0 terminal command identity, output and recovery contract tests."""

import base64
from dataclasses import replace

import pytest

from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.core.terminal_output import MAX_CHUNK_BYTES, OutputChunk
from sico.core.terminal_state import apply_event, event, recovered


def request(*, mode="pipes", text="printf hi", shell="/bin/sh", actor="model"):
    return CommandRequest(
        CommandRef("session", "terminal", "command"),
        "request",
        "/workspace/project",
        "environment-v1",
        actor=actor,
        mode=mode,
        shell=shell if text is not None else None,
        text=text,
        argv=(),
        task_id="task",
    )


def accepted(value):
    return apply_event(None, event("accepted", value.ref, request=value.record()))


def test_request_round_trip_keeps_the_execution_identity():
    value = request()
    restored = CommandRequest.from_record(value.record())

    assert restored == value
    assert restored.ref.record() == {
        "session_id": "session", "terminal_id": "terminal", "command_id": "command",
    }


def test_argv_request_is_distinct_from_shell_text():
    value = CommandRequest(
        CommandRef("session", "terminal", "argv-command"), "request-argv",
        "/workspace/project", "environment-v1", argv=("/bin/printf", "hello"),
    )

    assert CommandRequest.from_record(value.record()) == value
    with pytest.raises(ValueError, match="Choose shell text"):
        CommandRequest(
            value.ref, "request-mixed", "/workspace/project", "environment-v1",
            shell="/bin/sh", text="echo hi", argv=("echo",),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"cwd": "relative"},
        {"text": "bad\0text"},
        {"shell": "/bin/sh\0"},
        {"actor": "unknown"},
    ],
)
def test_request_rejects_unsafe_or_unbound_values(changes):
    values = dict(
        ref=CommandRef("session", "terminal", "invalid"), request_id="request-invalid",
        cwd="/workspace/project", environment_id="environment-v1", shell="/bin/sh",
        text="printf hi", argv=(), task_id="task",
    )
    values.update(changes)
    with pytest.raises(ValueError):
        CommandRequest(**values)


def test_output_round_trip_is_canonical_and_bounded():
    ref = CommandRef("session", "terminal", "command")
    chunk = OutputChunk(ref, 1, 0, "stdout", "中文\n".encode("utf-8"))
    record = chunk.record()

    assert OutputChunk.from_record(record) == chunk
    assert record["encoding"] == "base64"
    assert base64.b64encode(chunk.data).decode("ascii") == record["data"]
    with pytest.raises(ValueError, match="Invalid terminal output encoding"):
        OutputChunk.from_record({**record, "data": record["data"] + "="})
    with pytest.raises(ValueError, match="Noncanonical"):
        OutputChunk.from_record({**record, "data": "Zh=="})


def test_arbitrary_output_bytes_survive_split_utf8_and_control_bytes():
    value = request()
    data = "中文".encode() + b"\x00\xff\x1b[31m"
    chunks = [OutputChunk(value.ref, 1, 0, "stdout", data[:1]),
              OutputChunk(value.ref, 2, 1, "stderr", data[1:])]
    assert b"".join(OutputChunk.from_record(chunk.record()).data for chunk in chunks) == data


@pytest.mark.parametrize("data", [b"", b"x" * (MAX_CHUNK_BYTES + 1)])
def test_output_block_size_is_bounded(data):
    with pytest.raises(ValueError, match="block size"):
        OutputChunk(request().ref, 1, 0, "stdout", data)


def test_success_and_output_completeness_are_independent():
    value = request()
    state = accepted(value)
    state = apply_event(state, event("started", value.ref, execution_id="execution"))
    first = OutputChunk(value.ref, 1, 0, "stdout", b"ok")
    state = apply_event(state, event("output", value.ref, chunk=first.record()))
    state = apply_event(
        state,
        event(
            "exited",
            value.ref,
            exit_code=0,
            signal=None,
            output_complete=False,
            output_sequence=1,
            output_bytes=2,
        ),
    )

    assert state.succeeded
    assert not state.output_complete
    assert state.record()["phase"] == "exited"


def test_output_watermark_and_order_cannot_be_fabricated():
    value = request()
    state = apply_event(accepted(value), event("started", value.ref, execution_id="execution"))
    chunk = OutputChunk(value.ref, 1, 0, "stdout", b"ok")
    state = apply_event(state, event("output", value.ref, chunk=chunk.record()))
    with pytest.raises(ValueError, match="order"):
        apply_event(state, event("output", value.ref, chunk=OutputChunk(
            value.ref, 3, 2, "stdout", b"late",
        ).record()))
    with pytest.raises(ValueError, match="watermark"):
        apply_event(
            state,
            event(
                "exited", value.ref, exit_code=0, signal=None, output_complete=True,
                output_sequence=0, output_bytes=0,
            ),
        )


def test_cancel_is_a_request_until_exit_is_observed():
    value = request()
    state = apply_event(accepted(value), event("started", value.ref, execution_id="execution"))
    state = apply_event(state, event("cancel_requested", value.ref))

    assert state.phase == "running"
    assert state.cancel_requested
    assert state.exit_code is None


def test_recovery_marks_unobserved_execution_unknown_without_replaying_it():
    value = request()
    state = apply_event(accepted(value), event("started", value.ref, execution_id="execution"))
    restored = recovered(state)

    assert restored.phase == "unknown"
    assert restored.reason == "Execution owner is not attached"
    assert not restored.output_complete


def test_not_started_is_only_valid_before_start_observation():
    value = request()
    state = accepted(value)
    state = apply_event(state, event("not_started", value.ref, reason="spawn rejected"))
    assert state.phase == "not_started"
    assert state.output_complete

    running = apply_event(accepted(value), event("started", value.ref, execution_id="execution"))
    with pytest.raises(ValueError, match="Cannot prove"):
        apply_event(running, event("not_started", value.ref, reason="late"))


def test_recovered_admission_cannot_be_assumed_unexecuted():
    value = request()
    state = recovered(accepted(value))
    assert state.phase == "unknown"
    with pytest.raises(ValueError, match="reconciliation"):
        apply_event(state, event("started", value.ref, execution_id="second-execution"))
    with pytest.raises(ValueError, match="uncertain"):
        apply_event(state, event("not_started", value.ref, reason="no start row"))


def test_known_execution_can_reconcile_late_output_and_signal_exit():
    value = request()
    state = recovered(apply_event(accepted(value), event(
        "started", value.ref, execution_id="execution",
    )))
    chunk = OutputChunk(value.ref, 1, 0, "stdout", b"tail")
    state = apply_event(state, event("output", value.ref, chunk=chunk.record()))
    state = apply_event(state, event(
        "exited", value.ref, exit_code=None, signal=15, output_complete=False,
        output_sequence=1, output_bytes=4,
    ))
    assert state.phase == "exited" and state.signal == 15
    assert not state.succeeded and not state.output_complete
    assert recovered(state) is state


def test_duplicate_admission_does_not_reset_execution_and_conflicts_fail():
    value = request()
    state = apply_event(accepted(value), event("started", value.ref, execution_id="execution"))
    assert apply_event(state, event("accepted", value.ref, request=value.record())) is state
    changed = replace(value, text="different command")
    with pytest.raises(ValueError, match="conflict"):
        apply_event(state, event("accepted", value.ref, request=changed.record()))


@pytest.mark.parametrize("mode,stream", [("pty", "stdout"), ("pipes", "pty")])
def test_stream_labels_match_actual_capture_mode(mode, stream):
    value = request(mode=mode)
    state = apply_event(accepted(value), event("started", value.ref, execution_id="execution"))
    with pytest.raises(ValueError, match="stream mismatch"):
        apply_event(state, event("output", value.ref, chunk=OutputChunk(
            value.ref, 1, 0, stream, b"data",
        ).record()))


@pytest.mark.parametrize("kind", [[], {}, None, "future"])
def test_malformed_or_future_event_kinds_are_rejected(kind):
    with pytest.raises(ValueError, match="Invalid terminal event"):
        event(kind, request().ref)
    with pytest.raises(ValueError, match="Invalid terminal event"):
        apply_event(None, {"kind": kind})


@pytest.mark.parametrize("changes", [
    {"exit_code": True}, {"exit_code": None}, {"signal": 15},
    {"exit_code": 256}, {"exit_code": None, "signal": 0},
    {"output_sequence": True}, {"output_complete": 1},
])
def test_exit_facts_reject_ambiguous_codes_and_boolean_watermarks(changes):
    value = request()
    state = apply_event(accepted(value), event("started", value.ref, execution_id="execution"))
    fields = dict(exit_code=0, signal=None, output_complete=True,
                  output_sequence=0, output_bytes=0)
    fields.update(changes)
    with pytest.raises(ValueError):
        apply_event(state, event("exited", value.ref, **fields))
