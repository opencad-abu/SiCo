"""Explicit renames, incomplete references and bounded template input."""

import copy

import pytest
from template_circuit_fixture import persisted_case

from cadai.circuit_generic import checked_preview
from cadai.circuit_spec_schema import CircuitSpecError
from cadai.template_circuit import prepare_template
from cadai.template_schema import TemplateUnavailable


@pytest.mark.parametrize(
    "case",
    [
        "parameters_missing",
        "missing_device",
        "duplicate_source",
        "duplicate_target",
        "unknown_source",
        "merge_terminals",
        "unknown_terminal",
        "merge_nets",
        "global_rename",
        "local_to_global",
        "alias_unresolved",
        "port_direction_field",
        "opaque_object",
        "oversized",
        "unknown_field",
        "extra_unconnected",
    ],
)
def test_ambiguous_or_unsupported_selection_fails(tmp_path, monkeypatch, case):
    args, record = persisted_case(tmp_path, monkeypatch)
    device = args["devices"][0]
    nets = {n["source_name"]: n["id"] for n in record["topology"]["nets"]}
    if case == "parameters_missing":
        device.pop("parameters")
    elif case == "missing_device":
        args["devices"].pop()
    elif case == "duplicate_source":
        args["devices"].append(copy.deepcopy(device))
    elif case == "duplicate_target":
        args["devices"][1]["instance"] = device["instance"]
    elif case == "unknown_source":
        device["source_device"] = "d99"
    elif case == "merge_terminals":
        device["terminal_map"]["G"] = "D"
    elif case == "unknown_terminal":
        device["terminal_map"]["ZZ"] = "G"
    elif case == "merge_nets":
        args["net_map"][nets["out"]] = "A"
    elif case == "global_rename":
        args["net_map"][nets["vdd!"]] = "SUPPLY!"
    elif case == "local_to_global":
        args["net_map"][nets["in"]] = "IN!"
    elif case == "alias_unresolved":
        args.pop("port_map")
    elif case == "port_direction_field":
        args["port_directions"] = {"VDD": "input"}
    elif case == "opaque_object":
        device["parameters"]["width"] = {"evaluate": "source.width"}
    elif case == "oversized":
        device["parameters"]["width"] = "x" * 200000
    elif case == "unknown_field":
        args["overwrite"] = True
    else:
        device["unconnected"] = {"G": "would disconnect this pin"}
    with pytest.raises(CircuitSpecError):
        prepare_template(args, workspace=tmp_path)


def test_explicit_terminal_permutation_is_recorded_not_inferred(tmp_path, monkeypatch):
    args, _ = persisted_case(tmp_path, monkeypatch)
    args["devices"][0]["terminal_map"].update(D="S", S="D")
    prepared = prepare_template(args, workspace=tmp_path)
    target = prepared["spec"]["instances"][0]
    assert target["connections"]["S"] == "Y" and target["connections"]["D"] == "gnd!"
    assert prepared["template_use"]["devices"][0]["terminal_map"]["D"] == "S"
    assert not prepared["plan"]["template_evidence"]["device_equivalence_verified"]


def test_single_bit_requires_known_global_capture_semantics(tmp_path, monkeypatch):
    def change(rows):
        rows[0].pop("net_global_semantics")
        for row in rows:
            for key in ("name", "net"):
                if row.get(key) == "in":
                    row[key] = "DATA<0>"

    args, _ = persisted_case(tmp_path, monkeypatch, change)
    args["port_map"]["A"] = "DATA<0>"
    with pytest.raises(TemplateUnavailable, match="recapture bus member"):
        prepare_template(args, workspace=tmp_path)


def test_expanded_single_bit_preserves_bit_net_name(tmp_path, monkeypatch):
    def change(rows):
        for row in rows:
            for key in ("name", "net"):
                if row.get(key) == "in":
                    row[key] = "DATA<0>"

    args, _ = persisted_case(tmp_path, monkeypatch, change)
    args["port_map"]["A"] = "DATA<0>"
    result = prepare_template(args, workspace=tmp_path)
    assert result["spec"]["instances"][0]["connections"]["G"] == "DATA<0>"


def test_template_reference_and_null_use_fail_explicitly(tmp_path, monkeypatch):
    args, _ = persisted_case(tmp_path, monkeypatch)
    prepared = prepare_template(args, workspace=tmp_path)
    request = {"spec": prepared["spec"], "bindings": args["bindings"], "template_use": None}
    with pytest.raises(CircuitSpecError):
        checked_preview(request, workspace=tmp_path)
    args["template_ref"] = "tpl_" + "0" * 64
    with pytest.raises(TemplateUnavailable, match="version not found"):
        prepare_template(args, workspace=tmp_path)


def test_replay_input_budget_counts_mapping(tmp_path, monkeypatch):
    args, _ = persisted_case(tmp_path, monkeypatch)
    prepared = prepare_template(args, workspace=tmp_path)
    use = prepared["template_use"]
    use["unexpected_large_mapping"] = "x" * 200000
    with pytest.raises(CircuitSpecError, match="192 KiB"):
        checked_preview(
            {"spec": prepared["spec"], "bindings": args["bindings"], "template_use": use},
            workspace=tmp_path,
        )
