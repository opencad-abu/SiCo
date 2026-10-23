"""Model-facing tools for the one command-scoped session terminal.

They are registered only when a qualified SessionTerminal was injected. They
never create a process or route themselves; the product session owns that.
"""

from __future__ import annotations

import base64
import binascii
import uuid

from ..core.contracts import ToolResult
from ..core.terminal_commands import CommandRef, text_value
from ..core.terminal_input import MAX_INPUT_BYTES
from ..core.tools import Tool

TERMINAL_TOOL_NAMES = frozenset({
    "terminal_exec", "terminal_status", "terminal_wait", "terminal_write_input",
    "terminal_cancel",
})
MAX_WAIT_SECONDS = 30


def _command(value):
    if not isinstance(value, dict):
        raise ValueError("Terminal command reference must be an object")
    return CommandRef.from_record(value)


def _receipt(terminal, request, *, preview_limit=8192):
    state = terminal.command(request)
    return ToolResult(data=dict(command=request.record(), state=state.record(),
                                output=terminal.output_preview(request, preview_limit)))


def _start(terminal, inputs, _context):
    command = text_value(inputs["command"], limit=65536)
    cwd = inputs.get("cwd")
    if cwd is not None:
        text_value(cwd, limit=4096)
        if not cwd.startswith("/"):
            raise ValueError("Terminal command cwd must be absolute")
    request = terminal.request(uuid.uuid4().hex, uuid.uuid4().hex, text=command, cwd=cwd)
    terminal.submit(request)
    try:
        terminal.wait(request.ref, timeout=1)
    except TimeoutError:
        pass
    return _receipt(terminal, request.ref)


def _status(terminal, inputs, _context):
    return _receipt(terminal, _command(inputs["command"]))


def _wait(terminal, inputs, _context):
    request = _command(inputs["command"])
    timeout = inputs.get("timeout", 5)
    if type(timeout) is not int or not 1 <= timeout <= MAX_WAIT_SECONDS:
        raise ValueError("Terminal wait timeout is out of range")
    try:
        terminal.wait(request, timeout=timeout)
    except TimeoutError:
        pass
    return _receipt(terminal, request)


def _write(terminal, inputs, _context):
    request = _command(inputs["command"])
    data = inputs["data"]
    if not isinstance(data, str) or len(data) > 4 * ((MAX_INPUT_BYTES + 2) // 3):
        raise ValueError("Invalid terminal input data")
    try:
        raw = base64.b64decode(data.encode("ascii"), validate=True)
    except (UnicodeError, binascii.Error, ValueError) as exc:
        raise ValueError("Invalid terminal input encoding") from exc
    if not raw or len(raw) > MAX_INPUT_BYTES or base64.b64encode(raw).decode() != data:
        raise ValueError("Invalid terminal input data")
    terminal.take_input(request, "model")
    terminal.write_input(request, "model", raw)
    return _receipt(terminal, request)


def _cancel(terminal, inputs, _context):
    request = _command(inputs["command"])
    terminal.cancel(request)
    return _receipt(terminal, request)


def register_terminal(registry, terminal):
    """Register the terminal command tools on one session-owned registry."""
    command_schema = {
        "command": {"type": "string"},
        "cwd": {"type": ["string", "null"]},
    }
    ref_schema = {"command": {"type": "object"}}
    registry.register(Tool(
        "terminal_exec",
        "Run a SiCo/Codex shell command in this session's one command terminal. "
        "Returns a command reference and bounded output; use terminal_wait/terminal_input "
        "for longer commands and stdin.",
        {"type": "object", "properties": command_schema, "required": ["command"],
         "additionalProperties": False},
        lambda inputs: _validate_start(inputs), lambda inputs, context: _start(terminal, inputs, context),
        execution_domain="local", effect="unknown",
        annotations={"readOnlyHint": False, "destructiveHint": True},
    ))
    registry.register(Tool(
        "terminal_status",
        "Read the bounded status and output preview for a terminal command.",
        {"type": "object", "properties": ref_schema, "required": ["command"],
         "additionalProperties": False},
        lambda inputs: _validate_ref(inputs), lambda inputs, context: _status(terminal, inputs, context),
        execution_domain="local", effect="read",
    ))
    registry.register(Tool(
        "terminal_wait",
        "Wait up to 30 seconds for a terminal command and return its bounded status.",
        {"type": "object", "properties": {**ref_schema,
            "timeout": {"type": "integer", "minimum": 1, "maximum": MAX_WAIT_SECONDS}},
         "required": ["command"], "additionalProperties": False},
        lambda inputs: _validate_wait(inputs), lambda inputs, context: _wait(terminal, inputs, context),
        execution_domain="local", effect="read",
    ))
    registry.register(Tool(
        "terminal_write_input",
        "Take model ownership and write bounded base64 stdin to the active terminal command.",
        {"type": "object", "properties": {**ref_schema, "data": {"type": "string"}},
         "required": ["command", "data"], "additionalProperties": False},
        lambda inputs: _validate_write(inputs), lambda inputs, context: _write(terminal, inputs, context),
        execution_domain="local", effect="unknown",
        annotations={"readOnlyHint": False, "destructiveHint": True},
    ))
    registry.register(Tool(
        "terminal_cancel",
        "Request cancellation of the active terminal command; completion still requires observation.",
        {"type": "object", "properties": ref_schema, "required": ["command"],
         "additionalProperties": False},
        lambda inputs: _validate_ref(inputs), lambda inputs, context: _cancel(terminal, inputs, context),
        execution_domain="local", effect="unknown",
        annotations={"readOnlyHint": False, "destructiveHint": True},
    ))


def _validate_start(inputs):
    if not isinstance(inputs, dict) or set(inputs) - {"command", "cwd"} or "command" not in inputs:
        raise ValueError("Invalid terminal execution arguments")
    text_value(inputs["command"], limit=65536)
    if inputs.get("cwd") is not None:
        text_value(inputs["cwd"], limit=4096)


def _validate_ref(inputs):
    if not isinstance(inputs, dict) or set(inputs) != {"command"}:
        raise ValueError("Invalid terminal command reference")
    _command(inputs["command"])


def _validate_wait(inputs):
    if not isinstance(inputs, dict) or set(inputs) - {"command", "timeout"} or "command" not in inputs:
        raise ValueError("Invalid terminal wait arguments")
    _command(inputs["command"])
    timeout = inputs.get("timeout", 5)
    if type(timeout) is not int or not 1 <= timeout <= MAX_WAIT_SECONDS:
        raise ValueError("Terminal wait timeout is out of range")


def _validate_write(inputs):
    if not isinstance(inputs, dict) or set(inputs) != {"command", "data"}:
        raise ValueError("Invalid terminal input arguments")
    _command(inputs["command"])


__all__ = ["TERMINAL_TOOL_NAMES", "register_terminal"]
