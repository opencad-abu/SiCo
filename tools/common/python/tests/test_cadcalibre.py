from __future__ import annotations

from pathlib import Path

import pytest

from cadcalibre.lvs import append_custom_svrf, hcell_arguments, virtual_connect_lines


def test_virtual_connect_lines_render_colon_and_quoted_names() -> None:
    assert virtual_connect_lines("(t t)", ('"VDD"', "VSS?")) == [
        "VIRTUAL CONNECT COLON YES",
        'VIRTUAL CONNECT NAME "VDD" "VSS?"',
    ]


def test_virtual_connect_lines_require_names_when_enabled() -> None:
    with pytest.raises(ValueError, match="no net-name pattern"):
        virtual_connect_lines("(nil t)")


def test_append_custom_svrf_preserves_verbatim_command() -> None:
    command = 'LVS FILTER UNUSED OPTION AB \\ path\n// "quoted"'
    assert append_custom_svrf(
        "LAYOUT SYSTEM GDSII\n", enabled=True, command=command
    ) == f"LAYOUT SYSTEM GDSII\n\n{command}\n"


def test_append_custom_svrf_ignores_disabled_command() -> None:
    assert append_custom_svrf(
        "base\n", enabled=False, command="CUSTOM\n"
    ) == "base\n"


def test_hcell_arguments_render_optional_path() -> None:
    assert hcell_arguments(None) == []
    assert hcell_arguments(Path("/tmp/hcells")) == ["-hcell", "/tmp/hcells"]
