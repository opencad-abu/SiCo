"""Text formatting helpers for RC network views."""

from __future__ import annotations

import math
from typing import Any


_PREFIXES = ("f", "p", "n", "u", "m", "", "k", "M", "G", "T")


def engineering(value: float, unit: str) -> str:
    if value == 0 or not math.isfinite(value):
        return f"{value:.6g} {unit}"
    exponent = math.floor(math.log10(abs(value)) / 3) * 3
    exponent = min(12, max(-15, exponent))
    prefix = _PREFIXES[(exponent + 15) // 3]
    return f"{value / (10**exponent):.4g} {prefix}{unit}"


def format_node_label(node: Any) -> str:
    if node.external:
        return f"{node.name} [net {node.owner_net_name or 'unowned'}]"
    return str(node.name)


def format_tooltip(
    kind: str, item: Any, nodes: dict[int, Any], *, include_capacitance: bool = False,
) -> str:
    """Return stable plain-text details for a hit-tested network object."""
    if kind == "node":
        lines = [f"Node {item.name}", f"Kind: {item.kind}"]
        if item.external:
            lines.append(f"External owner: {item.owner_net_name or 'unowned'}")
        if item.x is not None and item.y is not None:
            lines.append(f"Position: {item.x:.6g}, {item.y:.6g}")
        if item.layer:
            lines.append(f"Layer: {item.layer}")
        if include_capacitance:
            lines.append(
                f"Ground C: {engineering(item.ground_capacitance, 'F')} "
                f"({item.ground_capacitor_count})"
            )
            lines.append(
                f"Coupling C: {engineering(item.coupling_capacitance, 'F')} "
                f"({item.coupling_capacitor_count})"
            )
        return "\n".join(lines)
    first, second = nodes.get(item.node1_id), nodes.get(item.node2_id)
    endpoints = (
        f"{getattr(first, 'name', item.node1_id)} -> "
        f"{getattr(second, 'name', item.node2_id)}"
    )
    if kind == "resistor":
        lines = [f"R {item.name}", endpoints, engineering(item.value, "ohm")]
        if item.layer:
            lines.append(f"Layer: {item.layer}")
        if item.length is not None or item.width is not None:
            length = item.length if item.length is not None else "-"
            width = item.width if item.width is not None else "-"
            lines.append(f"L/W: {length} / {width}")
        if item.cross_net:
            lines.append("Cross-net endpoint")
        return "\n".join(lines)
    lines = [
        f"C {item.name} ({item.kind})",
        endpoints,
        engineering(item.value, "F"),
    ]
    if item.layer:
        lines.append(f"Layer: {item.layer}")
    return "\n".join(lines)


__all__ = ["engineering", "format_node_label", "format_tooltip"]
