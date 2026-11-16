"""Compatibility collector for L1 matrix domain tests."""

# The imported names are intentionally re-exported for pytest collection.
# ruff: noqa: F401

from l1_matrix_cases_contract import (
    test_exact_cross_product_is_normalized_and_auditable,
    test_matrix_rejects_incomplete_or_unsafe_observations,
    test_matrix_rejects_out_of_contract_values_and_undeclared_metadata,
    test_matrix_rejects_duplicate_dimension_values_and_too_many_points,
    test_matrix_numeric_keys_preserve_large_integer_coordinates,
    test_matrix_rejects_whitespace_only_dimension_values,
    test_recipe_schema_matrix_values_require_scalar_nonblank_strings,
    test_build_plan_keeps_legacy_recipe_without_matrix_unchanged,
)
from l1_matrix_cases_mapping import (
    test_comparator_adapter_maps_only_registered_numeric_model_parameters,
    test_comparator_adapter_rejects_unqualified_model_parameter_dimension,
    test_comparator_adapter_exposes_only_controlled_parameter_mapping,
    test_comparator_matrix_maps_all_qualified_parameters_and_rejects_non_finite,
    test_comparator_matrix_rejects_integer_coordinates_that_collapse_in_sv_real,
    test_comparator_adapter_reports_unrepresentable_matrix_parameter,
    test_comparator_matrix_rejects_non_object_contract,
    test_qualification_recipe_has_authoritative_16_point_matrix_and_two_instances,
)
from l1_matrix_cases_routes import (
    test_comparator_routes_reuse_instances_by_model_parameter_tuple,
    test_comparator_route_verifier_rejects_missing_or_forged_routes,
    test_comparator_route_verifier_rejects_route_field_tampering,
    test_comparator_route_verifier_rejects_unknown_case_and_duplicate_summary,
    test_comparator_route_verifier_requires_every_matrix_case_route,
    test_comparator_adapter_validate_plan_rejects_non_object_matrix,
    test_comparator_adapter_verify_outputs_returns_finding_for_invalid_plan,
    test_comparator_route_verifier_rejects_legacy_route_injection,
)
from l1_matrix_cases_runtime import (
    test_matrix_evidence_requires_runtime_metadata_and_summary_point_count,
    test_runtime_matrix_case_set_mismatch_is_stable,
    test_adapter_rejects_non_object_matrix_contract,
)
