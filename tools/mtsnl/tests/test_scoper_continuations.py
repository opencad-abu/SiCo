from __future__ import annotations

import pytest

from mtsnetlistor.scoper.core import scope_netlist
from mtsnetlistor.scoper.lexer import logical_statements


@pytest.mark.parametrize("parenthesized", (False, True))
@pytest.mark.parametrize("newline", ("\n", "\r\n"))
@pytest.mark.parametrize("pin_format", (r"DATA\<{}\>", r"DATA\[{}\]"))
def test_many_continued_bus_ports_preserve_order_and_source(
    parenthesized: bool, newline: str, pin_format: str
) -> None:
    ports = ("VDD", "VSS") + tuple(pin_format.format(bit) for bit in range(127, -1, -1))
    rows = [" ".join(ports[index:index + 8]) for index in range(0, len(ports), 8)]
    port_text = (" \\  \n    ").join(rows)
    header = "subckt top " + (f"({port_text})" if parenthesized else port_text)
    raw = (header + "\nends top\n").replace("\n", newline)

    result = scope_netlist(raw, "spectre", "top")

    assert result.ports == ports
    assert result.report()["ports"] == list(ports)
    assert header in result.output
    assert result.warnings == ()
    again = scope_netlist(result.output, "spectre", "top")
    assert again.output == result.output
    assert again.ports == ports


@pytest.mark.parametrize("instance_name", ("X0", "X0<3>", r"X0\<3\>", "X0[3]", r"X0\[3\]"))
@pytest.mark.parametrize("wrap_at", ("none", "before_nodes", "in_nodes", "before_master"))
def test_bus_instances_and_continuations_keep_transitive_dependencies(
    instance_name: str, wrap_at: str
) -> None:
    separator = " \\\n    "
    before_nodes = separator if wrap_at == "before_nodes" else " "
    in_nodes = separator if wrap_at == "in_nodes" else " "
    before_master = separator if wrap_at == "before_master" else " "
    instance = (
        f"  {instance_name}{before_nodes}(DATA\\<1\\>{in_nodes}DATA\\<0\\>)"
        f"{before_master}mid"
    )
    raw = r"""subckt unused (a b)
ends unused
subckt leaf (a b)
ends leaf
subckt mid (a b)
  Xleaf\<0\> (a b) \
    leaf
ends mid
subckt top (DATA\<1\> DATA\<0\>)
""" + instance + "\nends top\n"

    result = scope_netlist(raw, "spectre", "top")

    assert result.dependencies == ("mid", "leaf")
    assert "subckt unused" not in result.output
    assert instance in result.output
    assert result.output.count("subckt mid") == 1
    assert result.output.count("subckt leaf") == 1
    assert result.warnings == ()
    again = scope_netlist(result.output, "spectre", "top")
    assert again.output == result.output
    assert again.dependencies == result.dependencies


@pytest.mark.parametrize("header", ("subckt top", "subckt top()", "subckt top ( )"))
def test_empty_spectre_port_list(header: str) -> None:
    result = scope_netlist(header + "\nends top\n", "spectre", "top")

    assert result.ports == ()
    assert header in result.output


def test_logical_text_preserves_escapes_and_source_spans() -> None:
    raw = (
        'include "models\\\\corner.scs" \\\n    section=tt\n'
        'parameters escaped=literal\\\\\n'
        'subckt top DATA\\<1\\> \\\n    DATA\\<0\\>\n'
        'ends top\n'
    )
    statements = logical_statements(raw, "spectre")

    assert [(item.start_line, item.end_line) for item in statements] == [
        (1, 2), (3, 3), (4, 5), (6, 6)
    ]
    assert "\n".join(item.text for item in statements) + "\n" == raw
    assert statements[0].parsed("spectre") == 'include "models\\\\corner.scs"      section=tt'
    assert statements[1].parsed("spectre") == 'parameters escaped=literal\\\\'
    assert statements[2].parsed("spectre") == r"subckt top DATA\<1\>      DATA\<0\>"


@pytest.mark.parametrize("parenthesized", (False, True))
def test_escaped_port_delimiters_are_part_of_the_pin_name(parenthesized: bool) -> None:
    ports = (r"DATA\(1\)", r"DATA\(0\)", r"net\,alias", r"net\ name")
    port_text = " \\\n    ".join(ports)
    header = "subckt top " + (f"({port_text})" if parenthesized else port_text)
    instance = r"  X\(0\) (DATA\(1\) DATA\(0\)) leaf"
    raw = "subckt leaf (a b)\nends leaf\n" + header + "\n" + instance + "\nends top\n"

    result = scope_netlist(raw, "spectre", "top")

    assert result.ports == ports
    assert result.dependencies == ("leaf",)
    assert result.warnings == ()
    assert header in result.output
    assert instance in result.output
