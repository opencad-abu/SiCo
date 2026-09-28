"""Compatibility collector; remove when historical test paths migrate."""

# ruff: noqa: F401 -- explicit pytest collection

from lsf_monitor_contract_cases import (
    test_lsf_monitor_selector_is_owned_validated_and_shared,
)
from lsf_monitor_frontend_cases import (
    test_flow_form_monitor_apply_cancel_and_stale_with_virtuoso,
    test_monitor_selected_host_survives_real_combo_callbacks,
)
from lsf_monitor_protocol_cases import (
    test_lsf_monitor_button_callback_with_dbaccess,
    test_lsf_monitor_selector_protocol_with_dbaccess,
)
