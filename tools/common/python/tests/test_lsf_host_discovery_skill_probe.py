"""Compatibility collector for LSF source contracts and opt-in probes."""

# ruff: noqa: F401 -- explicit historical pytest collection entry point

from lsf_skill_contract_cases import (
    test_shared_lsf_hardware_policy_is_wired_to_all_flows,
)
from lsf_skill_frontend_cases import (
    test_primary_flow_forms_default_to_locked_current_host,
    test_shared_lsf_helper_loads_with_flow_frontends,
)
from lsf_skill_process_cases import (
    test_lsf_host_discovery_callbacks_with_dbaccess,
    test_lsf_signal_result_semantics_with_dbaccess,
)
