"""Compatibility collector; remove when historical source probe paths migrate."""

# ruff: noqa: F401 -- explicit pytest collection

from form_lifecycle_cases import test_flow_form_close_reopen_reuses_live_form
from form_reduction_cases import test_rce_reduction_form_tracks_output_capabilities
from form_view_import_cases import test_rce_imports_original_and_reduced_text_views
