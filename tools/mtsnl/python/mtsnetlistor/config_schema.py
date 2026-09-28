"""Accepted request/workspace TOML keys."""

from __future__ import annotations




ROOT_KEYS = frozenset({"format", "schema_version", "source", "simulator", "models", "process_options", "simulator_options", "cells", "publish", "corner_export", "temperature_mode"})


SOURCE_KEYS = frozenset({"cds_lib", "library", "cell", "view", "startup_file", "simrc"})


CELL_KEYS = frozenset({
    "library", "cell", "view", "dialect", "models", "process_options",
    "simulator_options", "publish", "corner_export", "temperature_mode",
})


SIMULATOR_KEYS = frozenset({"dialect"})


PROCESS_KEYS = frozenset({"temp", "tnom", "scale", "scalem", "reltol", "gmin"})


MODEL_KEYS = frozenset({"enabled", "file", "section", "label"})


OPTION_KEYS = frozenset({"enabled", "name", "value_type", "value", "enum_values"})


PUBLISH_KEYS = frozenset({
    "generate_symbol_view",
    "generate_netlist_view",
    "target_library",
    "target_cell",
    "overwrite",
    "overwrite_symbol_view",
    "overwrite_netlist_view",
})


WORKSPACE_ROOT_KEYS = frozenset({"format", "schema_version", "processes"})


WORKSPACE_PROCESS_KEYS = frozenset({
    "name", "project", "source", "simulator", "models", "process_options",
    "simulator_options", "cells", "publish", "presentation", "corner_export", "temperature_mode",
})


PRESENTATION_KEYS = frozenset({
    "library", "cell", "view", "temperature_text", "scale_text", "gmin_text",
})
