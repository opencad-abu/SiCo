from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402


def _config(tmp_path: Path) -> RceConfig:
    run_dir = tmp_path / "run"
    corner_dir = tmp_path / "tech/Cmax"
    corner_dir.mkdir(parents=True)
    (corner_dir / "qrcTechFile").write_text("mock qrc tech\n", encoding="utf-8")
    return RceConfig(
        raw={
            "run": {"run_dir": str(run_dir)},
            "input": {
                "type": "CCI",
                "cci": {"cell": "top", "dir": str(tmp_path / "cci.top")},
            },
            "lvs": {"tool": "Calibre"},
            "extract": {
                "tool": "QRC",
                "tech_dir": str(tmp_path / "tech"),
                "corner": "Cmax",
                "rc_type": "RC",
                "output_type": "dspf",
            },
            "runtime": {"ext_cpus": "2"},
            "netlist": {"output_path": str(run_dir / "top.dspf")},
            "reduction": {"enabled": True},
        },
        config_path=tmp_path / "rce.toml",
    )


def _install_fake_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    qreduce_exit: int,
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    qrc = bin_dir / "qrc"
    qrc.write_text(
        """#!/bin/sh
set -eu
mkdir -p "$(dirname "$FAKE_EXTRACT_OUTPUT")"
printf '* original extracted netlist\n' > "$FAKE_EXTRACT_OUTPUT"
""",
        encoding="utf-8",
    )
    qrc.chmod(0o755)

    qreduce = bin_dir / "qreduce"
    qreduce.write_text(
        """#!/bin/sh
set -eu
: > "$FAKE_QREDUCE_ARGS"
for argument in "$@"; do
  printf '%s\n' "$argument" >> "$FAKE_QREDUCE_ARGS"
done
printf 'fake qreduce invoked\n'
if [ "$FAKE_QREDUCE_EXIT" -ne 0 ]; then
  exit "$FAKE_QREDUCE_EXIT"
fi
output=
input=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --out)
      output=$2
      shift 2
      ;;
    *)
      input=$1
      shift
      ;;
  esac
done
test -n "$output"
test -f "$input"
{
  printf '* reduced netlist\n'
  cat "$input"
} > "$output"
""",
        encoding="utf-8",
    )
    qreduce.chmod(0o755)

    trace = tmp_path / "qreduce.args"
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("RCE_QREDUCE", str(qreduce))
    monkeypatch.setenv("FAKE_EXTRACT_OUTPUT", str(tmp_path / "run/top.dspf"))
    monkeypatch.setenv("FAKE_QREDUCE_ARGS", str(trace))
    monkeypatch.setenv("FAKE_QREDUCE_EXIT", str(qreduce_exit))
    return trace


def _install_multi_cell_fake_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install fakes that derive every artifact from the task's run directory."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    qrc = bin_dir / "qrc"
    qrc.write_text(
        """#!/bin/sh
set -eu
printf '* original extracted netlist from %s\n' "$PWD" > "$PWD/top.dspf"
""",
        encoding="utf-8",
    )
    qrc.chmod(0o755)

    qreduce = bin_dir / "qreduce"
    qreduce.write_text(
        """#!/bin/sh
set -eu
original_arguments="$*"
output=
input=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --out)
      output=$2
      shift 2
      ;;
    *)
      input=$1
      shift
      ;;
  esac
done
test -n "$output"
test -f "$input"
printf '%s\n' "$original_arguments" > "$PWD/log/qreduce.args"
printf 'fake qreduce in %s\n' "$PWD"
{
  printf '* reduced netlist from %s\n' "$PWD"
  cat "$input"
} > "$output"
""",
        encoding="utf-8",
    )
    qreduce.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("RCE_QREDUCE", str(qreduce))


def test_completed_extraction_runs_qreduce_and_preserves_both_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = _install_fake_tools(tmp_path, monkeypatch, qreduce_exit=0)

    assert RceRunner(_config(tmp_path)).run() == 0

    original = tmp_path / "run/top.dspf"
    reduced = tmp_path / "run/top.reduced.dspf"
    assert original.read_text(encoding="utf-8") == "* original extracted netlist\n"
    assert reduced.read_text(encoding="utf-8") == (
        "* reduced netlist\n* original extracted netlist\n"
    )
    arguments = trace.read_text(encoding="utf-8").splitlines()
    assert arguments[-1] == str(original)
    assert arguments[arguments.index("--out") + 1] == str(reduced)
    assert arguments[arguments.index("--cpu") + 1] == "2"
    assert "fake qreduce invoked" in (
        tmp_path / "run/log/qreduce.stdout.log"
    ).read_text(encoding="utf-8")
    assert not (tmp_path / "run/log/exit-abnormally").exists()


