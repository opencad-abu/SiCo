from __future__ import annotations

import os
import re
import shutil as shutil
import subprocess as subprocess
import sys
from pathlib import Path

import pytest as pytest
from cadprofile.model import load_profile as load_profile
from cadprofile.skill_data import render_form_data as render_form_data
from skill_test_support import read_skill_source

CAD_ROOT = Path(__file__).resolve().parents[3]
PROFILE_REVISION = "20260924.profile.environment.v4"
PROFILE_GUI_REVISION = "20260818.profile.gui.v2"
ADAPTER_PROFILE_REVISION = "20260820.rce.live.log.v4"
RCE_PROFILE_REVISION = "20260909.pin.order.v1"
RUN_SKILL_PROBE = os.environ.get("RCE_RUN_SKILL_PROBE") == "1"


def _profile_python() -> Path:
    candidate = Path("/software/pkgs/python/3.9.13/bin/python3")
    return candidate if candidate.is_file() else Path(sys.executable)

DRC_FIELDS = {
    "run.root_type",
    "run.root",
    "run.run_type",
    "run.queue_name",
    "run.server_name",
    "input.type",
    "input.layout.lib",
    "input.layout.cell",
    "input.layout.view",
    "drc.tool",
    "drc.runset_name",
    "drc.runset_file",
    "drc.run_mode",
    "drc.rule_select_enable",
    "drc.rule_select_groups",
    "drc.rule_select_checks",
    "drc.custom_svrf_enable",
    "drc.custom_svrf_command",
    "runtime.cpus",
    "batch.scope",
    "batch.parallel_cells",
    "batch.tasks",
}

LVS_FIELDS = {
    "run.root_type",
    "run.root",
    "run.run_type",
    "run.queue_name",
    "run.server_name",
    "input.type",
    "input.cdl_include_enable",
    "input.schematic.lib",
    "input.schematic.cell",
    "input.schematic.view",
    "input.schematic.cdl_header_file",
    "input.layout.lib",
    "input.layout.cell",
    "input.layout.view",
    "input.cdl.file",
    "input.cdl.cell",
    "input.gds.file",
    "input.gds.cell",
    "lvs.tool",
    "lvs.runset_name",
    "lvs.runset_file",
    "lvs.run_mode",
    "lvs.hcell_enable",
    "lvs.hcell_file",
    "lvs.ignore_error",
    "lvs.case_sensitive",
    "lvs.virtual_connect_enable",
    "lvs.virtual_connect_name_enable",
    "lvs.virtual_connect_names",
    "lvs.recognize_gates",
    "lvs.custom_svrf_enable",
    "lvs.custom_svrf_command",
    "lvs.svdb_query",
    "runtime.lvs_cpus",
    "batch.scope",
    "batch.parallel_cells",
    "batch.tasks",
}

RCE_FIELDS = {
    "run.root_type",
    "run.root",
    "run.run_type",
    "run.queue_name",
    "run.server_name",
    "input.type",
    "input.cdl_include_enable",
    "input.schematic.lib",
    "input.schematic.cell",
    "input.schematic.view",
    "input.schematic.cdl_header_file",
    "input.layout.lib",
    "input.layout.cell",
    "input.layout.view",
    "input.cdl.file",
    "input.cdl.cell",
    "input.cdl.run_directory",
    "input.gds.file",
    "input.gds.cell",
    "input.svdb.dir",
    "input.svdb.cell",
    "input.cci.dir",
    "input.cci.cell",
    "lvs.tool",
    "lvs.runset_name",
    "lvs.runset_file",
    "lvs.hcell_enable",
    "lvs.hcell_file",
    "lvs.ignore_error",
    "lvs.case_sensitive",
    "lvs.virtual_connect_enable",
    "lvs.virtual_connect_name_enable",
    "lvs.virtual_connect_names",
    "lvs.recognize_gates",
    "lvs.custom_svrf_enable",
    "lvs.custom_svrf_command",
    "extract.tool",
    "extract.tech_name",
    "extract.tech_dir",
    "extract.corner_scope",
    "extract.corner",
    "extract.corners",
    "extract.temperature",
    "extract.corner_temperatures",
    "extract.rc_type",
    "extract.top_cell_source",
    "extract.name_source",
    "extract.output_type",
    "extract.start_rve",
    "extract.view.kind",
    "extract.view.name",
    "extract.view.cellmap_file",
    "extract.view.device_mapping_file",
    "extract.view.layer_mapping_file",
    "reduction.enabled",
    "reduction.mode",
    "reduction.output_tag",
    "reduction.control",
    "reduction.delay_rel",
    "reduction.delay_abs",
    "reduction.frequency",
    "reduction.temperature",
    "reduction.ground",
    "reduction.reduce_negative",
    "reduction.selection_file",
    "reduction.canonical_device_file",
    "runtime.lvs_cpus",
    "runtime.ext_cpus",
    "selection.net_enable",
    "selection.net_type",
    "selection.nets",
    "selection.cell_enable",
    "selection.cells",
    "filter.cap_percentage_enable",
    "filter.cap_percentage",
    "filter.cap_value_enable",
    "filter.cap_value",
    "filter.res_value_enable",
    "filter.res_value",
    "netlist.create_view",
    "netlist.pin_order_enable",
    "netlist.pin_order_type",
    "netlist.pin_order_file",
    "netlist.brackets_replace",
    "netlist.brackets_replace_type",
    "netlist.hierarchy_delimiter_enable",
    "netlist.hierarchy_delimiter",
    "netlist.dspf_remove_instances",
    "netlist.parasitic_coordinates",
    "netlist.parasitic_res_layer",
    "netlist.parasitic_res_dimensions",
    "batch.scope",
    "batch.parallel_cells",
    "batch.tasks",
}


def _source(relative: str) -> str:
    return read_skill_source(CAD_ROOT / relative)


def _procedure(text: str, name: str) -> str:
    start = text.index(f"procedure({name}(")
    end = text.find("\nprocedure(", start + 1)
    return text[start:] if end < 0 else text[start:end]


def _collect_paths(body: str) -> set[str]:
    return set(re.findall(r'(?:list\(|\()"([a-z][a-z0-9_.]+)"', body))


def _assert_order(body: str, *fragments: str) -> None:
    positions = [body.index(fragment) for fragment in fragments]
    assert positions == sorted(positions)



__all__ = [name for name in globals() if not name.startswith('__')]
