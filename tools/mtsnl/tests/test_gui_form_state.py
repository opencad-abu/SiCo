from __future__ import annotations

from mtsnetlistor.gui.form_state import (
    duplicate_row,
    move_row,
    optional_number,
    parse_enum_values,
    remove_rows,
)


def test_row_edit_helpers_preserve_order_and_do_not_mutate_input() -> None:
    source = ("core", "io", "rf")
    assert move_row(source, 1, -1) == ("io", "core", "rf")
    assert move_row(source, 0, -1) == source
    assert move_row(source, 2, 10) == source
    assert duplicate_row(source, 1) == ("core", "io", "io", "rf")
    assert duplicate_row(source, 99) == source
    assert remove_rows(source, (0, 2, 99)) == ("io",)
    assert source == ("core", "io", "rf")


def test_enum_parser_strips_empty_values_and_deduplicates() -> None:
    assert parse_enum_values(" trap, gear,trap,, ") == ("trap", "gear")
    assert parse_enum_values("") == ()


def test_optional_number_uses_explicit_unset_sentinel() -> None:
    assert optional_number(-273.150001, unset=-273.150001) is None
    assert optional_number(-273.15, unset=-273.150001) == -273.15
    assert optional_number(0.0, unset=0.0) is None
