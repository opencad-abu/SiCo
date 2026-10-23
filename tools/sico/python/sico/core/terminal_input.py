"""Bounded stdin ownership and delivery values for one active terminal command."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from .terminal_commands import CommandRef, exact_fields

INPUT_CONTRACT = "sico.terminal.input.v1"
MAX_INPUT_BYTES = 4096
INPUT_OWNERS = ("model", "user")


@dataclass(frozen=True)
class TerminalInput:
    """One serialized stdin delivery owned by the terminal's active input owner.

    Bytes are deliberately not a command-history fact: the journal records only
    that input occurred and how many bytes the trusted owner delivered. This
    keeps password-like input out of ordinary terminal history.
    """

    ref: CommandRef
    owner: str
    sequence: int
    data: bytes

    def __post_init__(self):
        if not isinstance(self.ref, CommandRef):
            raise ValueError("Terminal input requires a command reference")
        if self.owner not in INPUT_OWNERS:
            raise ValueError("Invalid terminal input owner")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Invalid terminal input sequence")
        if type(self.data) is not bytes or not 1 <= len(self.data) <= MAX_INPUT_BYTES:
            raise ValueError("Invalid terminal input size")

    def record(self):
        return dict(contract=INPUT_CONTRACT, ref=self.ref.record(), owner=self.owner,
                    sequence=self.sequence, encoding="base64",
                    data=base64.b64encode(self.data).decode("ascii"))

    @classmethod
    def from_record(cls, value):
        exact_fields(value, ("contract", "ref", "owner", "sequence", "encoding", "data"))
        if value["contract"] != INPUT_CONTRACT or value["encoding"] != "base64":
            raise ValueError("Unsupported terminal input contract")
        data = value["data"]
        if (not isinstance(data, str)
                or len(data) > 4 * ((MAX_INPUT_BYTES + 2) // 3)):
            raise ValueError("Invalid encoded terminal input")
        try:
            decoded = base64.b64decode(data.encode("ascii"), validate=True)
        except (UnicodeError, binascii.Error, ValueError) as exc:
            raise ValueError("Invalid terminal input encoding") from exc
        if base64.b64encode(decoded).decode("ascii") != data:
            raise ValueError("Noncanonical terminal input encoding")
        return cls(CommandRef.from_record(value["ref"]), value["owner"],
                   value["sequence"], decoded)
