"""Schema vocabulary and flow-specific profile constraints."""

from __future__ import annotations

import re
from typing import Union, Tuple

from cadconfig.booleans import FLOW_BOOLEAN_PATHS as _FLOW_BOOLEAN_PATHS
from cadconfig.scalars import INTEGER_TEXT_PATHS as _INTEGER_TEXT_PATHS

FORMAT_NAME = "sico-flow-profile"
SCHEMA_VERSION = 1
SUPPORTED_FLOWS = frozenset({"DRC", "LVS", "RCE", "LEF"})

ProfileScalar = Union[str, bool, int]
ProfileValue = Union[ProfileScalar, Tuple["ProfileValue", ...]]
ProfileEntry = Tuple[str, ProfileValue]

_METADATA_KEYS = frozenset({"format", "version", "flow"})
_ALLOWED_TOP_LEVEL = {
    "DRC": frozenset({"cad_config", "run", "input", "drc", "runtime", "batch"}),
    "LVS": frozenset(
        {"cad_config", "run", "input", "lvs", "runtime", "batch"}
    ),
    "RCE": frozenset(
        {
            "cad_config",
            "run",
            "input",
            "lvs",
            "extract",
            "reduction",
            "runtime",
            "selection",
            "filter",
            "netlist",
            "batch",
        }
    ),
    "LEF": frozenset(
        {"cad_config", "run", "input", "abstract", "steps", "output"}
    ),
}
_REQUIRED_TABLES = {
    "DRC": frozenset({"run", "input", "drc", "runtime"}),
    "LVS": frozenset({"run", "input", "lvs", "runtime"}),
    "RCE": frozenset({"run", "input", "lvs", "extract", "runtime"}),
    "LEF": frozenset({"run", "input", "abstract", "output"}),
}
_INPUT_TYPES = {
    "DRC": frozenset({"OA"}),
    "LVS": frozenset({"OA", "CDL+GDS", "CDL+LAY", "SCH+GDS"}),
    "RCE": frozenset(
        {"OA", "CDL+GDS", "CDL+LAY", "SCH+GDS", "SVDB", "CCI"}
    ),
}
_FLOW_ALLOWED_PATHS = {
    "DRC": frozenset(
        """
        run.root_type run.root run.run_type run.queue_name run.server_name
        input.type input.layout.lib input.layout.cell input.layout.view
        drc.tool drc.runset_name drc.runset_file drc.run_mode
        drc.rule_select_enable drc.rule_select_groups drc.rule_select_checks
        drc.custom_svrf_enable drc.custom_svrf_command runtime.cpus
        batch.scope batch.parallel_cells batch.tasks
        """.split()
    ),
    "LVS": frozenset(
        """
        run.root_type run.root run.run_type run.queue_name run.server_name
        input.type input.cdl_include_enable input.schematic.lib
        input.schematic.cell input.schematic.view
        input.schematic.cdl_header_file input.layout.lib input.layout.cell
        input.layout.view input.cdl.file input.cdl.cell input.gds.file
        input.gds.cell lvs.tool lvs.runset_name lvs.runset_file lvs.run_mode
        lvs.hcell_enable lvs.hcell_file lvs.ignore_error lvs.case_sensitive
        lvs.virtual_connect_enable lvs.virtual_connect_name_enable
        lvs.virtual_connect_names lvs.custom_svrf_enable
        lvs.recognize_gates lvs.custom_svrf_command lvs.svdb_query runtime.lvs_cpus batch.scope
        batch.parallel_cells batch.tasks
        """.split()
    ),
    "RCE": frozenset(
        """
        run.root_type run.root run.run_type run.queue_name run.server_name
        input.type input.cdl_include_enable input.schematic.lib
        input.schematic.cell input.schematic.view
        input.schematic.cdl_header_file input.layout.lib input.layout.cell
        input.cdl.run_directory input.layout.view input.cdl.file input.cdl.cell input.gds.file
        input.gds.cell input.svdb.dir input.svdb.cell input.cci.dir
        input.cci.cell lvs.tool lvs.runset_name lvs.runset_file
        lvs.hcell_enable lvs.hcell_file lvs.ignore_error lvs.case_sensitive
        lvs.virtual_connect_enable lvs.virtual_connect_name_enable
        lvs.virtual_connect_names lvs.custom_svrf_enable
        lvs.recognize_gates lvs.custom_svrf_command extract.tool extract.tech_name
        extract.tech_dir extract.corner_scope extract.corner extract.corners
        extract.temperature extract.corner_temperatures extract.rc_type
        extract.top_cell_source extract.name_source extract.output_type
        extract.start_rve extract.view.kind extract.view.name
        extract.view.library extract.view.cell
        extract.view.cellmap_file extract.view.device_mapping_file
        extract.view.layer_mapping_file reduction.enabled reduction.mode
        reduction.output_tag reduction.control reduction.delay_rel
        reduction.delay_abs reduction.frequency reduction.temperature
        reduction.ground reduction.reduce_negative reduction.selection_file
        reduction.canonical_device_file runtime.lvs_cpus runtime.ext_cpus
        selection.net_enable selection.net_type selection.nets
        selection.cell_enable selection.cells filter.cap_percentage_enable
        filter.cap_percentage filter.cap_value_enable filter.cap_value
        filter.res_value_enable filter.res_value netlist.create_view
        netlist.pin_order_enable netlist.pin_order_type
        netlist.pin_order_file netlist.brackets_replace
        netlist.brackets_replace_type netlist.hierarchy_delimiter_enable
        netlist.hierarchy_delimiter netlist.dspf_remove_instances
        netlist.parasitic_coordinates netlist.parasitic_res_layer
        netlist.parasitic_res_dimensions batch.scope batch.parallel_cells
        batch.tasks
        """.split()
    ),
    "LEF": frozenset(
        """
        run.root_type run.root run.cds_lib run.run_type run.queue_name
        run.server_name run.cpus run.executable
        input.library input.cell
        input.cells
        input.cell_list_file input.layout_view input.logical_view
        input.abstract_view abstract.options_file abstract.bin steps.pins
        steps.extract steps.abstract output.lef_file output.lef_version
        output.geometry output.technology
        """.split()
    ),
}
_FLOW_DYNAMIC_PATH_PREFIXES = {
    "DRC": (),
    "LVS": (),
    "RCE": (),
    "LEF": ("abstract.bin_options.",),
}
_ARRAY_PATHS = frozenset(
    {
        "batch.tasks",
        "drc.rule_select_groups",
        "drc.rule_select_checks",
        "extract.corners",
        "extract.corner_temperatures",
        "input.cells",
        "lvs.svdb_query",
    }
)
_GUI_TEXT_PATHS = {
    flow: frozenset(
        _FLOW_ALLOWED_PATHS[flow]
        - _FLOW_BOOLEAN_PATHS[flow]
        - _ARRAY_PATHS
        - _INTEGER_TEXT_PATHS
    )
    for flow in SUPPORTED_FLOWS
}
_FLOW_ENUM_VALUES = {
    "DRC": {
        "run.root_type": frozenset(
            {"Project Directory", "Current Directory", "Customize Directory"}
        ),
        "drc.tool": frozenset({"Calibre"}),
        "drc.run_mode": frozenset({"Hier", "Flat"}),
    },
    "LVS": {
        "run.root_type": frozenset(
            {"Project Directory", "Current Directory", "Customize Directory"}
        ),
        "lvs.tool": frozenset({"Calibre"}),
        "lvs.run_mode": frozenset({"Hier", "Flat"}),
        "lvs.recognize_gates": frozenset({"NONE", "ALL", "SIMPLE"}),
    },
    "RCE": {
        "run.root_type": frozenset(
            {"Project Directory", "Current Directory", "Customize Directory"}
        ),
        "lvs.tool": frozenset({"Calibre"}),
        "extract.tool": frozenset({"StarRC", "QRC", "CalXRC"}),
        "lvs.recognize_gates": frozenset({"NONE", "ALL", "SIMPLE"}),
        "extract.corner_scope": frozenset(
            {"Single Corner", "Multiple Corners"}
        ),
        "extract.rc_type": frozenset(
            {"R+Cg+Cc", "R+Cg", "Cg+Cc", "Cg", "R", "NONE", "Totem", "VoltusFi"}
        ),
        "extract.top_cell_source": frozenset({"Schematic", "Layout"}),
        "extract.name_source": frozenset({"Schematic", "Layout"}),
        "extract.view.kind": frozenset(
            {
                "Smart View",
                "Extracted View",
                "Calibre View",
                "OpenAccess Parasitic View",
            }
        ),
        "reduction.mode": frozenset(
            {
                "Default",
                "Reduction Control",
                "Delay and Frequency",
                "Selection File",
            }
        ),
        "selection.net_type": frozenset({"Include Nets", "Exclude Nets"}),
        "netlist.pin_order_type": frozenset(
            {"CDL Netlist File", "User Defined File"}
        ),
        "netlist.brackets_replace_type": frozenset(
            {"<> ===> []", "[] ===> <>"}
        ),
        "netlist.hierarchy_delimiter": frozenset({"/", "."}),
        "netlist.dspf_remove_instances": frozenset({"TRUE", "FALSE"}),
    },
    "LEF": {
        "run.root_type": frozenset(
            {"Project Directory", "Current Directory", "Customize Directory"}
        ),
        "abstract.bin": frozenset({"Core", "Block", "IO", "Corner"}),
        "output.lef_version": frozenset({"5.8", "5.7", "5.6", "5.5"}),
    },
}
_BIN_OPTION_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_REDUCTION_OUTPUT_TAG = re.compile(r"[A-Za-z0-9_]+\Z")
