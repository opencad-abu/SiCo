"""Compatibility collector; case modules are not independently discovered."""

# ruff: noqa: F401 -- explicit historical pytest collection entry point

from skill_load_completion_cases import (
    test_flow_log_widget_tails_the_complete_launch_log,
    test_ipc_output_is_routed_to_tail_viewfiles_not_ciw,
    test_lvs_rve_accepts_archived_config_path,
    test_main_flow_launchers_do_not_print_full_commands_to_ciw,
    test_rce_completion_summary_is_native_versioned_and_wired,
    test_rce_parasitic_netlist_views_are_wired_to_ipc_completion,
    test_standard_forms_keep_their_shell_and_flow_logs_use_the_shared_logo,
    test_text_cellview_import_uses_registered_ddpi_view_type,
)
from skill_load_drc_cases import (
    test_drc_and_lvs_advanced_options_are_grouped_under_disclosures,
    test_drc_and_lvs_run_modes_and_rve_completion_are_wired,
    test_drc_lvs_and_rce_show_rule_file_label,
    test_drc_rule_environment_uses_file_name,
    test_drc_rule_select_is_wired_to_gui_and_backend,
    test_instance_section_label_and_default_are_correct,
    test_lvs_rule_environment_is_split_between_lvs_and_rce,
)
from skill_load_extraction_options_cases import (
    test_rce_netlist_customize_controls_follow_tool_and_output_support,
    test_rce_process_corners_are_scanned_and_preserve_case,
)
from skill_load_native_view_cases import (
    test_lvs_oa_input_names_can_be_synchronized_in_either_direction,
    test_rce_advanced_options_are_grouped_under_disclosures,
    test_rce_native_oa_views_are_wired_to_gui_toml_and_completion,
    test_rce_oa_input_names_can_be_synchronized_in_either_direction,
    test_rce_top_cell_and_name_source_gui_and_toml_are_wired,
)
from skill_load_paths_cases import (
    test_file_callbacks_and_gui_external_paths_use_shared_helpers,
    test_gui_writers_make_external_toml_paths_absolute,
    test_gui_writers_normalize_run_dir_before_writing_toml,
    test_standalone_gds_and_cdl_use_independent_project_roots,
    test_standalone_stage_options_are_wired_to_both_forms_and_toml,
)
from skill_load_reduction_cases import (
    test_rce_quantus_reduction_is_wired_across_gui_toml_and_completion,
)
from skill_load_registry_cases import (
    test_ciw_menu_is_initialized_before_custom_install,
    test_flow_environment_fallbacks_are_isolated,
    test_flow_loaders_load_the_widget_before_ipc_callbacks,
    test_flow_project_directories_do_not_cross_fallback,
    test_frontend_versions_force_updated_callbacks_to_reload,
    test_function_il_files_have_version_guards,
    test_il_global_functions_are_unique,
    test_profile_and_lef_adapter_do_not_accept_legacy_local_host,
)
from skill_load_rule_options_cases import (
    test_cdl_include_is_wired_to_rce_lvs_and_export_cdl,
    test_custom_svrf_is_shared_by_drc_lvs_and_rce,
    test_ignore_lvs_error_is_standalone_only_and_svdb_query_is_lvs_only,
    test_lvs_and_rce_virtual_connect_name_defaults_and_callbacks,
    test_lvs_case_sensitive_matching_is_enabled_by_default,
    test_lvs_hcell_gui_is_wired_to_rce_and_standalone_lvs,
)
