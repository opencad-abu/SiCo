"""Compatibility collector for domain-specific cases; remove after test paths migrate."""

# ruff: noqa: F401 -- explicit historical pytest collection entry point

from selection_config_cases import (
    test_legacy_cell_selection_keys_are_ignored,
    test_legacy_net_selection_keys_are_ignored,
    test_names_or_file_sources_deduplicate_inline_names_in_order,
    test_names_or_file_sources_expand_files_comments_commas_and_duplicates,
)
from selection_qrc_cases import (
    test_qrc_converts_relative_capacitance_percent_to_ratio,
    test_qrc_generates_white_block_cell_file,
    test_qrc_generates_include_and_exclude_net_files,
)
from selection_starrc_cases import (
    test_starrc_converts_filter_units_and_emits_minres,
    test_starrc_generates_net_and_block_cell_selection_files,
    test_starrc_merges_foundry_and_user_block_cells_cumulatively,
    test_starrc_rejects_common_options_that_change_user_net_selection,
    test_starrc_rejects_nested_common_net_selection,
)
