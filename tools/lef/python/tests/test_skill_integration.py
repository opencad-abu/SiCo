"""Compatibility collector; remove when historical test paths migrate."""

# ruff: noqa: F401 -- explicit pytest collection

from lef_skill_core_cases import (
    test_lef_frontend_backs_up_the_run_before_starting_ipc,
    test_lef_frontend_emits_batch_input_and_step_controls,
    test_lef_frontend_keeps_abstract_commands_out_of_virtuoso,
    test_lef_skill_sources_are_bounded_and_balanced,
)
from lef_skill_gui_cases import (
    test_lef_completion_summary_exposes_output_and_log_actions,
    test_lef_form_lifecycle_reuses_valid_forms_and_never_deletes_in_callbacks,
    test_lef_frontend_exposes_collapsed_bin_options_and_toml_loader,
    test_lef_more_options_use_aligned_grid_prompts,
)
from lef_skill_probe_cases import (
    test_lef_common_hardware_fields_with_dbaccess,
    test_lef_frontend_dbaccess_probe,
)
from lef_skill_profile_cases import (
    test_lef_profile_adapter_covers_business_fields_transactionally,
    test_lef_profile_collector_preserves_hidden_executable_state,
    test_lef_profile_failed_apply_restores_hidden_state_with_dbaccess,
)
