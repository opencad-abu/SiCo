"""Compatibility collector for domain-specific cases; remove after test paths migrate."""

# ruff: noqa: F401 -- explicit historical pytest collection entry point

from qrc_netlist_format_cases import (
    test_disable_instances_is_only_emitted_for_dspf,
    test_disabled_hierarchy_delimiter_keeps_slash_default,
    test_dspf_explicitly_controls_qrc_instance_output,
    test_file_output_formats_emit_required_file_name,
    test_hierarchy_delimiter_only_changes_qrc_output_format,
    test_spef_accepts_ieee_hierarchy_delimiters,
    test_spef_rejects_non_ieee_hierarchy_delimiter,
)
from qrc_netlist_geometry_cases import (
    test_qrc_coordinates_rejects_extracted_view_output,
    test_qrc_coordinates_supports_documented_output_formats,
    test_qrc_parasitic_info_boolean_combinations,
    test_qrc_resistor_layer_and_dimensions_support_documented_output_formats,
)
from qrc_netlist_mapping_cases import (
    test_brackets_replace_does_not_emit_qrc_busbit_conversion,
    test_cdl_out_mapping_uses_generated_or_external_export_directory,
)
from qrc_netlist_pin_cases import (
    test_legacy_pin_order_key_is_ignored,
    test_user_pin_order_rejects_layout_namespace_for_file_netlist,
    test_user_pin_order_rejects_missing_or_unknown_user_file,
    test_user_pin_order_uses_native_qrc_option_in_schematic_namespace,
    test_view_output_forces_schematic_namespace_and_allows_pin_order,
)
