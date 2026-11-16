"""Wire policy checks executable calls, without interpreting comments or data."""

import pytest

from cadai.skill_check import check_source


@pytest.mark.parametrize("entry", ["draw", "route"])
@pytest.mark.parametrize("method", ["direct", "full"])
@pytest.mark.parametrize("cv", ["cv", "getEditView()", "instance~>cellView"])
def test_physical_wire_methods_pass(entry, method, cv):
    source = f'schCreateWire({cv} "{entry}" "{method}" list(0:0 1:1) 0.0625 0.0625 0)'
    assert check_source(source.encode())["ok"]


@pytest.mark.parametrize(
    "source",
    [
        'schCreateWire(cv "route" "flight" points 0.0625 0.0625 0)',
        '(schCreateWire cv "draw" "flight" points 0.0625 0.0625 0)',
        'procedure(wire(cv) schCreateWire(cv "route" "flight" points 1 1 0))',
        'let(((cv getEditView())) schCreateWire(cv "draw" "flight" points 1 1 0))',
        'schCreateWire(cv "draw" routeMethod points 1 1 0)',
        'schCreateWire(cv "draw" if(flag "full" "flight") points 1 1 0)',
        'schCreateWire(cv "draw" "unknown" points 1 1 0)',
        'schCreateWire(cv "draw")',
        '`(data ,schCreateWire(cv "route" "flight" points 1 1 0))',
    ],
)
def test_unsupported_or_unresolved_methods_rejected(source):
    result = check_source(source.encode())
    assert not result["ok"] and not result["executed"]
    assert result["checks"]["wire_policy"] == "failed"
    assert result["errors"][0]["code"] == "wire_route_method_required"


@pytest.mark.parametrize(
    "source",
    [
        '; schCreateWire(cv "route" "flight" points 1 1 0)\nt',
        '/* schCreateWire(cv "route" "flight" points 1 1 0) */ t',
        '\'schCreateWire(cv "route" "flight" points 1 1 0)',
        'quote(schCreateWire(cv "route" "flight" points 1 1 0))',
        '`(schCreateWire cv "route" "flight" points 1 1 0)',
        "procedure(schCreateWire(cv entry method points xs ys width) t)",
    ],
)
def test_nonexecuted_forms_are_not_wire_calls(source):
    assert check_source(source.encode())["ok"]
