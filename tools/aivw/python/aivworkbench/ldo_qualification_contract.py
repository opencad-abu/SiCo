"""Source-derived topology interface and operating-window contracts."""

from __future__ import annotations

import math
from typing import Any, Mapping

_TOPOLOGY_CONTRACT = {
    "LDO_MASTER": {
        "ports": ("VDD", "VSS", "VOUT"),
        "vdd_window": (4.5, 5.5),
        "en_polarity": None,
    },
    "LDO_AON": {
        "ports": ("VDD", "VSS", "EN", "VOUT"),
        "vdd_window": (1.9, 2.9),
        "en_polarity": "active_high",
    },
}


def _mapping_value(value: Mapping[str, Any], *paths: str) -> object:
    """Return the first present dotted field from an evidence object."""

    for path in paths:
        current: object = value
        present = True
        for component in path.split("."):
            if not isinstance(current, Mapping) or component not in current:
                present = False
                break
            current = current[component]
        if present:
            return current
    return None


def _validate_topology_contract(value: Mapping[str, Any], topology: str, findings: list[str]) -> None:
    """Check source-derived interface and operating-window facts."""

    contract = _TOPOLOGY_CONTRACT[topology]
    raw_ports = _mapping_value(value, "ports", "port_order", "interface.port_order", "interface.ports")
    ports: tuple[str, ...] | None = None
    if isinstance(raw_ports, Mapping):
        # A map-shaped interface is accepted only when its explicit order is
        # present; relying on dictionary insertion order would weaken the
        # source/interface binding.
        raw_order = raw_ports.get("port_order")
        raw_ports = raw_order
    if isinstance(raw_ports, (list, tuple)) and not isinstance(raw_ports, (str, bytes)):
        if all(isinstance(item, str) for item in raw_ports):
            ports = tuple(str(item) for item in raw_ports)
    if ports != contract["ports"]:
        findings.append("%s port contract does not match source topology" % topology)

    raw_window = _mapping_value(value, "vdd_window", "supply_window", "contract.vdd_window", "contract.supply_window")
    window: tuple[float, float] | None = None
    if isinstance(raw_window, Mapping):
        lower = raw_window.get("min")
        upper = raw_window.get("max")
        if (
            isinstance(lower, (int, float))
            and not isinstance(lower, bool)
            and isinstance(upper, (int, float))
            and not isinstance(upper, bool)
            and math.isfinite(float(lower))
            and math.isfinite(float(upper))
        ):
            window = (float(lower), float(upper))
    if window != contract["vdd_window"]:
        findings.append("%s VDD window does not match source topology" % topology)

    raw_polarity = _mapping_value(value, "en_polarity", "enable.polarity", "contract.en_polarity")
    if topology == "LDO_AON":
        if raw_polarity != contract["en_polarity"]:
            findings.append("LDO_AON enable polarity must be active_high")
        raw_active = _mapping_value(value, "en_active_level", "enable.active_level", "contract.en_active_level")
        raw_inactive = _mapping_value(value, "en_inactive_level", "enable.inactive_level", "contract.en_inactive_level")
        if raw_active != 1 or raw_inactive != 0:
            findings.append("LDO_AON enable levels must be active=1 and inactive=0")
    elif raw_polarity not in (None, "none", "not_applicable"):
        findings.append("LDO_MASTER must not declare an enable polarity")
