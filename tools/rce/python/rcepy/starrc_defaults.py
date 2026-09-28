"""Read installation StarRC defaults and migrate old behavior fields."""

from __future__ import annotations

import re

from caddefaults import Defaults
from caddefaults.syntax import sections

MIGRATED_FIELDS = {
    "mode": "MODE",
    "reduction": "REDUCTION",
    "density_based_thickness": "DENSITY_BASED_THICKNESS",
    "couple_to_ground": "COUPLE_TO_GROUND",
    "xref_use_layout_device_name": "XREF_USE_LAYOUT_DEVICE_NAME",
    "extract_via_caps": "EXTRACT_VIA_CAPS",
    "remove_floating_nets": "REMOVE_FLOATING_NETS",
    "remove_dangling_nets": "REMOVE_DANGLING_NETS",
    "skip_cells": "SKIP_CELLS",
    "translate_retain_bulk_layers": "TRANSLATE_RETAIN_BULK_LAYERS",
}
_RESERVED = {
    "BLOCK", "CALIBRE_RUNSET", "CALIBRE_QUERY_FILE", "CASE_SENSITIVE",
    "HIERARCHICAL_SEPARATOR", "TCAD_GRD_FILE", "MAPPING_FILE", "OPERATING_TEMPERATURE",
    "SIMULTANEOUS_MULTI_CORNER", "CORNERS_FILE", "SELECTED_CORNERS", "EXTRACTION",
    "COUPLING_REPORT_FILE", "XREF", "CELL_TYPE", "NET_TYPE", "NETLIST_FORMAT",
    "NETLIST_FILE", "SPICE_SUBCKT_FILE", "NETS", "NETS_FILE", "SKIP_CELLS_FILE",
    "INCLUDE_FILE", "3D_IC", "3D_IC_SUBCKT_FILE", "POWER_REDUCTION",
    "NETLIST_TAIL_COMMENTS", "NETLIST_CONNECT_SECTION", "NETLIST_NODE_SECTION",
    "EXTRA_GEOMETRY_INFO", "KEEP_VIA_NODES", "CAPACITOR_TAIL_COMMENTS",
    "NETLIST_UNSCALED_COORDINATES", "NETLIST_UNSCALED_RES_PROP",
    "COUPLING_ABS_THRESHOLD", "COUPLING_REL_THRESHOLD", "COUPLING_THRESHOLD_OPERATION",
    "NETLIST_MINRES_HANDLING", "NETLIST_MINRES_THRESHOLD",
}


class StarDefaults:
    def __init__(self, defaults: Defaults, cfg):
        self.source = defaults.source("starrc.options")
        for field, command in MIGRATED_FIELDS.items():
            if field in cfg.section("extract", "starrc"):
                raise ValueError(
                    f"extract.starrc.{field} moved to {self.source.path}: "
                    f"set {command} there and remove the old TOML field"
                )
        self.groups = sections(
            self.source, {"extraction", "coupling", "xref", "database", "after_selection", "netlist.file"},
            comments=("*", "#"),
        )
        seen = set()
        for section, records in self.groups.items():
            for number, line in records:
                match = re.fullmatch(r"([A-Z][A-Z0-9_]*)\s*:\s*(\S.*)", line)
                if not match or any(char in line for char in (";", "$", "`", "{", "}")):
                    raise self.source.error(number, "Expected a literal StarRC KEY: value")
                key = match[1]
                if key in _RESERVED or key.startswith("OA_") or key in seen:
                    raise self.source.error(number, f"GUI/flow-owned or repeated command {key}")
                required_section = {"REDUCTION": "extraction", "COUPLE_TO_GROUND": "coupling"}.get(key)
                if required_section and section != required_section:
                    raise self.source.error(number, f"{key} belongs in [{required_section}]")
                seen.add(key)

    def value(self, key: str) -> str | None:
        for records in self.groups.values():
            for _, line in records:
                name, _, value = line.partition(":")
                if name.strip() == key:
                    return value.strip()
        return None

    def lines(self, section: str, *, force_unreduced: bool = False) -> list[str]:
        result = []
        replaced = False
        for _, line in self.groups.get(section, ()):
            if force_unreduced and line.partition(":")[0].strip() == "REDUCTION":
                result.extend(("REDUCTION: NO", "POWER_REDUCTION: NO"))
                replaced = True
            else:
                result.append(line)
        if force_unreduced and not replaced:
            result.extend(("REDUCTION: NO", "POWER_REDUCTION: NO"))
        return result
