from __future__ import annotations

import json
from pathlib import Path

import pytest

from aivworkbench.errors import EnvironmentError
from aivworkbench.m1ai_golden import run_m1_ai_golden_nominal
from aivworkbench.m1ai_golden_inputs import (
    find_nominal_evidence,
    load_json_artifact,
    parse_nominal_csv,
    relocate_maestro_project_dirs,
    stage_saradc_library,
    validate_spectre_and_netlist,
)
from aivworkbench.m1ai_golden_runtime import PhaseFailure, run_spectre_preflight
from aivworkbench.process import ProcessResult
from aivworkbench.profiles import Profile, ToolSpec
from aivworkbench.toolchain import ToolResult
from aivworkbench.workspace import resolve_launch_paths


TEST = "saradc:comparator_offset_TB_new:1"
ACTIVE_STATE = """<field Name="projectDir" Type="string">"/old/saradc/noise"</field>
<field Name="projectDir" Type="string">"/old/saradc/offset"</field>
"""


def test_stage_and_relocate_maestro_inputs(tmp_path: Path) -> None:
    source = tmp_path / "source" / "saradc"
    source.mkdir(parents=True)
    (source / "data").write_text("OA placeholder\n", encoding="utf-8")
    destination = tmp_path / "run" / "saradc"
    destination.parent.mkdir()

    staged = stage_saradc_library(source, destination)
    assert staged["initial_copy_matches"] is True
    assert (destination / "data").read_bytes() == (source / "data").read_bytes()

    active = destination / "active.state"
    active.write_text(ACTIVE_STATE, encoding="utf-8")
    project_dir = tmp_path / "run" / "simulation"
    relocated = relocate_maestro_project_dirs(active, project_dir)
    assert relocated["new"] == [
        f"{project_dir}/saradc/noise",
        f"{project_dir}/saradc/offset",
    ]


def test_maestro_relocation_requires_exactly_two_fields(tmp_path: Path) -> None:
    active = tmp_path / "active.state"
    active.write_text(ACTIVE_STATE.splitlines()[0], encoding="utf-8")
    with pytest.raises(EnvironmentError, match="expected two"):
        relocate_maestro_project_dirs(active, tmp_path / "simulation")


def _detail_csv(path: Path, value: str, verdict: str = "pass") -> None:
    path.write_text(
        ",Parameter,Nominal,,,\n\n\n"
        "Test,Output,Nominal,Spec,Pass/Fail\n"
        f"{TEST},Offset,{value},range -5m 5m,{verdict}\n",
        encoding="utf-8",
    )


def test_nominal_csv_accepts_in_spec_pass_and_rejects_tool_error(tmp_path: Path) -> None:
    detail = tmp_path / "detail.csv"
    _detail_csv(detail, "1.25e-4")
    metric = parse_nominal_csv(detail)
    assert metric["value_volts"] == pytest.approx(1.25e-4)
    assert metric["passes"] is True

    _detail_csv(detail, "netl err")
    with pytest.raises(EnvironmentError, match="unavailable"):
        parse_nominal_csv(detail)


