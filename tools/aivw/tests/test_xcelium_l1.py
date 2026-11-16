"""Compatibility collector for Xcelium L1 domain tests."""

from xcelium_l1_cases_events import (
    test_l1_event_evaluator_accepts_exact_machine_readable_evidence,  # noqa: F401
    test_l1_event_evaluator_rejects_duplicate_and_missing_evidence,  # noqa: F401
    test_l1_event_evaluator_rejects_non_finite_and_wrong_metrics,  # noqa: F401
    test_l1_event_evaluator_requires_exact_temporal_evidence,  # noqa: F401
    test_l1_event_evaluator_keeps_legacy_plan_compatible,  # noqa: F401
    test_l1_event_evaluator_requires_case_metadata_to_match_exactly,  # noqa: F401
    test_l1_event_evaluator_requires_coverage_and_waveform_artifacts,  # noqa: F401
    test_waveform_retention_policy_is_enforced,  # noqa: F401
)
from xcelium_l1_cases_execution import (
    test_xcelium_evidence_binding_is_required_and_exact,  # noqa: F401
    test_xrun_version_requires_one_unambiguous_identity,  # noqa: F401
    test_xcelium_rejects_generation_plan_tampering_before_subprocess,  # noqa: F401
)
