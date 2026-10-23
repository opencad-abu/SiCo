"""Lossless bounded terminal output blocks with explicit capture stream labels."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from .terminal_commands import CommandRef, exact_fields

OUTPUT_CONTRACT = "sico.terminal.output.v1"
MAX_CHUNK_BYTES = 65536
MAX_POSITION = (1 << 63) - 1


def position(value, *, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_POSITION:
        raise ValueError("Invalid terminal output position")
    return value


def output_position(ref, sequence, offset, stream, size):
    """Validate capture coordinates independently of attachment availability."""
    if not isinstance(ref, CommandRef):
        raise ValueError("Output requires a terminal reference")
    position(sequence, minimum=1)
    position(offset)
    if stream not in ("stdout", "stderr", "pty"):
        raise ValueError("Invalid terminal output stream")
    if type(size) is not int or not 1 <= size <= MAX_CHUNK_BYTES:
        raise ValueError("Invalid terminal output block size")
    return position(offset + size)


@dataclass(frozen=True)
class OutputChunk:
    ref: CommandRef
    sequence: int
    offset: int
    stream: str
    data: bytes

    def __post_init__(self):
        if type(self.data) is not bytes:
            raise ValueError("Invalid terminal output block size")
        output_position(self.ref, self.sequence, self.offset, self.stream, len(self.data))

    @property
    def end(self):
        """Byte offset across all streams in capture order, not display characters."""
        return self.offset + len(self.data)

    def record(self):
        return dict(contract=OUTPUT_CONTRACT, ref=self.ref.record(),
                    sequence=self.sequence, offset=self.offset, stream=self.stream,
                    encoding="base64", data=base64.b64encode(self.data).decode("ascii"))

    @classmethod
    def from_record(cls, value):
        exact_fields(value, ("contract", "ref", "sequence", "offset", "stream",
                             "encoding", "data"))
        if value["contract"] != OUTPUT_CONTRACT or value["encoding"] != "base64":
            raise ValueError("Unsupported terminal output contract")
        data = value["data"]
        if not isinstance(data, str) or len(data) > 4 * ((MAX_CHUNK_BYTES + 2) // 3):
            raise ValueError("Invalid encoded terminal output size")
        try:
            decoded = base64.b64decode(data.encode("ascii"), validate=True)
        except (UnicodeError, binascii.Error, ValueError) as exc:
            raise ValueError("Invalid terminal output encoding") from exc
        if base64.b64encode(decoded).decode("ascii") != data:
            raise ValueError("Noncanonical terminal output encoding")
        return cls(CommandRef.from_record(value["ref"]), value["sequence"],
                   value["offset"], value["stream"], decoded)
