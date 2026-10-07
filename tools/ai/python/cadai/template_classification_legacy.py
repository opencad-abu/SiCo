"""Frozen pre-20260922 capture classification for archived raw captures only.

Compatibility boundary: never use for a capture declaring explicit_evidence_v1.
Retire only when archived capture reproduction is no longer supported; stored
v1/v2 records do not call this classifier and remain readable independently.
"""

import re

_PIN_CELLS = {"ipin", "opin", "iopin"}
_ANALOG = {
    "vdc": "voltage_source",
    "vac": "voltage_source",
    "vpulse": "voltage_source",
    "idc": "current_source",
    "ipwl": "current_source",
    "res": "resistor",
    "cap": "capacitor",
    "ind": "inductor",
    "gnd": "supply_marker",
    "vdd": "supply_marker",
}


def classify_legacy(instance, pin_names):
    lib, cell = instance["libName"], instance["cellName"]
    if lib == "basic" and cell in _PIN_CELLS:
        return "port_graphic", "port_graphic", {}
    if lib == "basic" and cell == "noConn":
        return "no_connect", "no_connect", {}
    if lib == "US_8ths" or (
        lib == "zambezi45" and cell in {"sheet_a", "sheet_b", "sheet_d"} and not pin_names
    ):
        return "decoration", "sheet", {}
    if lib == "gpdk045":
        mos = re.fullmatch(r"([np]mos)([12]v)(?:_(hvt|lvt|nat|3))?", cell)
        if mos:
            return "device", mos[1], {"voltage_class": mos[2], "flavor": mos[3] or "standard"}
        known = {
            "resnsppoly": "resistor",
            "resnsnpoly": "resistor",
            "ndio": "diode",
            "mimcap": "capacitor",
            "vpnp2": "pnp",
            "nmoscap1v": "moscap_n",
            "nmoscap2v": "moscap_n",
            "pmoscap1v": "moscap_p",
        }
        if cell in known:
            return "device", known[cell], {"device_class": cell}
    if lib == "analogLib" and cell in _ANALOG:
        kind = _ANALOG[cell]
        return (
            ("supply_marker" if kind == "supply_marker" else "device"),
            kind,
            {"device_class": cell},
        )
    if instance.get("schematic_available") or lib in {
        "gsclib045",
        "saradc",
        "saradcII",
        "zambezi45",
        "amsLDO",
    }:
        return "hierarchy", "subcircuit", {"master": lib + "/" + cell + "/" + instance["viewName"]}
    return "unknown", "unknown", {"master": lib + "/" + cell + "/" + instance["viewName"]}
