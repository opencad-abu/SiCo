"""Bounded candidate-layout DRC checks and their provenance."""

import json
from pathlib import Path

import pytest
from sico_calibre_fixture import (
    make_calibre,
    make_flow,
    make_runner,
    wait_terminal,
    write_script,
)
from sico_datahub.calibre.candidate import normalize_bbox, prepare_candidate
from sico_datahub.calibre.context import resolve_flow
from sico_datahub.calibre.jobs import JobStore
from sico_datahub.errors import HubError


def _candidate(tmp_path, suffix=".gds"):
    path = tmp_path / ("candidate" + suffix)
    path.write_text("CANDIDATE\n", encoding="ascii")
    return path


def _prepare(tmp_path, flow, candidate, region):
    store = JobStore(tmp_path)
    calibre = make_calibre(tmp_path) / "bin/calibre"
    return store, prepare_candidate(
        resolve_flow("drc", str(flow.config), str(tmp_path)),
        store=store,
        run_ref="candidate-1",
        candidate_path=str(candidate),
        region=region,
        calibre_executable=calibre,
        timeout=10,
    )


def test_candidate_preparation_binds_candidate_and_private_config(tmp_path):
    flow = make_flow(tmp_path, kind="drc")
    candidate = _candidate(tmp_path)
    store, spec = _prepare(tmp_path, flow, candidate, [1, 2, 30, 40])
    assert spec.flow.run_dir == store.candidate_run_dir("candidate-1")
    assert spec.flow.options["scope"] == "candidate"
    assert spec.flow.options["candidate_bbox"] == [[1.0, 2.0], [30.0, 40.0]]
    assert spec.candidate.path == str(candidate)
    assert spec.deck.is_file()
    assert "-drc" in spec.command
    deck = spec.prepared.effective_deck.path
    body = (tmp_path / deck).read_text(encoding="utf-8")
    assert f'LAYOUT PATH "{candidate}"' in body
    assert "LAYOUT SYSTEM GDSII" in body
    assert str(spec.flow.run_dir / "db/drc/cal_drc.out") in body
    config = (tmp_path / spec.prepared.config.path).read_text(encoding="utf-8")
    assert 'type = "CDL+GDS"' in config
    assert f'file = "{candidate}"' in config
    assert f'run_dir = "{spec.flow.run_dir}"' in config
    assert spec.prepared.preparation["candidate"] is True


def test_candidate_oasis_selects_oasis_layout_system(tmp_path):
    flow = make_flow(tmp_path, kind="drc")
    candidate = _candidate(tmp_path, ".oas")
    _store, spec = _prepare(tmp_path, flow, candidate, None)
    body = (tmp_path / spec.prepared.effective_deck.path).read_text(encoding="utf-8")
    assert "LAYOUT SYSTEM OASIS" in body
    assert "LAYOUT SYSTEM GDSII" not in body
    driver = (spec.flow.log_dir / "drc.cal").read_text(encoding="utf-8")
    assert "LAYOUT SYSTEM OASIS" in driver
    assert "LAYOUT SYSTEM GDSII" not in driver
    assert spec.region is None


def test_candidate_bbox_rejects_incomplete_or_inverted_values():
    with pytest.raises(HubError, match="four numbers"):
        normalize_bbox([0, 0, 10])
    with pytest.raises(HubError, match="Candidate bbox is invalid"):
        normalize_bbox([10, 0, 0, 10])
    assert normalize_bbox(None) is None


def test_candidate_runner_is_non_signoff_and_keeps_provenance(tmp_path):
    flow = make_flow(tmp_path, kind="drc")
    candidate = _candidate(tmp_path)
    runner = make_runner(tmp_path)
    calibre = runner.calibre_executable
    write_script(
        calibre,
        'if [ "$1" = "-version" ]; then\n'
        '  printf "Calibre 2026.3 fixture\\n"\n'
        '  exit 0\n'
        "fi\n"
        'deck=\n'
        'for argument in "$@"; do deck=$argument; done\n'
        'results=$(sed -n \'s/^DRC RESULTS DATABASE "\\(.*\\)" ASCII$/\\1/p\' "$deck")\n'
        'summary=$(sed -n \'s/^DRC SUMMARY REPORT "\\(.*\\)"$/\\1/p\' "$deck")\n'
        'mkdir -p "$(dirname "$results")" "$(dirname "$summary")"\n'
        'printf "TOTAL RESULT COUNT = 0\\n" > "$summary"\n'
        'printf "# Calibre DRC Results Database\\n" > "$results"\n'
        'exit 0\n',
    )
    try:
        receipt = runner.submit_candidate(
            "candidate-run-1",
            str(flow.config),
            str(tmp_path),
            str(candidate),
            [0, 0, 100, 100],
        )
        assert receipt.scope == "candidate"
        assert receipt.candidate is not None
        final = wait_terminal(runner, "candidate-run-1")
        assert final.state == "clean"
        assert final.effective_config is not None
        _stored, result = runner.result("candidate-run-1")
        assert result["scope"] == "candidate"
        assert result["signoff_eligible"] is False
        provenance = result["provenance"]
        assert provenance["candidate"]["path"] == str(candidate)
        assert provenance["effective_deck"]["path"] == final.effective_deck.path
        assert provenance["options"]["candidate_bbox"] == [[0.0, 0.0], [100.0, 100.0]]
    finally:
        runner.close()


