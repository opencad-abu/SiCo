"""Read-only RNM CLI contract and its call-time orchestration dependencies."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_rnm

SCRIPT = Path(check_rnm.__file__)
MODEL = """`timescale 1ns/1ps
module model(input wreal vin, output wreal vout);
assign vout = vin;
endmodule
"""


@pytest.mark.parametrize("strict,expected_code", [(False, 0), (True, 1)])
def test_cli_order_warning_is_read_only(tmp_path, strict, expected_code):
    source = tmp_path / "model.sv"
    source.write_text(MODEL)
    netlist = tmp_path / "netlist.json"
    netlist.write_text(json.dumps({"cells": [
        {"name": "other", "ports": []},
        {"name": "MODEL", "ports": ["vout", "vin"]},
    ]}))
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"ports": {"vin": {"kind": "analog"}}}))
    before = {p: p.read_bytes() for p in (source, netlist, spec)}
    command = [
        sys.executable, str(SCRIPT), str(source), "--netlist-json", str(netlist),
        "--cell", "model", "--spec", str(spec), "--format", "json",
    ]
    if strict:
        command.append("--fail-on-warnings")
    result = subprocess.run(command, capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == expected_code
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["module"]["ports"] == ["vin", "vout"]
    assert report["counts"] == {"error": 0, "warning": 1, "info": 0}
    assert report["findings"][0]["category"] == "port-order"
    assert {p: p.read_bytes() for p in before} == before
    assert set(tmp_path.iterdir()) == set(before)


@pytest.mark.parametrize(
    "kind,diagnostic",
    [
        ("missing", "cannot read"),
        ("no-module", "no module declaration found"),
        ("bad-netlist", "cannot use netlist JSON"),
        ("multiple-cells", "pass --cell"),
        ("missing-cell", "not present"),
        ("bad-spec", "cannot read spec"),
    ],
)
def test_cli_rejects_unusable_inputs(tmp_path, kind, diagnostic):
    source = tmp_path / "model.sv"
    if kind != "missing":
        source.write_text("// empty" if kind == "no-module" else MODEL)
    args = []
    if kind in {"bad-netlist", "multiple-cells", "missing-cell"}:
        netlist = tmp_path / "netlist.json"
        netlist.write_text("invalid" if kind == "bad-netlist" else json.dumps(
            {"cells": [{"name": "a"}, {"name": "b"}]}
        ))
        args = ["--netlist-json", str(netlist)]
        if kind == "missing-cell":
            args += ["--cell", "absent"]
    if kind == "bad-spec":
        spec = tmp_path / "spec.json"
        spec.write_text("{")
        args = ["--spec", str(spec)]
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), *args],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode == 2
    assert diagnostic in result.stderr and not result.stdout
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_main_preserves_call_time_checker_hooks_and_report_order(tmp_path, monkeypatch, capsys):
    source = tmp_path / "model.sv"
    source.write_text(MODEL)
    netlist = tmp_path / "netlist.json"
    netlist.write_text('{"cells": [{"ports": ["vin", "vout"]}]}')
    spec = tmp_path / "spec.json"
    spec.write_text('{}')
    facts = check_rnm.parse_module(MODEL)
    calls = []

    def parse(text):
        assert text == MODEL
        calls.append("parse")
        return facts

    def ports(module, names):
        assert module is facts and names == ["vin", "vout"]
        calls.append("ports")
        return [check_rnm.Finding("warning", "ports", "vin", "ports hook")]

    def spec_check(module, value):
        assert module is facts and value == {}
        calls.append("spec")
        return [check_rnm.Finding("info", "spec", "model", "spec hook")]

    def style(module, text):
        assert module is facts and text == MODEL
        calls.append("style")
        return [check_rnm.Finding("error", "style", "model", "style hook")]

    def compile_check(path, extra_args):
        assert path == source and extra_args == ["-ams"]
        calls.append("compile")
        return [check_rnm.Finding("error", "compile", "model", "compile hook")]

    for name, callback in (
        ("parse_module", parse), ("check_ports", ports), ("check_spec", spec_check),
        ("check_modeling_style", style), ("compile_check", compile_check),
    ):
        monkeypatch.setattr(check_rnm, name, callback)
    code = check_rnm.main([
        str(source), "--netlist-json", str(netlist), "--spec", str(spec),
        "--compile", "--compile-arg=-ams", "--format", "json",
    ])
    assert code == 1 and calls == ["parse", "ports", "spec", "style", "compile"]
    report = json.loads(capsys.readouterr().out)
    assert report["counts"] == {"error": 2, "warning": 1, "info": 1}
    assert [f["category"] for f in report["findings"]] == ["compile", "style", "ports", "spec"]
