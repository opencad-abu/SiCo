"""Make whitespace-bearing paths readable by StarRC's token-based parser."""

from __future__ import annotations

from .config_context import DesignContext
from .command_paths import command_path


_PATH_COMMANDS = frozenset("""
CALIBRE_RUNSET CALIBRE_QUERY_FILE TCAD_GRD_FILE MAPPING_FILE CORNERS_FILE
CALIBRE_OPTIONAL_DEVICE_PIN_FILE COUPLING_REPORT_FILE NETLIST_FILE SPICE_SUBCKT_FILE
3D_IC_SUBCKT_FILE NETS_FILE SKIP_CELLS_FILE OA_LIB_DEF OA_DEVICE_MAPPING_FILE
OA_LAYER_MAPPING_FILE OA_CDLOUT_RUNDIR
""".split())


def starrc_path_lines(ctx: DesignContext, lines: list[str]) -> list[str]:
    result = []
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and key.strip() in _PATH_COMMANDS:
            path = value.strip().strip('"')
            if any(char.isspace() for char in path):
                path = command_path(ctx, path, "star")
                line = f"{key}: {path}"
            elif value.strip().startswith('"'):
                line = f"{key}: {path}"
        result.append(line)
    return result
