from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import textwrap

import pytest

from mtsnetlistor.errors import IsolationError, MtsNetlistorError
from mtsnetlistor.model import CellNetlistSpec, NetlistRequest, ProcessOptions, SourceDesign
from mtsnetlistor.process import run_isolated
from mtsnetlistor.workflow import GenerationResult, generate, generate_many, probe_defaults
from mtsnetlistor.defaults import DefaultsProbeRequest


def test_run_isolated_records_process_audit_metadata(tmp_path: Path) -> None:
    result = run_isolated(
        ["/bin/sh", "-c", "printf output"],
        cwd=tmp_path,
        environment={"PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 0
    assert result.pid > 0
    assert result.ppid == os.getpid()
    assert result.pgid == result.pid
    assert result.cwd == str(tmp_path.resolve())
    assert len(result.environment_digest) == 64
    assert result.started_at and result.finished_at


def test_run_isolated_forwards_stdout_and_monitor_file_incrementally(
    tmp_path: Path,
) -> None:
    """Live diagnostics include both pipes and an OCEAN-style transcript."""

    monitor = tmp_path / "ocean.log"
    received: list[str] = []
    script = (
        "printf 'stdout-line\\n'; "
        f"printf 'first\\n' > {monitor}; "
        "sleep 0.12; "
        f"printf 'second\\n' >> {monitor}; "
        "printf 'stderr-line\\n' >&2"
    )
    result = run_isolated(
        ["/bin/sh", "-c", script],
        cwd=tmp_path,
        environment={"PATH": os.environ.get("PATH", "")},
        monitor_file=monitor,
        output_callback=received.append,
    )

    assert result.returncode == 0
    combined = "".join(received)
    assert "stdout-line" in combined
    assert "stderr-line" in combined
    assert "first" in combined
    assert "second" in combined
    # The delayed append must have been observed before the process result
    # returned, rather than only being available from the durable log merge.
    assert monitor.read_text(encoding="utf-8") == "first\nsecond\n"


def test_run_isolated_ignores_output_callback_failures(tmp_path: Path) -> None:
    calls = 0

    def broken(_text: str) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("consumer failed")

    result = run_isolated(
        ["/bin/sh", "-c", "printf 'still-successful\\n'"],
        cwd=tmp_path,
        environment={"PATH": os.environ.get("PATH", "")},
        output_callback=broken,
    )
    assert result.returncode == 0
    assert result.stdout == "still-successful\n"
    assert calls >= 1


def test_run_isolated_fails_closed_on_any_mps_selector(tmp_path: Path) -> None:
    with pytest.raises(IsolationError, match="CDS_MPS_FUTURE_SELECTOR"):
        run_isolated(
            ["/bin/true"],
            cwd=tmp_path,
            environment={"CDS_MPS_FUTURE_SELECTOR": "must-not-start"},
        )


def _request(tmp_path: Path) -> NetlistRequest:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    return NetlistRequest(SourceDesign(cds, "work", "top", "schematic"))


def _fake_virtuoso(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-virtuoso"
    executable.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def test_generate_failure_persists_failed_manifest_and_preserves_stable_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path)
    monkeypatch.setenv("PROJ_ADE_DB_DIR", str(tmp_path / "ade"))
    executable = _fake_virtuoso(tmp_path, "echo worker-failed >&2; exit 7")
    with pytest.raises(MtsNetlistorError, match="source Virtuoso failed"):
        generate(request, virtuoso=str(executable), timeout=2)

    namespace = tmp_path / "ade" / "work.top.schematic"
    runs = sorted((namespace / ".mts-netlistor" / "runs").iterdir())
    assert len(runs) == 1
    manifest = json.loads((runs[0] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["failure"]["kind"] == "worker_exit"
    assert manifest["processes"][0]["ppid"] == os.getpid()
    assert not (namespace / "top.spe").exists()
    assert not (namespace / "latest.json").exists()


def test_generate_cancel_persists_canceled_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path)
    monkeypatch.setenv("PROJ_ADE_DB_DIR", str(tmp_path / "ade"))
    executable = _fake_virtuoso(tmp_path, "sleep 10")
    from threading import Event

    cancel = Event()
    cancel.set()
    with pytest.raises(MtsNetlistorError, match="canceled"):
        generate(request, virtuoso=str(executable), timeout=2, cancel_event=cancel)
    namespace = tmp_path / "ade" / "work.top.schematic"
    runs = sorted((namespace / ".mts-netlistor" / "runs").iterdir())
    manifest = json.loads((runs[0] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "canceled"


def test_probe_failure_persists_manifest_and_merges_ocean_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protected_worker_context
) -> None:
    source = _request(tmp_path).source
    monkeypatch.setenv("PROJ_ADE_DB_DIR", str(tmp_path / "ade"))
    executable = _fake_virtuoso(
        tmp_path,
        "printf 'worker stdout\\n'; printf 'worker stderr\\n' >&2; "
        "printf 'cadence transcript\\n' > defaults.log; exit 9",
    )
    with pytest.raises(MtsNetlistorError, match="source defaults worker failed"):
        probe_defaults(
            DefaultsProbeRequest(source, "spectre"),
            ocean=str(executable),
            timeout=2,
        )

    namespace = tmp_path / "ade" / "work.top.schematic"
    runs = sorted((namespace / ".mts-netlistor" / "runs").iterdir())
    assert len(runs) == 1
    run = runs[0]
    manifest = json.loads(
        (run / "defaults-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["failure"]["kind"] == "failed"
    assert manifest["processes"][0]["returncode"] == 9
    worker_log = (run / "logs" / "defaults-worker.log").read_text(encoding="utf-8")
    assert "worker stdout" in worker_log
    assert "worker stderr" in worker_log
    assert "cadence transcript" in worker_log


def test_probe_manifest_counts_after_design_when_startup_snapshot_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protected_worker_context
) -> None:
    source = _request(tmp_path).source
    monkeypatch.setenv("PROJ_ADE_DB_DIR", str(tmp_path / "ade"))
    executable = _fake_virtuoso(
        tmp_path,
        "cat > \"$MTS_NETLISTOR_DEFAULTS_OUTPUT\" <<'EOF'\n"
        '{"schema_version":1,"status":"succeeded","dialect":"spectre",'
        '"tool_name":"spectre","source":{"cds_lib":"SOURCE_CDS",'
        '"library":"work","cell":"top","view":"schematic"},'
        '"baseline":{"model_files":[],"environment_options":{},"simulator_options":{}},'
        '"after_design":{"model_files":[["/tmp/model.scs","tt"]],'
        '"environment_options":{},"simulator_options":{"reltol":{"value":"1e-3"}}},'
        '"after_startup_simrc":{},"diagnostics":[],"api_errors":[]}\n'
        "EOF\n",
    )
    # The report source must match the validated request's absolute cds.lib.
    body = executable.read_text(encoding="utf-8").replace(
        "SOURCE_CDS", str(source.cds_lib.resolve())
    )
    executable.write_text(body, encoding="utf-8")
    probe_defaults(
        DefaultsProbeRequest(source, "spectre"), ocean=str(executable), timeout=2
    )
    namespace = tmp_path / "ade" / "work.top.schematic"
    runs = sorted((namespace / ".mts-netlistor" / "runs").iterdir())
    assert len(runs) == 1
    manifest = json.loads(
        (runs[0] / "defaults-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "succeeded"
    assert manifest["model_count"] == 1
    assert manifest["simulator_option_count"] == 1
    assert manifest["provider"] == "asi_initialization"
    assert manifest["mae_setup"] is None


def test_probe_rejects_worker_report_with_wrong_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protected_worker_context
) -> None:
    source = _request(tmp_path).source
    monkeypatch.setenv("PROJ_ADE_DB_DIR", str(tmp_path / "ade"))
    executable = _fake_virtuoso(
        tmp_path,
        "cat > \"$MTS_NETLISTOR_DEFAULTS_OUTPUT\" <<'EOF'\n"
        '{"schema_version":1,"status":"succeeded","provider":"mae_test",'
        '"mae_setup":{"library":"ade","cell":"setup","view":"maestro","test":"nominal"},'
        '"dialect":"spectre","tool_name":"spectre","source":{"cds_lib":"SOURCE_CDS",'
        '"library":"work","cell":"top","view":"schematic"},'
        '"baseline":{},"after_design":{},"after_startup_simrc":{},'
        '"diagnostics":[],"api_errors":[]}\n'
        "EOF\n",
    )
    body = executable.read_text(encoding="utf-8").replace(
        "SOURCE_CDS", str(source.cds_lib.resolve())
    )
    executable.write_text(body, encoding="utf-8")
    with pytest.raises(MtsNetlistorError, match="provider does not match request"):
        probe_defaults(
            DefaultsProbeRequest(source, "spectre"), ocean=str(executable), timeout=2
        )
    namespace = tmp_path / "ade" / "work.top.schematic"
    run = next((namespace / ".mts-netlistor" / "runs").iterdir())
    manifest = json.loads((run / "defaults-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["provider"] == "asi_initialization"
    assert manifest["mae_setup"] is None


def test_generate_many_delegates_each_cell_with_its_own_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    request = NetlistRequest(
        SourceDesign(cds, "work", "placeholder"),
        cell_specs=(
            CellNetlistSpec("work", "a", process_options=ProcessOptions(temp=25)),
            CellNetlistSpec("work", "b", process_options=ProcessOptions(temp=85)),
        ),
    )
    captured = []

    def fake_generate(child: NetlistRequest, **_kwargs) -> GenerationResult:
        captured.append(child)
        cell = child.source.cell
        root = tmp_path / cell
        root.mkdir()
        artifact = root / f"{cell}.spe"
        artifact.write_text(f"subckt {cell}\nends {cell}\n", encoding="utf-8")
        return GenerationResult(
            "a" * 64,
            "succeeded",
            root,
            artifact,
            artifact,
            artifact,
            artifact,
            object(),  # type: ignore[arg-type]
        )

    monkeypatch.setattr("mtsnetlistor.workflow.generate", fake_generate)
    result = generate_many(request)
    assert [item.cell for item in result.cells] == ["a", "b"]
    assert [item.process_options.temp for item in captured] == [25, 85]
    assert all(not item.cell_specs for item in captured)
    assert all(not item.target.generate_symbol_view for item in captured)
    assert all(not item.target.generate_netlist_view for item in captured)
