"""Control-authorized frontend commands for the session command terminal."""

import base64
import binascii

from ..core.terminal_commands import CommandRef
from ..core.terminal_input import MAX_INPUT_BYTES, INPUT_OWNERS
from ..core.terminal_receipts import MAX_COMMANDS
from ..transport.framing import ProtocolError

COMMANDS = {"terminal_status", "terminal_take_input", "terminal_write_input",
            "terminal_cancel", "terminal_history", "terminal_read"}


def validate(operation, args, kwargs):
    if operation not in COMMANDS:
        raise ProtocolError("Unknown terminal command")
    if operation == "terminal_status":
        if args or kwargs:
            raise ProtocolError("Terminal query takes no arguments")
        return
    if operation == "terminal_history":
        if args or set(kwargs) - {"after", "limit"}:
            raise ProtocolError("Invalid terminal history arguments")
        if "after" in kwargs and (type(kwargs["after"]) is not int
                                  or not 0 <= kwargs["after"] <= (1 << 63) - 1):
            raise ProtocolError("Invalid terminal history cursor")
        if "limit" in kwargs and (type(kwargs["limit"]) is not int
                                  or not 1 <= kwargs["limit"] <= MAX_COMMANDS):
            raise ProtocolError("Invalid terminal history limit")
        return
    if operation == "terminal_read":
        if args or set(kwargs) != {"command", "offset", "limit"}:
            raise ProtocolError("Invalid terminal read arguments")
        command = kwargs["command"]
        if not isinstance(command, dict):
            raise ProtocolError("Terminal command reference must be an object")
        try:
            CommandRef.from_record(command)
        except ValueError as exc:
            raise ProtocolError("Invalid terminal command reference") from exc
        if (type(kwargs["offset"]) is not int or type(kwargs["limit"]) is not int
                or not 0 <= kwargs["offset"] <= (1 << 63) - 1
                or not 1 <= kwargs["limit"] <= 16384):
            raise ProtocolError("Invalid terminal read range")
        return
    fields = {"terminal_take_input": {"command", "owner"},
              "terminal_write_input": {"command", "owner", "data"},
              "terminal_cancel": {"command"}}[operation]
    if args or set(kwargs) != fields:
        raise ProtocolError("Invalid terminal command arguments")
    command = kwargs["command"]
    if not isinstance(command, dict):
        raise ProtocolError("Terminal command reference must be an object")
    try:
        CommandRef.from_record(command)
    except ValueError as exc:
        raise ProtocolError("Invalid terminal command reference") from exc
    if operation == "terminal_cancel":
        return
    if kwargs["owner"] not in INPUT_OWNERS:
        raise ProtocolError("Invalid terminal input owner")
    if operation == "terminal_write_input":
        data = kwargs["data"]
        if not isinstance(data, str) or len(data) > 4 * ((MAX_INPUT_BYTES + 2) // 3):
            raise ProtocolError("Invalid terminal input data")
        try:
            raw = base64.b64decode(data.encode("ascii"), validate=True)
        except (UnicodeError, binascii.Error, ValueError) as exc:
            raise ProtocolError("Invalid terminal input encoding") from exc
        if not raw or len(raw) > MAX_INPUT_BYTES or base64.b64encode(raw).decode() != data:
            raise ProtocolError("Invalid terminal input data")


def execute(controller, operation, args, kwargs):
    terminal = controller.terminal
    if terminal is None:
        raise ValueError("Session command terminal is not qualified")
    if operation == "terminal_status":
        return terminal.snapshot()
    if operation == "terminal_history":
        from ..storage.terminal_history import TerminalHistory

        return TerminalHistory(controller.journal).history(
            after=kwargs.get("after", 0), limit=kwargs.get("limit", MAX_COMMANDS))
    command = CommandRef.from_record(kwargs["command"])
    if (command.session_id != controller.session_id
            or command.terminal_id != terminal.claim.ref.terminal_id):
        raise ValueError("Terminal command belongs to another session terminal")
    if operation == "terminal_read":
        from ..storage.terminal_history import TerminalHistory

        return TerminalHistory(controller.journal).output(
            command, offset=kwargs["offset"], limit=kwargs["limit"])
    if operation == "terminal_take_input":
        terminal.take_input(command, kwargs["owner"])
    elif operation == "terminal_write_input":
        terminal.write_input(command, kwargs["owner"],
                             base64.b64decode(kwargs["data"].encode("ascii"), validate=True))
    elif operation == "terminal_cancel":
        terminal.cancel(command)
    else:
        raise ProtocolError("Unknown terminal command")
    return terminal.snapshot()
