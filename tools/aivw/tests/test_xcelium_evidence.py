"""Compatibility collector for Xcelium evidence domain tests."""

from xcelium_evidence_cases_contract import (
    test_adapter_registry_is_exact_and_unknown_names_fail_closed,  # noqa: F401
    test_adapter_binding_is_exact_and_rejects_missing_extra_or_mismatched_fields,  # noqa: F401
    test_generic_binding_rejects_unsafe_paths,  # noqa: F401
    test_generic_binding_rejects_direct_and_intermediate_symlinks,  # noqa: F401
    test_adapter_rejects_unqualified_waveform_format_and_bad_contract_ids,  # noqa: F401
)
from xcelium_evidence_cases_executor import (
    test_imc_contract_is_recognized_but_preflight_is_always_blocking,  # noqa: F401
    test_executor_blocks_imc_before_binding_or_xrun_resolution,  # noqa: F401
    test_executor_blocks_physical_pvt_without_registered_adapter_before_subprocess,  # noqa: F401
    test_executor_requires_recipe_selected_adapter_to_match_candidate,  # noqa: F401
    test_adapter_preflight_rejects_preexisting_bound_output,  # noqa: F401
    test_adapter_preflight_rejects_stale_symlink_even_when_target_is_missing,  # noqa: F401
    test_adapter_preflight_rejects_existing_file_and_complete_old_evidence,  # noqa: F401
    test_adapter_preflight_rejects_file_at_bound_path,  # noqa: F401
    test_executor_blocks_stale_adapter_output_before_any_subprocess,  # noqa: F401
    test_executor_classifies_dangling_stale_output_before_binding_validation,  # noqa: F401
    test_adapter_controls_xcelium_arguments_and_retention_hooks,  # noqa: F401
    test_matrix_preparation_records_metadata_and_parameter_execution_scope,  # noqa: F401
)
from xcelium_evidence_cases_output import (
    test_output_verifier_requires_raw_ucd_and_ucm_and_rejects_parent_symlink,  # noqa: F401
    test_output_verifier_requires_complete_shm,  # noqa: F401
    test_output_verifier_enforces_clean_pass_and_never_retention,  # noqa: F401
    test_validated_evidence_locators_publish_and_resolve_from_split_manifest,  # noqa: F401
    test_legacy_manifest_without_locator_collection_remains_compatible,  # noqa: F401
)