def _runtime_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    spectre = tmp_path / "spectre.out"
    spectre.write_text(
        "Version 23.1.0.538.isr10\nspectre completes with 0 errors\n",
        encoding="utf-8",
    )
    netlist = tmp_path / "netlist"
    netlist.write_text(
        "// Cell name: comparator_new\n"
        "subckt comparator_new Dinp Dinm clk outb out\n"
        "ends comparator_new\n"
        "I0 (a b c d e) comparator_new\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "runSimulation"
    launcher.write_text(" spectre input.scs\n", encoding="utf-8")
    return spectre, netlist, launcher


def test_runtime_evidence_fails_closed_on_version_and_dut_identity(tmp_path: Path) -> None:
    spectre, netlist, launcher = _runtime_files(tmp_path)
    valid = validate_spectre_and_netlist(
        spectre, netlist, launcher, expected_version="23.1.0.538.isr10"
    )
    assert valid["all_checks_pass"] is True

    wrong_version = validate_spectre_and_netlist(
        spectre, netlist, launcher, expected_version="25.1.0.125.isr1"
    )
    assert wrong_version["checks"]["spectre_version_matches"] is False
    spectre.write_text(
        "Version 23.1.0.538.isr10\n"
        "FATAL (SPECTRE-209): required license could not be checked out\n",
        encoding="utf-8",
    )
    blocked = validate_spectre_and_netlist(
        spectre, netlist, launcher, expected_version="23.1.0.538.isr10"
    )
    assert blocked["license_blocked"] is True
    netlist.write_text("I0 (a b c d e) comparator\n", encoding="utf-8")
    wrong_dut = validate_spectre_and_netlist(
        spectre, netlist, launcher, expected_version="23.1.0.538.isr10"
    )
    assert wrong_dut["checks"]["target_subckt_present"] is False
    assert wrong_dut["checks"]["top_instance_uses_target"] is False


def test_json_and_history_artifacts_are_scratch_only_and_path_safe(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence.json"
    artifact.write_text(
        json.dumps({"schema_version": 1, "provider": "dbAccess"}),
        encoding="utf-8",
    )
    with pytest.raises(EnvironmentError, match="scratch-only"):
        load_json_artifact(artifact, provider="dbAccess")
    with pytest.raises(EnvironmentError, match="unsafe nominal history"):
        find_nominal_evidence(tmp_path, "../../source")


def test_nominal_evidence_uses_numeric_ade_point_not_psf_projection(
    tmp_path: Path,
) -> None:
    results = tmp_path / "results"
    for container in ("1", "psf"):
        point = results / "Interactive.0" / container / TEST
        (point / "psf").mkdir(parents=True)
        (point / "netlist").mkdir()
        (point / "psf" / "spectre.out").write_text("spectre\n", encoding="utf-8")
        (point / "netlist" / "netlist").write_text("netlist\n", encoding="utf-8")
        (point / "netlist" / "runSimulation").write_text(
            "spectre input.scs\n", encoding="utf-8"
        )

    artifacts = find_nominal_evidence(results, "Interactive.0")
    assert all(f"{Path('Interactive.0') / '1' / TEST}" in str(path) for path in artifacts)

    (results / "Interactive.0" / "2").mkdir()
    with pytest.raises(EnvironmentError, match="exactly one numeric ADE point"):
        find_nominal_evidence(results, "Interactive.0")


def test_spectre_preflight_classifies_license_failure(tmp_path: Path) -> None:
    def blocked(command: object, **kwargs: object) -> ProcessResult:
        Path(str(kwargs["log_file"])).write_text(
            "Version 23.1.0.538.isr10\n"
            "FATAL (SPECTRE-209): required license could not be checked out\n",
            encoding="utf-8",
        )
        return ProcessResult(
            tuple(str(item) for item in command),
            str(kwargs["cwd"]),
            "start", "finish", 0.1, 2, False,
            str(kwargs["log_file"]),
        )

    with pytest.raises(PhaseFailure) as raised:
        run_spectre_preflight(
            blocked,
            "/approved/spectre",
            cwd=tmp_path / "work",
            environment={"PATH": "/approved"}, log_file=tmp_path / "preflight.log",
            timeout=30.0, expected_version="23.1.0.538.isr10",
        )
    assert raised.value.status == "BLOCKED_LICENSE"
    assert raised.value.result is not None


def test_spectre_preflight_writes_valid_dc_analysis(tmp_path: Path) -> None:
    def succeeds(command: object, **kwargs: object) -> ProcessResult:
        argv = tuple(str(item) for item in command)
        netlist = Path(argv[1])
        text = netlist.read_text(encoding="utf-8")
        assert "dcOp dc\n" in text
        assert "op op\n" not in text
        Path(str(kwargs["log_file"])).write_text(
            "Version 23.1.0.538.isr10\n"
            "spectre completes with 0 errors\n",
            encoding="utf-8",
        )
        return ProcessResult(
            argv,
            str(kwargs["cwd"]),
            "start", "finish", 0.1, 0, False,
            str(kwargs["log_file"]),
        )

    result = run_spectre_preflight(
        succeeds,
        "/approved/spectre",
        cwd=tmp_path / "work",
        environment={"PATH": "/approved"},
        log_file=tmp_path / "preflight.log",
        timeout=30.0,
        expected_version="23.1.0.538.isr10",
    )
    assert result.returncode == 0


def _profile(tmp_path: Path) -> Profile:
    adc = tmp_path / "adc"
    saradc = adc / "DESIGNS" / "GPDK045" / "SARADC" / "oa" / "saradc"
    maestro = saradc / "comparator_new" / "maestro"
    maestro.mkdir(parents=True)
    (maestro / "active.state").write_text(ACTIVE_STATE, encoding="utf-8")
    saradc_ii = saradc.parent / "saradcII"
    saradc_ii.mkdir()
    (saradc_ii / "reference.oa").write_text("reference\n", encoding="utf-8")
    cds_lib = adc / "WORK" / "saradc" / "cds.lib"
    cds_lib.parent.mkdir(parents=True)
    cds_lib.write_text("DEFINE saradc /source/saradc\n", encoding="utf-8")
    setup = tmp_path / "setup.bashrc"
    modules = ("fake/ic", "fake/xcelium", "fake/spectre")
    setup.write_text("module load " + " ".join(modules) + "\n", encoding="utf-8")
    profile_file = tmp_path / "profile.json"
    profile_file.write_text("{}\n", encoding="utf-8")
    return Profile(
        name="test",
        display_name="Test",
        workspace_root=tmp_path,
        setup_script=setup,
        modules=modules,
        tools=tuple(ToolSpec(name, name) for name in ("dbaccess", "virtuoso", "spectre")),
        inputs=(),
        projects={"adc": {"root": str(adc), "cds_lib": str(cds_lib)}},
        pilots={},
        source_path=profile_file,
    )


def _fake_process(command: object, **kwargs: object) -> ProcessResult:
    argv = tuple(str(item) for item in command)
    environment = kwargs["environment"]
    console = Path(str(kwargs["log_file"]))
    console.write_text("phase console\n", encoding="utf-8")
    if argv[0].endswith("spectre"):
        console.write_text(
            "Version 23.1.0.538.isr10\n"
            "spectre completes with 0 errors\n",
            encoding="utf-8",
        )
    elif argv[0].endswith("dbAccess"):
        scratch = Path(environment["AIVW_SCRATCH_SARADC"])
        assert scratch == scratch.resolve()
        Path(environment["AIVW_REBIND_OUTPUT"]).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": "dbAccess",
                    "scratch_only": True,
                    "bindings": [
                        {"readback_verified": True},
                        {"readback_verified": True},
                    ],
                }
            ),
            encoding="utf-8",
        )
        console.write_text("AIVW_M1_REBIND_DONE\n", encoding="utf-8")
    elif argv[-1].endswith("m1ai_schcheck_worker.il"):
        log = Path(argv[argv.index("-log") + 1])
        log.write_text(
            "AIVW_M1_SCHCHECK comparator_noise_TB_new errors=0 warnings=0\n"
            "AIVW_M1_SCHCHECK comparator_offset_TB_new errors=0 warnings=0\n"
            "AIVW_M1_SCHCHECK_DONE\n",
            encoding="utf-8",
        )
    else:
        log = Path(argv[argv.index("-log") + 1])
        log.write_text("AIVW_M1_NOMINAL_DONE\n", encoding="utf-8")
        history = "Interactive.1"
        Path(environment["AIVW_NOMINAL_OUTPUT"]).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": "Virtuoso/ADE Assembler",
                    "scratch_only": True,
                    "setup_db_dir": environment["AIVW_SETUP_DB_DIR"],
                    "results_location": environment["AIVW_RESULTS_LOCATION"],
                    "history": history,
                }
            ),
            encoding="utf-8",
        )
        _detail_csv(Path(environment["AIVW_DETAIL_CSV"]), "0.0")
        root = (
            Path(environment["AIVW_RESULTS_LOCATION"])
            / history
            / "1"
            / TEST
        )
        (root / "psf").mkdir(parents=True)
        (root / "netlist").mkdir()
        spectre, netlist, launcher = _runtime_files(root / "netlist")
        spectre.replace(root / "psf" / "spectre.out")
        assert netlist.name == "netlist" and launcher.name == "runSimulation"
    return ProcessResult(argv, str(kwargs["cwd"]), "start", "finish", 0.1, 0, False, str(console))


