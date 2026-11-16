"""Compatibility collector for CLI domain tests."""

from cli_cases_basic import (
    test_wrapper_is_named_aivw_and_reports_cwd_artifact_root,  # noqa: F401
    test_cli_detaches_inherited_mps_selectors,  # noqa: F401
    test_m1ai_run_requires_an_explicit_gate,  # noqa: F401
    test_m1ai_correlation_without_evidence_is_a_blocked_input,  # noqa: F401
    test_m1ai_evidence_gates_require_source_manifests,  # noqa: F401
    test_cli_lists_and_validates_generic_recipe,  # noqa: F401
)
from cli_cases_recipe_connectivity import (
    test_cli_rejects_generic_execution_until_handlers_are_bound,  # noqa: F401
    test_cli_rejects_publication_through_generic_execution,  # noqa: F401
    test_cli_verify_run_rejects_missing_manifests,  # noqa: F401
    test_cli_connectivity_checker_passes_a_complete_inventory,  # noqa: F401
)
