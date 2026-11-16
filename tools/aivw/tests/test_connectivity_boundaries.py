"""Structural gate rejection and diagnostic contracts without external SI files."""

import pytest

from aivworkbench.connectivity import (
    ConnectivityParseError,
    Structure,
    canonicalize_module,
    compare_structures,
    parse_globalmap_models,
    parse_verilog,
)


@pytest.mark.parametrize("separator", [" ", "\n"])
def test_instance_bindings_after_declarations_cannot_be_omitted(separator):
    source = separator.join([
        "module top (a, y);", "input a;", "output y;",
        "leaf I0 (a, y);", "leaf I1 (a, y);", "endmodule",
        "module leaf (a, y);", "input a;", "output y;", "endmodule",
    ])
    official = Structure(parse_verilog(source))
    assert [item.name for item in official.modules[0].instances] == ["I0", "I1"]
    candidate = Structure(parse_verilog(source.replace("I1 (a, y)", "I1 (y, a)")))
    findings = compare_structures(official, candidate, expected_module="top")
    mismatches = [item for item in findings if item.code == "connection_mismatch"]
    assert len(mismatches) == 2


def test_child_findings_keep_detail_scope_and_order():
    official = Structure(
        parse_verilog("""module root (); endmodule
module child (input a, output y);
leaf I0 (.y(y));
endmodule
module leaf (output y); endmodule""")
    )
    candidate = Structure(
        parse_verilog("""module root (); endmodule
module child (output a, output y);
leaf I0 (.y(y));
leaf I1 (.y(y));
endmodule
module leaf (output y); endmodule""")
    )
    findings = [
        item.as_dict()
        for item in compare_structures(official, candidate, expected_module="root")
    ]
    assert [item["code"] for item in findings] == [
        "port_direction_mismatch",
        "extra_instance",
        "multiple_drivers",
    ]
    assert findings[0] == {
        "code": "port_direction_mismatch",
        "severity": "error",
        "scope": "child",
        "detail": "candidate module child port a direction differs",
        "expected": "input",
        "actual": "output",
        "terminal": "a",
    }
    # Preserve the established report scope even for a child driver conflict.
    assert findings[-1] == {
        "code": "multiple_drivers",
        "severity": "error",
        "scope": "top",
        "detail": "net y has multiple output drivers",
        "expected": 1,
        "actual": 2,
    }


def test_positional_fallback_still_reports_omitted_hierarchy():
    top = "module root (input a, output y);\nleaf I0 (a, y);\nendmodule"
    child = "module leaf (input a, output y); endmodule"
    findings = compare_structures(
        Structure(parse_verilog(top + "\n" + child)),
        Structure(parse_verilog(top)),
        expected_module="root",
    )
    assert [item.code for item in findings] == ["missing_module"]


def test_child_aliases_stay_scoped_to_the_declared_module():
    template = """module root (input a, output y);
child I0 (.a(a), .y(y));
endmodule
module child (input a, output y);
leaf I0 (.a({net}), .y(y));
endmodule
module leaf (input a, output y); endmodule"""
    official = Structure(parse_verilog(template.format(net="a")))
    candidate = Structure(parse_verilog(template.format(net="local_a")))
    assert (
        compare_structures(
            official,
            candidate,
            expected_module="root",
            module_aliases={"child": {"local_a": "a"}},
        )
        == ()
    )
    findings = compare_structures(
        official,
        candidate,
        expected_module="root",
        module_aliases={"root": {"local_a": "a"}},
    )
    assert [(item.code, item.scope) for item in findings] == [
        ("connection_mismatch", "child")
    ]


@pytest.mark.parametrize("args", [".a(a), y", "a, .y(y)"])
def test_mixed_named_and_positional_bindings_are_rejected(args):
    with pytest.raises(ConnectivityParseError, match="mixed named/positional"):
        parse_verilog(f"module root (a, y);\nleaf I0 ({args});\nendmodule")


@pytest.mark.parametrize(
    "args, message",
    [
        (".a(a), .a(y)", "duplicate terminal a on I0"),
        ("a, y, extra", "positional terminal 2 exceeds leaf port count"),
    ],
)
def test_ambiguous_terminal_bindings_are_rejected(args, message):
    modules = parse_verilog(
        f"module root (a, y);\nleaf I0 ({args});\nendmodule\n"
        "module leaf (input a, output y); endmodule"
    )
    with pytest.raises(ConnectivityParseError, match=message):
        canonicalize_module(modules[0], {m.name: m.ports for m in modules})


def test_globalmap_model_error_keeps_its_metadata_kind():
    with pytest.raises(ConnectivityParseError) as caught:
        parse_globalmap_models("$model\ninvalid\n$endmodel\n")
    assert str(caught.value) == "unrecognized globalmap model entry: invalid"


def test_escaped_ids_and_bus_slices_keep_their_bindings():
    modules = parse_verilog(
        "module root (input [3:0] bus, output y);\n"
        "leaf \\I/0  (.a(bus[2]), .y(y));\nendmodule\n"
        "module leaf (input a, output y); endmodule"
    )
    instance = canonicalize_module(modules[0], {m.name: m.ports for m in modules})[
        "instances"
    ][0]
    assert instance == {
        "name": "I/0",
        "master": "leaf",
        "connections": (("a", "bus[2]"), ("y", "y")),
    }
