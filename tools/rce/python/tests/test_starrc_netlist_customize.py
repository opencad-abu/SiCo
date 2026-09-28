"""Compatibility collector for domain-specific cases; remove after test paths migrate."""

# ruff: noqa: F401 -- explicit historical pytest collection entry point

from starrc_netlist_format_cases import (
    test_disabled_hierarchy_delimiter_keeps_starrc_slash_default,
    test_starrc_accepts_documented_hierarchy_separators,
    test_starrc_emits_supported_parasitic_netlist_formats,
    test_starrc_rejects_ordinary_spice_output,
    test_starrc_rejects_unsupported_hierarchy_separator,
)
from starrc_netlist_geometry_cases import (
    test_starrc_legacy_addon_info_uses_parasitic_info_mapping,
    test_starrc_maps_all_parasitic_info_combinations_for_spf,
    test_starrc_rejects_incomplete_rc_coordinates_for_spef,
    test_starrc_spef_supports_resistor_layer_and_dimensions,
)
from starrc_netlist_override_cases import (
    test_starrc_allows_equivalent_required_settings_at_end_of_common_options,
    test_starrc_rejects_common_options_that_override_required_geometry,
    test_starrc_rejects_common_options_that_reduce_required_via_nodes,
    test_starrc_rejects_common_output_semantics_that_drop_requested_detail,
    test_starrc_rejects_cyclic_common_includes_for_required_settings,
    test_starrc_rejects_required_setting_overridden_by_common_include,
)
from starrc_netlist_pin_cases import (
    test_starrc_allows_equivalent_common_pin_file_path,
    test_starrc_disabled_pin_order_preserves_foundry_setting,
    test_starrc_pin_order_rejects_common_output_format_override,
    test_starrc_rejects_common_pin_file_override,
    test_starrc_rejects_missing_user_pin_order_file,
    test_starrc_rejects_unsupported_pin_order_source,
    test_starrc_rejects_user_pin_order_for_spef,
    test_starrc_user_pin_order_accepts_existing_user_file,
    test_starrc_user_pin_order_uses_input_cdl_for_dspf,
)
