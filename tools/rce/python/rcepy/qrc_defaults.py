"""Read Quantus default option fragments and protect generated options."""

from __future__ import annotations

import re
import shlex

from caddefaults import Defaults, resolve_defaults
from caddefaults.syntax import sections, rows


_RESERVED = {
    "distributed_processing": {"multi_cpu"},
    "process_technology": {
        "technology_directory", "temperature", "technology_library_file",
        "technology_corner",
    },
    "capacitance": set(),
    "filter_cap": set(),
    "filter_coupling_cap": {"coupling_cap_threshold_absolute", "coupling_cap_threshold_relative"},
    "filter_res": {"min_res"},
    "extraction_setup": {"parasitic_blocking_device_cells_file"},
    "input_db": {
        "type", "run_name", "directory_name", "layer_map_file",
        "design_cell_name", "library_definitions_file",
    },
    "output_db": {
        "type", "output_xy", "include_parasitic_res_model_by_sub_conductor",
        "include_parasitic_res_length", "include_parasitic_res_width_drawn",
        "disable_instances", "pin_order_file", "cdl_out_map_directory", "view_name",
    },
    "output_setup": {"net_name_space", "directory_name", "file_name"},
    "log_file": {"file_name"},
    "extract": {"selection", "type"},
}
_MODES = {
    "output_db.file", "output_db.smart_view", "output_db.extracted_view",
    "extraction_setup.block_cells", "process_technology.multi_corner",
}
_CONDITIONAL = {
    ("output_db", "hierarchy_delimiter"): "output_db.file",
    ("extraction_setup", "parasitic_blocking_device_cells_type"): "extraction_setup.block_cells",
    ("process_technology", "technology_name"): "process_technology.multi_corner",
}


class QrcDefaults:
    def __init__(self, defaults: Defaults):
        self.source = defaults.source("quantus.options")
        self.groups = sections(self.source, set(_RESERVED) | _MODES)
        for section, records in self.groups.items():
            command = section.split(".")[0]
            seen = set()
            for number, line in records:
                try:
                    tokens = shlex.split(line)
                except ValueError as exc:
                    raise self.source.error(number, str(exc)) from exc
                if (
                    len(tokens) < 2 or not re.fullmatch(r"-[a-z][a-z0-9_]*", tokens[0])
                    or any(char in line for char in (";", "$", "`", "[", "]", "{", "}", "\\"))
                    or any(token.startswith("-") and not re.fullmatch(r"-\d+(?:\.\d+)?", token)
                           for token in tokens[1:])
                ):
                    raise self.source.error(number, "Expected one literal Quantus option per line")
                key = tokens[0][1:]
                if key in _RESERVED[command] or key in {"include", "source", "cmd"} or key in seen:
                    raise self.source.error(number, f"GUI/flow-owned or repeated option {tokens[0]}")
                required_section = _CONDITIONAL.get((command, key))
                if required_section and section != required_section:
                    raise self.source.error(number, f"{tokens[0]} belongs in [{required_section}]")
                if command == "input_db" and key in {
                    "net_property_value", "instance_property_value", "device_property_value",
                } and (len(tokens) != 2 or not re.fullmatch(r"[0-9]+", tokens[1])):
                    raise self.source.error(number, f"{tokens[0]} requires an integer property number")
                if key == "technology_name" and (
                    len(tokens) != 2 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", tokens[1])
                ):
                    raise self.source.error(number, "Expected one technology library identifier")
                seen.add(key)

    def options(self, command: str, mode: str = "") -> list[str]:
        records = list(self.groups.get(command, ()))
        if mode:
            records.extend(self.groups.get(command + "." + mode, ()))
        result, seen = [], set()
        for number, line in records:
            key = line.split()[0]
            if key in seen:
                raise self.source.error(number, f"Common/mode defaults repeat {key}")
            seen.add(key)
            result.append(line)
        return result

    def mode_options(self, command: str, mode: str) -> list[str]:
        self.options(command, mode)  # Validate common/mode collisions together.
        return [line for _, line in self.groups.get(command + "." + mode, ())]

    def value(self, command: str, option: str, mode: str = "") -> str | None:
        """Read one literal value for a companion file from the same snapshot."""
        self.options(command, mode)
        records = [*self.groups.get(command, ()), *self.groups.get(command + "." + mode, ())]
        for number, line in records:
            tokens = shlex.split(line)
            if tokens[0] == "-" + option:
                if len(tokens) != 2 or not tokens[1]:
                    raise self.source.error(number, f"-{option} requires one nonempty value")
                return tokens[1]
        return None


def qrc_launch_arguments(defaults: Defaults | None = None) -> list[str]:
    source = resolve_defaults(("quantus.launch.args",), defaults).source("quantus.launch.args")
    args = [line for _, line in rows(source)]
    # Initial launch customization is deliberately restricted to license waiting.
    # All execution mode, command path and tool identity arguments stay generated.
    if not args:
        return []
    if len(args) != 2 or args[0] != "-lic_queue" or not args[1].isdigit():
        raise source.error(1, "Expected -lic_queue and a nonnegative integer on separate lines")
    return args
