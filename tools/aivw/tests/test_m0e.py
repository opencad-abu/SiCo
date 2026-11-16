from __future__ import annotations

from pathlib import Path

from aivworkbench.m0e import run_m0_e
from aivworkbench.process import ProcessResult
from aivworkbench.profiles import InputSpec, PilotSpec, Profile, ToolSpec
from aivworkbench.toolchain import ToolResult
from aivworkbench.workspace import resolve_launch_paths


def test_m0e_known_good_verdict_and_source_isolation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    lab6 = source / "Lab6"
    sim = lab6 / "test" / "CORE_UVM" / "sim"
    controls = lab6 / "src" / "ams" / "control_files"
    tb = lab6 / "test" / "CORE_UVM" / "tb"
    for directory in (sim, controls, tb):
        directory.mkdir(parents=True, exist_ok=True)
    run_file = sim / "run_full.f"
    run_file.write_text("-uvm\n", encoding="utf-8")
    for name in ("cdsproc_macros.svh", "cdsproc_package.sv", "upf_package.sv", "LDO_MASTER.sv"):
        (sim / name).write_text("// input\n", encoding="utf-8")
    for name in ("divInit.dat", "divInit_neg.dat", "divInit_fra.dat", "divInit_lt2.dat"):
        (sim / name).write_text(f"{name} input\n", encoding="utf-8")
    (lab6 / "src" / "design.f").write_text("design.sv\n", encoding="utf-8")
    (controls / "CORE_UVM_amscf.scs").write_text("amsd {}\n", encoding="utf-8")
    (tb / "TB_CORE.upf").write_text("upf_version 2.0\n", encoding="utf-8")
    setup = source / "setup.bashrc"
    setup.write_text("module load fake/xcelium\n", encoding="utf-8")
    profile_file = source / "profile.json"
    profile_file.write_text("{}\n", encoding="utf-8")
    pilot = PilotSpec("m0-e", "test", "test", str(run_file), (), ())
    profile = Profile(
        name="test",
        display_name="Test",
        workspace_root=source,
        setup_script=setup,
        modules=("fake/xcelium",),
        tools=(ToolSpec("xrun", "xrun"),),
        inputs=(InputSpec("m0_e_run_file", run_file),),
        projects={"dms_ams_flow": {"root": str(source)}},
        pilots={"m0-e": pilot},
        source_path=profile_file,
    )

    def load_environment(_modules: object) -> dict[str, str]:
        return {"PATH": "/fake", "AMSHOME": "/fake/xcelium"}

    def probe(_specs: object, _environment: object) -> tuple[ToolResult, ...]:
        return (ToolResult("xrun", "xrun", "/fake/xrun", 0, "23.09", "PASS"),)

    def run_process(command: object, **kwargs: object) -> ProcessResult:
        log_file = Path(str(kwargs["log_file"]))
        log_file.write_text(
            "UVM_ERROR :    0\n"
            "UVM_FATAL :    0\n"
            "** TEST PASSED **\n"
            "Simulation complete via $finish(1)\n"
            "xmsim: *E,RNFNSH: Cannot continue simulation due to a previous $finish.\n",
            encoding="utf-8",
        )
        work = Path(str(kwargs["cwd"]))
        (work / "xrun.log").write_text("complete\n", encoding="utf-8")
        return ProcessResult(
            command=tuple(str(item) for item in command),
            cwd=str(work),
            started_at="2026-08-29T00:00:00+00:00",
            finished_at="2026-08-29T00:00:01+00:00",
            elapsed_seconds=1.0,
            returncode=1,
            timed_out=False,
            log_file=str(log_file),
        )

    before = {path: path.read_bytes() for path in (run_file, setup, profile_file)}
    result = run_m0_e(
        profile,
        resolve_launch_paths(tmp_path),
        environment_loader=load_environment,
        tool_prober=probe,
        process_runner=run_process,
    )
    assert result["status"] == "PASS"
    assert result["sources_unchanged"] is True
    assert result["verdict_evidence"]["uvm_error_counts"] == [0]
    assert result["verdict_evidence"]["tool_error_codes"] == ["RNFNSH"]
    assert result["verdict_evidence"]["unexpected_tool_error_codes"] == []
    assert result["verdict_evidence"]["accepted_post_finish_rnfnsh"] is True
    assert result["process"]["command"][1:3] == ["-incdir", str(sim)]
    run_work = Path(str(result["run_dir"])) / "work"
    assert {
        path.name for path in run_work.glob("divInit*.dat")
    } == {"divInit.dat", "divInit_neg.dat", "divInit_fra.dat", "divInit_lt2.dat"}
    assert len(result["staged_runtime_inputs"]) == 4
    assert {path: path.read_bytes() for path in before} == before
    assert Path(str(result["run_dir"])).is_relative_to(tmp_path / ".aivw" / "runs")
