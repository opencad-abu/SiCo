"""Recovery-oriented, line-at-a-time DSPF parser."""

from __future__ import annotations

import shlex
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .values import layer_attribute, optional_spice_number, parse_attributes
from .values import parse_spice_number


PARSER_VERSION = "2"
_SUMMARY_LIMIT = 240


@dataclass(frozen=True)
class DspfRecord:
    kind: str
    line_start: int
    line_end: int
    fields: Mapping[str, Any]
    raw: str


@dataclass(frozen=True)
class _LogicalLine:
    text: str
    line_start: int
    line_end: int
    byte_end: int
    decode_error: bool = False


def iter_dspf_records(
    source: str | Path,
    *,
    on_line: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[DspfRecord]:
    """Yield structured records without retaining the source file in memory."""
    current_net: str | None = None
    in_layer_map = False
    in_instance_section = False
    for logical in _logical_lines(Path(source)):
        if cancelled is not None and cancelled():
            from .indexer import IndexCancelled

            raise IndexCancelled("DSPF indexing cancelled")
        if on_line is not None:
            on_line(logical.byte_end, logical.line_end)
        raw = logical.text
        text = raw.strip()
        if logical.decode_error:
            yield _diagnostic(logical, "warning", "decode_replacement", "Invalid UTF-8 bytes were replaced")
        if not text:
            continue
        marker = text[0]
        if marker == "*":
            upper = text.upper()
            if "INSTANCE SECTION" in upper:
                in_instance_section = True
                current_net = None
                in_layer_map = False
                yield _record(logical, "instance_section")
                continue
            if upper.startswith("*LAYER_MAP"):
                in_layer_map = True
                continue
            if text.startswith("*|"):
                directive = _directive_record(logical)
                if directive is not None:
                    if directive.kind == "net":
                        current_net = str(directive.fields["name"])
                        in_instance_section = False
                        in_layer_map = False
                    elif directive.fields.get("code") == "malformed_net":
                        current_net = None
                        in_instance_section = False
                        in_layer_map = False
                        yield _record(logical, "net_boundary")
                    yield directive
                    if directive.fields.get("parse_error"):
                        yield _diagnostic(
                            logical, "error", "invalid_net_capacitance",
                            str(directive.fields["parse_error"]),
                        )
                continue
            if in_layer_map:
                layer = _layer_record(logical)
                if layer is not None:
                    yield layer
            continue
        if marker == ".":
            upper = text.upper()
            if upper.startswith(".SUBCKT"):
                fields = _tokens(text)
                if len(fields) < 2:
                    yield _diagnostic(logical, "error", "malformed_subckt", "Missing subcircuit name")
                else:
                    current_net = None
                    in_instance_section = False
                    yield _record(logical, "subcircuit_start", name=fields[1], ports=fields[2:])
                continue
            if upper.startswith(".ENDS"):
                fields = _tokens(text)
                yield _record(logical, "subcircuit_end", name=fields[1] if len(fields) > 1 else None)
                current_net = None
                in_instance_section = False
                continue
            if upper != ".END":
                yield _diagnostic(logical, "warning", "unsupported_directive", "Unsupported DSPF directive")
            continue
        if marker == "+":
            yield _diagnostic(logical, "warning", "orphan_continuation", "Continuation has no preceding record")
            continue
        prefix = marker.upper()
        if prefix in ("R", "C"):
            yield from _element_records(logical, prefix, current_net, in_instance_section)
        elif in_instance_section:
            yield _instance_record(logical)
        elif prefix in ("X", "M", "Q", "D", "J"):
            in_instance_section = True
            current_net = None
            yield _record(logical, "instance_section")
            yield _instance_record(logical)
        elif prefix in ("L", "K"):
            yield _diagnostic(logical, "warning", "unsupported_device", f"{prefix} device is not analyzed")
        else:
            yield _diagnostic(logical, "warning", "unknown_record", "Unrecognized DSPF record")


def _logical_lines(source: Path) -> Iterator[_LogicalLine]:
    pending: _LogicalLine | None = None
    byte_end = 0
    with source.open("rb") as stream:
        for line_no, raw in enumerate(stream, 1):
            byte_end += len(raw)
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            replacement = "\ufffd" in text
            if text.lstrip().startswith("+") and pending is not None:
                addition = text.lstrip()[1:].strip()
                pending = _LogicalLine(
                    f"{pending.text} {addition}", pending.line_start, line_no,
                    byte_end, pending.decode_error or replacement,
                )
                continue
            if pending is not None:
                yield pending
            pending = _LogicalLine(text, line_no, line_no, byte_end, replacement)
    if pending is not None:
        yield pending


def _directive_record(line: _LogicalLine) -> DspfRecord | None:
    text = line.text.strip()
    head = text[2:].lstrip()
    match = re.match(r"[A-Za-z_]+", head)
    keyword = match.group(0).upper() if match else ""
    remainder = head[match.end():].strip() if match else ""
    if keyword == "NET":
        fields = _tokens(remainder)
        if not fields:
            return _diagnostic(line, "error", "malformed_net", "Missing net name")
        cap = None
        raw_cap = fields[1] if len(fields) > 1 else None
        if raw_cap is not None:
            try:
                cap = parse_spice_number(raw_cap)
            except ValueError:
                return _record(
                    line, "net", name=fields[0], declared_cap=None,
                    raw_cap=raw_cap,
                    parse_error=f"Invalid net capacitance {raw_cap!r}",
                )
        return _record(
            line, "net", name=fields[0], declared_cap=cap, raw_cap=raw_cap,
        )
    if keyword in ("P", "I", "S"):
        return _connection_record(line, keyword.lower())
    if keyword == "GROUND_NET":
        fields = _tokens(remainder)
        if not fields:
            return _diagnostic(line, "error", "malformed_ground", "Missing ground net name")
        return _record(line, "ground", name=fields[0])
    value = remainder.strip().strip('"')
    return _record(line, "metadata", key=keyword.lower(), value=value)


def _connection_record(line: _LogicalLine, kind: str) -> DspfRecord:
    text = line.text
    start = text.find("(")
    end = text.find(")", start + 1)
    if start < 0 or end < 0:
        return _diagnostic(line, "error", "malformed_connection", f"Malformed *|{kind.upper()} record")
    fields = _tokens(text[start + 1:end])
    minimum = {"p": 2, "i": 4, "s": 1}[kind]
    if len(fields) < minimum:
        return _diagnostic(line, "error", "malformed_connection", f"Truncated *|{kind.upper()} record")
    attrs = parse_attributes(text[end + 1:])
    data: dict[str, Any] = {"name": fields[0], "attributes": attrs}
    if kind == "p":
        data.update(direction=fields[1], capacitance=_number_at(fields, 2), x=_number_at(fields, 3), y=_number_at(fields, 4))
    elif kind == "i":
        data.update(instance=fields[1], pin=fields[2], direction=fields[3], capacitance=_number_at(fields, 4), x=_number_at(fields, 5), y=_number_at(fields, 6))
    else:
        data.update(x=_number_at(fields, 1), y=_number_at(fields, 2))
    data["layer"] = layer_attribute(attrs)
    return DspfRecord("node_" + kind, line.line_start, line.line_end, data, _summary(text))


def _element_records(
    line: _LogicalLine, prefix: str, current_net: str | None, in_instance: bool
) -> Iterator[DspfRecord]:
    parts = line.text.strip().split(None, 4)
    if in_instance or current_net is None:
        yield _record(line, "model_device", device_type=prefix, name=parts[0])
        yield _diagnostic(line, "warning", "model_device_ignored", f"Model {prefix} device is not parasitic")
        return
    if len(parts) < 4:
        yield _diagnostic(line, "error", "malformed_element", f"Truncated {prefix} element")
        return
    try:
        value = parse_spice_number(parts[3])
    except ValueError:
        model_value = parts[4].split(None, 1)[0] if len(parts) > 4 else ""
        try:
            parse_spice_number(model_value)
        except ValueError:
            yield _diagnostic(line, "error", "invalid_element_value", f"Invalid {prefix} value {parts[3]!r}")
            return
        yield _record(
            line, "model_device", device_type=prefix, name=parts[0],
            model=parts[3], value=model_value,
        )
        yield _diagnostic(line, "warning", "model_device_ignored", f"Model {prefix} device is not parasitic")
        return
    attrs_text = parts[4] if len(parts) > 4 else ""
    attrs = parse_attributes(attrs_text)
    kind = "resistor" if prefix == "R" else "capacitor"
    yield _record(
        line, kind, name=parts[0], node1=parts[1], node2=parts[2],
        value=value, raw_value=parts[3], declared_net=current_net,
        layer=layer_attribute(attrs), length=optional_spice_number(attrs.get("l")),
        width=optional_spice_number(attrs.get("w")), attributes=attrs,
    )
    if value < 0:
        yield _diagnostic(line, "warning", "negative_" + kind, f"Negative {kind} value")


def _instance_record(line: _LogicalLine) -> DspfRecord:
    fields = _tokens(line.text)
    params = next((index for index, value in enumerate(fields[1:], 1) if "=" in value), len(fields))
    model_at = max(1, params - 1)
    return _record(
        line, "instance", name=fields[0], model=fields[model_at] if len(fields) > 1 else None,
        nodes=fields[1:model_at],
    )


def _layer_record(line: _LogicalLine) -> DspfRecord | None:
    fields = _tokens(line.text[1:])
    if len(fields) < 2 or not fields[0].isdigit():
        return None
    attrs = parse_attributes(" ".join(fields[2:]))
    return _record(line, "layer", number=int(fields[0]), name=fields[1], itf=attrs.get("itf"))


def _record(line: _LogicalLine, kind: str, **fields: Any) -> DspfRecord:
    return DspfRecord(kind, line.line_start, line.line_end, fields, _summary(line.text))


def _diagnostic(line: _LogicalLine, severity: str, code: str, message: str) -> DspfRecord:
    return _record(line, "diagnostic", severity=severity, code=code, message=message)


def _tokens(text: str) -> list[str]:
    if not any(character in text for character in "\"'\\"):
        return text.split()
    try:
        return shlex.split(text, comments=False, posix=True)
    except ValueError:
        return text.split()


def _number_at(fields: list[str], index: int) -> float | None:
    return optional_spice_number(fields[index]) if index < len(fields) else None


def _summary(text: str) -> str:
    value = text.strip()
    return value if len(value) <= _SUMMARY_LIMIT else value[: _SUMMARY_LIMIT - 3] + "..."
