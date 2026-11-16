"""Compatibility collection entry for controller source tests.

Case modules own the tests; the fixture module owns their shared transport and
runtime setup. Keep this entry until supported test commands and fixture imports
migrate to the owning modules.
"""

# Imported tests are collected here exactly once; case modules are not test_*.
# Keep FakeTransport/runtime available to the existing transport-test consumers.
# ruff: noqa: F401

from controller_transport_fixture import FakeTransport, runtime

from controller_request_cases import (
    test_eval_materializes_controller_owned_source,
    test_all_virtuoso_request_paths_use_shared_spool_while_socket_stays_local,
    test_eval_consumes_inbound_and_controller_spools,
    test_relative_load_is_resolved_from_workspace,
    test_relative_load_cannot_escape_workspace,
    test_skill_fifo_is_rejected_without_waiting_for_a_writer,
    test_timeout_is_sent_once_and_never_retried,
    test_transport_disconnect_preserves_runtime,
    test_malformed_transport_response_preserves_runtime,
    test_non_object_result_is_removed_from_shared_bridge,
    test_large_result_returns_spool_reference,
    test_large_result_local_copy_failure_cleans_shared_bridge,
)

from controller_snapshot_cases import (
    test_snapshot_materializes_private_jsonl_artifact,
    test_snapshot_rejects_virtuoso_artifact_outside_spool,
    test_snapshot_rejects_symlink_without_deleting_its_target,
    test_snapshot_skill_failure_cleans_partial_artifact,
    test_snapshot_invalid_inner_metadata_cleans_partial_artifact,
    test_snapshot_binding_reads_the_serialized_gate_outputs_contract,
    test_snapshot_binding_rejects_summary_only_identity,
)

from controller_launch_cases import (
    test_codex_command_enables_mcp_without_legacy_sandbox_or_config_write,
    test_codex_verification_profile_is_read_only,
    test_codex_verification_profile_only_enables_bounded_tools,
    test_session_selects_launch_temp_as_shared_spool_base,
    test_session_uses_launch_cwd_when_workspace_differs,
)
