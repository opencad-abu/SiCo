from __future__ import annotations

import json
from pathlib import Path

from aivworkbench.doctor import run_doctor
from aivworkbench.profiles import InputSpec, PilotSpec, Profile, ToolSpec
from aivworkbench.toolchain import ToolResult
from aivworkbench.workspace import resolve_launch_paths


def test_doctor_writes_pass_manifest_only_under_cwd(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    setup = source / "setup.bashrc"
    setup.write_text("module load test/ic\n", encoding="utf-8")
    source_input = source / "input.f"
    source_input.write_text("-sv\n", encoding="utf-8")
    profile_file = source / "profile.json"
    profile_file.write_text("{}\n", encoding="utf-8")
    pilot = PilotSpec("m0-e", "test", "test", str(source_input), (), ())
    profile = Profile(
        name="test",
        display_name="Test",
        workspace_root=source,
        setup_script=setup,
        modules=("test/ic",),
        tools=(ToolSpec("fake", "fake"),),
        inputs=(InputSpec("input", source_input),),
        projects={},
        pilots={"m0-e": pilot},
        source_path=profile_file,
    )

    def load_environment(_modules: object) -> dict[str, str]:
        return {"PATH": "/bin", "CDSHOME": "/fake/ic"}

    def probe(_specs: object, _environment: object) -> tuple[ToolResult, ...]:
        return (ToolResult("fake", "fake", "/fake/ic/fake", 0, "test", "PASS"),)

    manifest = run_doctor(
        profile,
        resolve_launch_paths(tmp_path),
        environment_loader=load_environment,
        tool_prober=probe,
    )
    assert manifest["status"] == "PASS"
    run_dir = Path(str(manifest["run_dir"]))
    assert run_dir.is_relative_to(tmp_path / ".aivw" / "runs")
    stored = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert stored["source_policy"]["control_contract"] == "$CWD/.aivw"
    assert stored["storage"]["payload_root"] is None
    assert set(source.iterdir()) == {setup, source_input, profile_file}
