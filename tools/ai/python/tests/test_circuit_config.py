"""Bounded new config contract and explicit top-level binding serialization."""

import pytest
from test_circuit_tools import Client, call

from cadai.circuit_config import checked_config
from cadai.circuit_tools import CircuitArgumentError, build_circuit_skill
from cadai.mcp import McpServer

SPEC = dict(
    library="work",
    cell="tb",
    top_library="work",
    top_cell="tb",
    task_ref="task:one",
    request_id="cfg1",
)
BIND = dict(instance="I_inv_0", library="work", cell="inv", view="schematic")


def test_default_spectre_config_and_explicit_instance_name():
    args = {**SPEC, "bindings": [BIND]}
    normalized = checked_config("create_circuit_config", args)
    assert normalized["view"] == "config" and normalized["top_view"] == "schematic"
    code = build_circuit_skill("create_circuit_config", args)
    assert (
        code
        == 'aiConfigCreate("task:one" "cfg1" "work" "tb" "config" '
        + 'list("work" "tb" "schematic") list(list("I_inv_0" "work" "inv" "schematic")) '
        + 'list("spectre" "schematic") list("spectre"))'
    )
    client = Client()
    assert call(McpServer(client), "create_circuit_config", args)["result"]["isError"] is False
    assert client.calls == [("eval_skill_native", {"code": code})]
    assert (
        build_circuit_skill("inspect_circuit_config", {"config_ref": "config:cfg1"})
        == 'aiConfigInspect("config:cfg1")'
    )


@pytest.mark.parametrize(
    "update",
    [
        {"bindings": "bad"},
        {"bindings": [BIND] * 65},
        {"bindings": [BIND, BIND]},
        {"bindings": [{**BIND, "instance": "I/a"}]},
        {"bindings": [{**BIND, "foo": 1}]},
        {"bindings": [{**BIND, "view": "config"}]},
        {"bindings": [{"instance": "I0"}]},
        {"view": "a b"},
        {"library": "gpdk045"},
        {"overwrite": True},
        {"top_view": "layout"},
        {"request_id": "x)exit("},
        {"task_ref": False},
    ],
)
def test_invalid_config_never_reaches_virtuoso(update):
    with pytest.raises(CircuitArgumentError):
        build_circuit_skill("create_circuit_config", {**SPEC, **update})


def test_missing_fields_and_no_topology_in_inspect():
    with pytest.raises(CircuitArgumentError):
        build_circuit_skill("create_circuit_config", {})
    with pytest.raises(CircuitArgumentError):
        build_circuit_skill("inspect_circuit_config", {"config_ref": "config:one", "bindings": []})
