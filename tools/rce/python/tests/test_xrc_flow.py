"""Compatibility collector for domain-specific cases; remove after test paths migrate."""

# ruff: noqa: F401 -- explicit historical pytest collection entry point

from xrc_flow_execution_cases import (
    test_mock_calibre_xrc_runs_serially_and_creates_artifacts,
    test_runner_plans_exact_xrc_commands_and_prefers_mgc_home,
    test_start_rve_is_enabled_only_for_xrc,
    test_xrc_enabled_stages_own_lvs_and_skip_query,
    test_xrc_start_rve_uses_svdb_and_detaches_from_ipc,
)
from xrc_flow_modes_cases import (
    test_xrc_no_rc_maps_the_three_rce_output_formats,
    test_xrc_no_rc_rejects_formats_outside_the_rce_contract,
    test_xrc_no_rc_rejects_parasitic_only_options,
    test_xrc_no_rc_uses_simple_formatter_without_pdb,
    test_xrc_rc_mode_selects_pdb_and_formatter_flags,
)
from xrc_flow_netlist_cases import (
    test_xrc_bracket_replacement_uses_native_character_map,
    test_xrc_capacitance_only_rejects_unavailable_capacitor_locations,
    test_xrc_netlist_formats_select_svrf_format_and_suffix,
    test_xrc_parasitic_info_flag_combinations,
    test_xrc_rejects_formats_outside_the_rce_contract,
    test_xrc_rejects_unknown_bracket_replacement,
    test_xrc_supported_formats_emit_all_parasitic_info_options,
    test_xrc_translates_filter_selection_and_netlist_options,
)
from xrc_flow_runset_cases import (
    test_generate_xrc_runset_contains_required_svrf,
    test_xrc_appends_custom_svrf_without_modifying_foundry_deck,
    test_xrc_case_matching_defaults_to_strict_and_can_be_disabled,
    test_xrc_name_domain_does_not_rekey_layout_artifacts,
    test_xrc_rejects_missing_lvs_rule_file,
    test_xrc_rejects_one_file_used_as_lvs_and_xrc_decks,
    test_xrc_requires_lvs_rule_file,
)
