"""Command dispatch, structured output and failure exits for mts-netlistor."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mtsnetlistor import cli


@pytest.fixture(autouse=True)
def detach_without_touching_the_parent(monkeypatch):
    monkeypatch.setattr(cli, "detach_mps_environment", lambda _environment: None)


def test_parser_exposes_the_documented_commands_and_requires_scope_options():
    parser = cli.build_parser()
    assert parser.parse_args(["validate", "request.toml"]).command == "validate"
    assert parser.parse_args(["catalog", "--source-cdslib", "cds.lib"]).command == "catalog"
    assert parser.parse_args([
        "scope", "--dialect", "spectre", "--top", "top", "raw.scs",
    ]).command == "scope"
    with pytest.raises(SystemExit) as missing:
        parser.parse_args([])
    assert missing.value.code == 2
    with pytest.raises(SystemExit) as invalid:
        parser.parse_args(["scope", "--dialect", "spice", "raw.scs"])
    assert invalid.value.code == 2


def test_catalog_dispatches_to_the_source_provider(tmp_path, capsys, monkeypatch):
    payload = {"libraries": [{"name": "work"}]}
    result = SimpleNamespace(
        catalog=SimpleNamespace(to_dict=lambda: payload),
        authoritative=False,
        provider="filesystem-preview",
        diagnostics=["fallback requested"],
    )
    calls = []

    def load(path, **kwargs):
        calls.append((path, kwargs))
        return result

    monkeypatch.setattr(cli, "load_source_catalog", load)
    source = tmp_path / "cds.lib"
    assert cli.main([
        "catalog", "--source-cdslib", str(source), "--fallback", "--json",
    ]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert calls == [(source, {"dbaccess": "dbAccess", "allow_fallback": True})]
    assert printed == {
        "libraries": [{"name": "work"}],
        "authoritative": False,
        "provider": "filesystem-preview",
        "diagnostics": ["fallback requested"],
    }


def test_validate_prints_the_canonical_request(tmp_path, capsys, monkeypatch):
    from mtsnetlistor import config

    request = object()
    monkeypatch.setattr(config, "load_request", lambda _path: request)
    monkeypatch.setattr(config, "request_to_dict", lambda _request: {"format": "request"})
    monkeypatch.setattr(config, "canonical_request_digest", lambda _request: "sha256:abc")
    path = tmp_path / "request.toml"
    assert cli.main(["validate", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "format": "request",
        "request_digest": "sha256:abc",
    }


def test_scope_writes_output_and_report_and_rejects_empty_input(tmp_path, capsys, monkeypatch):
    raw = tmp_path / "raw.scs"
    raw.write_text("subckt top a\nends top\n", encoding="utf-8")
    output = tmp_path / "out" / "scoped.scs"
    report = tmp_path / "out" / "report.json"
    seen = []

    def scope(text, dialect, top):
        seen.append((text, dialect, top))
        return SimpleNamespace(output="scoped\n", report=lambda: {"cell": "top"})

    monkeypatch.setattr(cli, "scope_netlist", scope)
    assert cli.main([
        "scope", "--dialect", "spectre", "--top", "top", str(raw),
        "--output", str(output), "--report", str(report),
    ]) == 0
    assert seen == [("subckt top a\nends top\n", "spectre", "top")]
    assert output.read_text(encoding="utf-8") == "scoped\n"
    assert json.loads(report.read_text(encoding="utf-8")) == {"cell": "top"}
    assert json.loads(capsys.readouterr().out) == {"cell": "top"}

    empty = tmp_path / "empty.scs"
    empty.write_text("", encoding="utf-8")
    assert cli.main([
        "scope", "--dialect", "spectre", "--top", "top", str(empty),
    ]) == 1
    assert "cannot access raw netlist" in capsys.readouterr().err


def test_generate_selects_single_or_multi_cell_workflow(capsys, monkeypatch):
    from mtsnetlistor import config, workflow

    calls = []
    request = SimpleNamespace(cell_specs=[])

    def load(_path):
        return request

    def single(value, **kwargs):
        calls.append(("single", value, kwargs))
        return SimpleNamespace(to_dict=lambda: {"mode": "single"})

    def many(value, **kwargs):
        calls.append(("many", value, kwargs))
        return SimpleNamespace(to_dict=lambda: {"mode": "many"})

    monkeypatch.setattr(config, "load_request", load)
    monkeypatch.setattr(cli, "generate", single)
    monkeypatch.setattr(workflow, "generate_many", many)
    assert cli.main(["generate", "request.toml", "--timeout", "12"]) == 0
    assert json.loads(capsys.readouterr().out) == {"mode": "single"}
    request.cell_specs = [object()]
    assert cli.main(["generate", "request.toml"]) == 0
    assert json.loads(capsys.readouterr().out) == {"mode": "many"}
    assert calls[0][0] == "single" and calls[1][0] == "many"
    assert calls[0][2]["timeout"] == 12.0


def test_inspect_reads_only_the_run_manifest(tmp_path, capsys):
    run = tmp_path / "run"
    assert cli.main(["inspect", str(run)]) == 1
    assert "run manifest is missing" in capsys.readouterr().err
    run.mkdir()
    (run / "manifest.json").write_text(
        json.dumps({"run_id": "r1", "status": "succeeded"}), encoding="utf-8"
    )
    assert cli.main(["inspect", str(run)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "run_id": "r1", "status": "succeeded",
    }


@pytest.mark.parametrize(("status", "expected"), [("succeeded", 0), ("failed", 1)])
def test_publish_maps_structured_status_to_exit_code(
    tmp_path, capsys, monkeypatch, status, expected
):
    from mtsnetlistor import config

    monkeypatch.setattr(config, "load_request", lambda _path: object())
    monkeypatch.setattr(cli, "read_session", lambda _path: object())
    result = SimpleNamespace(
        status=status,
        to_dict=lambda: {"status": status, "run_id": "r1"},
    )
    seen = []

    def publish(_request, _session, **kwargs):
        seen.append(kwargs)
        return result

    monkeypatch.setattr(cli, "publish_bundle", publish)
    assert cli.main([
        "publish", "request.toml", "--session", str(tmp_path / "session.json"),
        "--netlist", "netlist.scs", "--run-dir", str(tmp_path / "run"),
    ]) == expected
    assert json.loads(capsys.readouterr().out)["status"] == status
    assert seen[0]["strict_ownership"] is True


def test_main_maps_known_errors_and_interrupts_to_stable_exits(capsys, monkeypatch):
    def reject(_args):
        raise cli.RequestValidationError("bad request")

    monkeypatch.setattr(cli, "_catalog", reject)
    assert cli.main(["catalog", "--source-cdslib", "cds.lib"]) == 1
    assert capsys.readouterr().err.startswith("mts-netlistor: bad request")

    def interrupted(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_catalog", interrupted)
    assert cli.main(["catalog", "--source-cdslib", "cds.lib"]) == 130