def test_lvs_candidate_preparation_uses_candidate_layout_and_source(tmp_path):
    flow = make_flow(tmp_path, kind="lvs")
    candidate = _candidate(tmp_path)
    source = tmp_path / "candidate.cdl"
    source.write_text(".SUBCKT compare a b\n.ENDS\n", encoding="ascii")
    store = JobStore(tmp_path)
    calibre = make_calibre(tmp_path) / "bin/calibre"
    spec = prepare_candidate(
        resolve_flow("lvs", str(flow.config), str(tmp_path)),
        store=store,
        run_ref="candidate-lvs-1",
        candidate_path=str(candidate),
        source_path=str(source),
        region=[0, 0, 100, 100],
        calibre_executable=calibre,
        timeout=10,
    )
    assert spec.source is not None and spec.source.path == str(source)
    assert "-lvs" in spec.command
    body = (tmp_path / spec.prepared.effective_deck.path).read_text(encoding="utf-8")
    assert f'LAYOUT PATH "{candidate}"' in body
    assert f'SOURCE PATH "{source}"' in body
    assert f'LVS REPORT "{spec.flow.log_dir / "lvs.report"}"' in body
    config = (tmp_path / spec.prepared.config.path).read_text(encoding="utf-8")
    assert f'file = "{source}"' in config
    assert f'file = "{candidate}"' in config


def test_lvs_candidate_runner_is_correct_but_not_signoff(tmp_path):
    flow = make_flow(tmp_path, kind="lvs")
    candidate = _candidate(tmp_path)
    source = tmp_path / "candidate.cdl"
    source.write_text(".SUBCKT compare a b\n.ENDS\n", encoding="ascii")
    runner = make_runner(tmp_path)
    calibre = runner.calibre_executable
    write_script(
        calibre,
        'if [ "$1" = "-version" ]; then\n'
        '  printf "Calibre 2026.3 fixture\\n"\n'
        '  exit 0\n'
        "fi\n"
        "deck=\n"
        'for argument in "$@"; do deck=$argument; done\n'
        'report=$(sed -n \'s/^LVS REPORT "\\(.*\\)"$/\\1/p\' "$deck")\n'
        'mkdir -p "$(dirname "$report")"\n'
        'printf "OVERALL COMPARISON RESULTS\\n#     CORRECT     #\\n" > "$report"\n'
        "exit 0\n",
    )
    try:
        receipt = runner.submit_candidate(
            "candidate-lvs-run-1",
            str(flow.config),
            str(tmp_path),
            str(candidate),
            [0, 0, 100, 100],
            kind="lvs",
            source_path=str(source),
        )
        assert receipt.kind == "lvs"
        assert receipt.candidate_source is not None
        final = wait_terminal(runner, "candidate-lvs-run-1")
        assert final.state == "correct"
        _stored, result = runner.result("candidate-lvs-run-1")
        assert result["status"] == "correct"
        assert result["scope"] == "candidate"
        assert result["signoff_eligible"] is False
        assert result["provenance"]["source_netlist"]["path"] == str(source)
    finally:
        runner.close()


def test_lvs_candidate_rejects_a_visible_fragment_sidecar(tmp_path):
    flow = make_flow(tmp_path, kind="lvs")
    candidate = _candidate(tmp_path, ".oasis")
    Path(str(candidate) + ".sico-visible.json").write_text(
        json.dumps({
            "schema": "cad.visible_layout_fragment.v1",
            "connectivity_preserved": False,
            "lvs_eligible": False,
        }),
        encoding="utf-8",
    )
    source = tmp_path / "candidate.cdl"
    source.write_text(".SUBCKT compare a b\n.ENDS\n", encoding="ascii")
    store = JobStore(tmp_path)
    with pytest.raises(HubError, match="Visible-window fragments"):
        prepare_candidate(
            resolve_flow("lvs", str(flow.config), str(tmp_path)),
            store=store,
            run_ref="candidate-visible-lvs",
            candidate_path=str(candidate),
            source_path=str(source),
            region=[0, 0, 100, 100],
            calibre_executable=make_calibre(tmp_path) / "bin/calibre",
            timeout=10,
        )


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        "[]",
        '"text"',
    ],
)
def test_lvs_candidate_rejects_a_malformed_visible_sidecar(tmp_path, payload):
    flow = make_flow(tmp_path, kind="lvs")
    candidate = _candidate(tmp_path, ".oasis")
    Path(str(candidate) + ".sico-visible.json").write_text(
        payload,
        encoding="utf-8",
    )
    source = tmp_path / "candidate.cdl"
    source.write_text(".SUBCKT compare a b\n.ENDS\n", encoding="ascii")
    with pytest.raises(HubError, match="Visible-window fragments"):
        prepare_candidate(
            resolve_flow("lvs", str(flow.config), str(tmp_path)),
            store=JobStore(tmp_path),
            run_ref="candidate-malformed-sidecar",
            candidate_path=str(candidate),
            source_path=str(source),
            region=[0, 0, 100, 100],
            calibre_executable=make_calibre(tmp_path) / "bin/calibre",
            timeout=10,
        )


