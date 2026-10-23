"""Normalize the symbol interface with one verified terminal ordering."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from rnm_inspection import (
    REQUIRED_SYMBOL_KEYS,
    BlockedInput,
    _as_dicts,
    _cellview,
    _integer,
    _require_ok,
    _text,
)


def _ordered_symbol_terminals(symbol: Mapping[str, Any], path: Path) -> tuple[list[dict[str, Any]], str]:
    terminals = _as_dicts(symbol.get("terminals"), "terminals", str(path))
    by_name: dict[str, dict[str, Any]] = {}
    for terminal in terminals:
        name = _text(terminal.get("name"), "terminal.name", str(path), required=True)
        key = name.casefold()
        if key in by_name:
            raise BlockedInput(f"{path}: duplicate symbol terminal {name!r}", code="duplicate_name")
        by_name[key] = terminal

    port_order = symbol.get("port_order")
    term_order = symbol.get("term_order")
    if port_order not in (None, []) and not isinstance(port_order, list):
        raise BlockedInput(f"{path}: port_order must be a list", code="invalid_field")
    if term_order not in (None, []) and not isinstance(term_order, list):
        raise BlockedInput(f"{path}: term_order must be a list", code="invalid_field")

    for label, order in (("port_order", port_order), ("term_order", term_order)):
        if order:
            if any(not isinstance(name, str) or not name.strip() for name in order):
                raise BlockedInput(f"{path}: {label} contains an invalid name", code="invalid_field")
            if len({name.casefold() for name in order}) != len(order):
                raise BlockedInput(f"{path}: {label} contains duplicate names", code="duplicate_name")
            missing = [name for name in order if name.casefold() not in by_name]
            if missing:
                raise BlockedInput(
                    f"{path}: {label} references unknown terminal(s): {', '.join(missing)}",
                    code="order_mismatch",
                )

    if port_order and term_order:
        if [str(name).casefold() for name in port_order] != [
            str(name).casefold() for name in term_order
        ]:
            raise BlockedInput(
                f"{path}: port_order and term_order disagree",
                code="order_mismatch",
            )

    if port_order:
        order = port_order
        source = "symbol.port_order+term_order" if term_order else "symbol.port_order"
    elif term_order:
        order = term_order
        source = "symbol.term_order"
    else:
        order = [str(terminal["name"]) for terminal in terminals]
        source = "symbol.terminals_fallback"

    if len(order) != len(terminals):
        raise BlockedInput(
            f"{path}: port order has {len(order)} entries but terminals has {len(terminals)}",
            code="order_mismatch",
        )
    return [by_name[str(name).casefold()] for name in order], source


def _normalize_symbol(symbol: Mapping[str, Any], path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _require_ok(symbol, "symbol", REQUIRED_SYMBOL_KEYS, path)
    identity = _cellview(symbol, str(path))
    terminals, order_source = _ordered_symbol_terminals(symbol, path)
    normalized: list[dict[str, Any]] = []
    for order, terminal in enumerate(terminals):
        subject = f"{path}: terminal {terminal.get('name')!r}"
        name = _text(terminal.get("name"), "name", subject, required=True)
        entry: dict[str, Any] = {
            "name": name,
            "direction": _text(terminal.get("direction"), "direction", subject),
            "bits": _integer(terminal.get("bits"), "bits", subject),
            "data_type": _text(terminal.get("data_type"), "data_type", subject),
            "port_kind": _text(terminal.get("port_kind"), "port_kind", subject),
            "order": order,
        }
        pins = terminal.get("pins")
        if pins is not None:
            pin_items = _as_dicts(pins, "pins", subject)
            entry["pins"] = [
                {"net": _text(pin.get("net"), "pin.net", subject)} for pin in pin_items
            ]
        normalized.append(entry)
    return normalized, {"identity": identity, "order_source": order_source}
