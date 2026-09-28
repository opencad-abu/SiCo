"""Compatibility collector; remove after test paths migrate to the domain cases."""

# ruff: noqa: F401 -- explicit pytest collection

from dspf_skill_contract_cases import (
    test_dspf_bridge_protocol_and_process_are_bounded,
    test_dspf_bridge_uses_only_the_captured_layout_context,
    test_dspf_launcher_uses_a_native_chooser_without_oa_bridge,
    test_dspf_skill_sources_have_balanced_reader_delimiters,
    test_dspf_summary_and_loader_rebuild_and_wire_the_analyzer,
    test_rce_and_analyzer_inherit_the_shared_python,
)
from dspf_skill_protocol_cases import (
    test_dspf_skill_dbaccess_probe,
)