def test_lvs_candidate_rejects_sidecar_declaring_lvs_ineligible(tmp_path):
    flow = make_flow(tmp_path, kind="lvs")
    candidate = _candidate(tmp_path, ".oasis")
    Path(str(candidate) + ".sico-visible.json").write_text(
        json.dumps({"schema": "custom.fragment.v2", "lvs_eligible": False}),
        encoding="utf-8",
    )
    source = tmp_path / "candidate.cdl"
    source.write_text(".SUBCKT compare a b\n.ENDS\n", encoding="ascii")
    with pytest.raises(HubError, match="Visible-window fragments"):
        prepare_candidate(
            resolve_flow("lvs", str(flow.config), str(tmp_path)),
            store=JobStore(tmp_path),
            run_ref="candidate-ineligible-sidecar",
            candidate_path=str(candidate),
            source_path=str(source),
            region=[0, 0, 100, 100],
            calibre_executable=make_calibre(tmp_path) / "bin/calibre",
            timeout=10,
        )


def test_lvs_candidate_allows_unrelated_sidecar_schema(tmp_path):
    flow = make_flow(tmp_path, kind="lvs")
    candidate = _candidate(tmp_path)
    Path(str(candidate) + ".sico-visible.json").write_text(
        json.dumps({"schema": "custom.annotation.v1"}),
        encoding="utf-8",
    )
    source = tmp_path / "candidate.cdl"
    source.write_text(".SUBCKT compare a b\n.ENDS\n", encoding="ascii")
    spec = prepare_candidate(
        resolve_flow("lvs", str(flow.config), str(tmp_path)),
        store=JobStore(tmp_path),
        run_ref="candidate-unrelated-sidecar",
        candidate_path=str(candidate),
        source_path=str(source),
        region=[0, 0, 100, 100],
        calibre_executable=make_calibre(tmp_path) / "bin/calibre",
        timeout=10,
    )
    assert spec.flow.options["scope"] == "candidate"


def test_drc_candidate_ignores_visible_fragment_sidecar(tmp_path):
    flow = make_flow(tmp_path, kind="drc")
    candidate = _candidate(tmp_path, ".oasis")
    Path(str(candidate) + ".sico-visible.json").write_text(
        json.dumps({
            "schema": "cad.visible_layout_fragment.v1",
            "connectivity_preserved": False,
            "lvs_eligible": False,
        }),
        encoding="utf-8",
    )
    _store, spec = _prepare(tmp_path, flow, candidate, [0, 0, 100, 100])
    assert spec.flow.options["scope"] == "candidate"
    assert spec.candidate.path == str(candidate)
    body = (tmp_path / spec.prepared.effective_deck.path).read_text(encoding="utf-8")
    assert f'LAYOUT PATH "{candidate}"' in body


def test_export_reuses_existing_lvs_stage_cli(tmp_path):
    flow = make_flow(tmp_path, kind="lvs")
    runner = make_runner(tmp_path)
    write_script(
        runner.commands["lvs"],
        'if [ "$1" = "run" ] && [ "$4" = "gds" ]; then\n'
        f'  mkdir -p {flow.run_dir / "db/gds"} {flow.run_dir / "db/cdl"}\n'
        f'  printf "GDS\\n" > {flow.run_dir / "db/gds/inv.gds"}\n'
        f'  printf "CDL\\n" > {flow.run_dir / "db/cdl/inv.cdl"}\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )
    try:
        payload = runner.export_layout("export-1", str(flow.config), str(tmp_path))
        assert payload["export_ref"] == "export-1"
        assert payload["gds"]["path"].endswith("/db/gds/inv.gds")
        assert payload["cdl"]["path"].endswith("/db/cdl/inv.cdl")
        assert payload["stdout"]["size"] >= 0
        assert payload["stderr"]["size"] >= 0
        assert payload["layout_cell"] == "inv"
        assert payload["source_cell"] == "inv"
    finally:
        runner.close()