def test_nominal_runner_records_reproducible_scratch_only_pass(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    physical_launch = tmp_path / "physical-launch"
    physical_launch.mkdir()
    launch = tmp_path / "logical-launch"
    launch.symlink_to(physical_launch, target_is_directory=True)

    def probe(_specs: object, _environment: object) -> tuple[ToolResult, ...]:
        return (
            ToolResult("dbaccess", "dbAccess", "/fake/dbAccess", 0, "", "PASS"),
            ToolResult("virtuoso", "virtuoso", "/fake/virtuoso", 0, "", "PASS"),
            ToolResult(
                "spectre",
                "spectre",
                "/fake/spectre",
                0,
                "Version 23.1.0.538.isr10",
                "PASS",
            ),
        )

    result = run_m1_ai_golden_nominal(
        profile,
        resolve_launch_paths(launch),
        environment_loader=lambda _modules: {"PATH": "/fake"},
        tool_prober=probe,
        process_runner=_fake_process,
    )

    assert result["status"] == "PASS"
    assert result["sources_unchanged"] is True
    assert len(result["processes"]) == 4
    assert result["verdict_evidence"]["runtime"]["all_checks_pass"] is True
    assert any(item["path"] == "golden-nominal-evidence.json" for item in result["artifacts"])
    assert Path(str(result["run_dir"])).is_relative_to(launch / ".aivw")
    assert result["launch"]["physical_cwd"] == str(physical_launch)
