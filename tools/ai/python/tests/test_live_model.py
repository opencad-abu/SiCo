"""Collection entry for live-model source tests.

The case modules own protocol, lifecycle and result assertions. Shared fixtures
have one owner. Imported tests retain existing pytest node IDs and run once.
"""

# ruff: noqa: F401
from live_model_fixture import (
    TARGET,
    VIEW,
    assert_protocol_code,
    make_session,
    passing_result,
    sessions,
    source_message,
    start_message,
    wait_for,
)
from live_model_protocol_cases import (
    test_copy_boundary_rejects_secret_and_verdict_keys_in_nested_status_result,
    test_loads_strict_accepts_one_json_object,
    test_loads_strict_rejects_ambiguous_or_non_finite_json,
    test_start_protocol_rejects_unknown_non_text_secret_verdict_and_nan,
    test_target_view_binding_and_schematic_source_are_required,
)
from live_model_result_cases import (
    test_event_sink_disconnect_does_not_kill_worker_or_retry,
    test_history_is_bounded_to_128_independent_snapshots,
    test_malformed_runner_result_is_rejected_without_retry,
    test_publication_is_refused_without_publisher_and_can_be_cancelled,
    test_runner_arguments_and_public_snapshots_are_deeply_isolated,
    test_runner_failures_are_typed_and_fail_closed,
    test_runner_secrets_are_redacted_from_all_observable_boundaries,
    test_runner_verdict_claim_is_a_protocol_error,
    test_typed_provider_unavailable_code_is_preserved,
)
from live_model_session_cases import (
    test_active_session_cannot_be_reconfigured_without_stop,
    test_configured_identity_mismatch_and_duplicate_sequence_are_rejected,
    test_debounce_coalesces_to_the_latest_saved_source,
    test_newer_save_marks_late_result_stale_and_runs_latest_source,
    test_stop_discards_late_runner_result_and_emits_no_candidate,
    test_stop_then_restart_resets_sequence_and_configuration,
)
