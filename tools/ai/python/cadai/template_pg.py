"""PG (power/ground) role candidates for one stored template.

The captured ``sig_type`` is source data and is not trustworthy on its own
(several references mark signal nets as ``supply``). Names are the strongest
evidence, then the direction: output pins are never power pins. This module
only proposes candidates with reasons; the caller confirms the roles, and the
target keeps local nets with explicit pins instead of global ``VDD!`` nets.
"""

from __future__ import annotations

import re

from .template_schema import TemplateUnavailable

PG_HINTS_SCHEMA = "cad.template.pg_hints.v1"
MAX_NETS = 256

POWER_STEMS = {
    "VDD",
    "VDDA",
    "VDDB",
    "VDDC",
    "VDDD",
    "VDDIO",
    "VDDP",
    "AVDD",
    "DVDD",
    "VCC",
    "VCCA",
    "VEE",
    "VBAT",
}
GROUND_STEMS = {
    "VSS",
    "VSSA",
    "VSSB",
    "VSSC",
    "VSSD",
    "VSSIO",
    "GND",
    "GNDA",
    "GNDD",
    "AGND",
    "DGND",
}


def _stem(name):
    # Global markers ("VDD!", "gnd!") and trailing indices still name the same rail.
    text = str(name).strip().rstrip("!")
    text = re.sub(r"[_ ]*[0-9]+$", "", text)
    return text.upper()


def pg_hints(record):
    """Role candidates for every captured net that can carry a PG hint."""
    topology = record.get("topology")
    if not topology:
        raise TemplateUnavailable("PG hints require a stored topology")
    ports_by_net = {}
    for port in topology.get("ports", []):
        ports_by_net.setdefault(port.get("net"), []).append(port)
    terminals = {}
    for device in topology.get("devices", []):
        for pin in device.get("pins", []):
            net = pin.get("net")
            if net:
                terminals[net] = terminals.get(net, 0) + 1
    hints = []
    for net in topology.get("nets", [])[:MAX_NETS]:
        net_id = net.get("id")
        name = net.get("source_name") or net_id
        ports = ports_by_net.get(net_id, [])
        sig_type = net.get("sig_type")
        stem = _stem(name)
        name_role = (
            "power" if stem in POWER_STEMS else "ground" if stem in GROUND_STEMS else None
        )
        signal_role = (
            "power" if sig_type == "supply" else "ground" if sig_type == "ground" else None
        )
        if not name_role and not signal_role:
            continue  # signal nets stay out of the PG candidate list
        direction_ok = all(port.get("direction") != "output" for port in ports) if ports else True
        reasons = []
        if name_role:
            reasons.append("name:" + name)
        if signal_role:
            if not direction_ok:
                reasons.append("pg_sig_type_ignored_for_output_pin")
            elif name_role is None:
                reasons.append("pg_sig_type_without_pg_name")
            else:
                reasons.append("sig_type:" + str(sig_type))
        role = name_role
        if role and name_role == signal_role:
            confidence = "high"
        elif role:
            confidence = "medium"
        else:
            confidence = "low"  # signals typed supply/ground without a PG name
        hints.append(
            {
                "net": name,
                "role_hint": role or "signal",
                "confidence": confidence,
                "sig_type": sig_type,
                "terminals": terminals.get(net_id, 0),
                "ports": [
                    {"name": port.get("name"), "direction": port.get("direction")}
                    for port in ports
                ],
                "reasons": reasons,
            }
        )
    return {
        "schema": PG_HINTS_SCHEMA,
        "template_ref": record.get("template_ref"),
        "nets": hints,
        "use": (
            "hint_only; caller confirms roles; target nets stay local and PG nets "
            "are declared as explicit pins"
        ),
    }