def test_qreduce_failure_marks_the_rce_run_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_tools(tmp_path, monkeypatch, qreduce_exit=23)

    with pytest.raises(RuntimeError, match="Stage reduction failed with exit code 23"):
        RceRunner(_config(tmp_path)).run()

    assert (tmp_path / "run/top.dspf").read_text(encoding="utf-8") == (
        "* original extracted netlist\n"
    )
    assert not (tmp_path / "run/top.reduced.dspf").exists()
    assert (tmp_path / "run/log/exit-abnormally").read_text(
        encoding="utf-8"
    ) == "Stage reduction failed with exit code 23\n"
    assert "fake qreduce invoked" in (
        tmp_path / "run/log/qreduce.stdout.log"
    ).read_text(encoding="utf-8")


def test_multi_cell_reduction_keeps_parallel_outputs_and_logs_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_multi_cell_fake_tools(tmp_path, monkeypatch)
    task_roots = (tmp_path / "cell_a", tmp_path / "cell_b")
    configs = tuple(_config(root) for root in task_roots)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda cfg: RceRunner(cfg).run(), configs))

    assert results == (0, 0)
    for root in task_roots:
        run_dir = root / "run"
        original = run_dir / "top.dspf"
        reduced = run_dir / "top.reduced.dspf"
        stdout_log = run_dir / "log/qreduce.stdout.log"
        trace = run_dir / "log/qreduce.args"

        assert original.read_text(encoding="utf-8") == (
            f"* original extracted netlist from {run_dir}\n"
        )
        assert reduced.read_text(encoding="utf-8") == (
            f"* reduced netlist from {run_dir}\n"
            f"* original extracted netlist from {run_dir}\n"
        )
        arguments = trace.read_text(encoding="utf-8").split()
        assert arguments[-1] == str(original)
        assert arguments[arguments.index("--out") + 1] == str(reduced)
        assert stdout_log.read_text(encoding="utf-8") == (
            f"fake qreduce in {run_dir}\n"
        )
        assert not (run_dir / "log/exit-abnormally").exists()

    assert not (task_roots[0] / "run/top.reduced.dspf").samefile(
        task_roots[1] / "run/top.reduced.dspf"
    )


@pytest.mark.parametrize(
    ("reduction", "message"),
    (
        (
            {
                "enabled": True,
                "mode": "Selection File",
                "selection_file": "missing.sel",
            },
            "selection file",
        ),
        ({"enabled": True, "output_tag": "bad-tag"}, "output_tag"),
    ),
)
def test_reduction_setup_failure_is_marked_before_extraction(
    tmp_path: Path, reduction: dict[str, object], message: str
) -> None:
    cfg = _config(tmp_path)
    cfg = cfg.replace('reduction', value=reduction)

    with pytest.raises(RuntimeError, match=rf"Reduction setup failed:.*{message}"):
        RceRunner(cfg).run()

    marker = tmp_path / "run/log/exit-abnormally"
    assert marker.read_text(encoding="utf-8").startswith("Reduction setup failed:")
    assert not (tmp_path / "run/top.dspf").exists()


def test_dry_run_lists_reduction_unless_stopped_at_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    qreduce = "/opt/quantus/bin/qreduce"
    monkeypatch.setenv("RCE_QREDUCE", qreduce)

    assert RceRunner(_config(tmp_path / "complete"), dry_run=True).run() == 0
    complete_output = capsys.readouterr().out
    assert f"Running reduction: {qreduce} --type dspf" in complete_output
    assert "--out" in complete_output

    assert (
        RceRunner(
            _config(tmp_path / "stopped"),
            dry_run=True,
            stop_after="extract",
        ).run()
        == 0
    )
    stopped_output = capsys.readouterr().out
    assert "Running extract:" in stopped_output
    assert "Running reduction:" not in stopped_output
