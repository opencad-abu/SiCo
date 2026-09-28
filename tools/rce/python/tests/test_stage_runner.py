"""Direct source tests for the RCE stage execution boundary."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from rcepy.stage_runner import Stage, StageExecutor


def _executor(messages: list[str], warnings: list[str], errors: list[str]) -> StageExecutor:
    def fail(message: str):
        errors.append(message)
        raise RuntimeError(message)

    return StageExecutor(
        fail=fail,
        info=messages.append,
        warn=warnings.append,
        output=messages.append,
    )


def _stage(tmp_path: Path, command: str, **kwargs: object) -> Stage:
    return Stage(
        name="fixture",
        command=["/bin/sh", "-c", command],
        cwd=tmp_path,
        log_file=tmp_path / "stage.log",
        **kwargs,
    )


def test_stage_executor_streams_log_and_preserves_environment(tmp_path: Path) -> None:
    messages: list[str] = []
    executor = _executor(messages, [], [])
    stage = _stage(tmp_path, "printf '%s\\n' \"$RCE_FIXTURE\"")

    assert executor.run(
        stage,
        dry_run=False,
        environment={"RCE_FIXTURE": "environment-value"},
        pass_fds=(),
        allow_lvs_mismatch=False,
    ) is False
    assert (tmp_path / "stage.log").read_text() == "environment-value\n"
    assert "environment-value\n" in messages


def test_stage_executor_dry_run_does_not_start_process(tmp_path: Path) -> None:
    messages: list[str] = []
    executor = _executor(messages, [], [])
    stage = _stage(tmp_path, "touch should-not-exist")

    assert executor.run(
        stage,
        dry_run=True,
        environment={},
        pass_fds=(),
        allow_lvs_mismatch=False,
    ) is False
    assert not (tmp_path / "should-not-exist").exists()


@pytest.mark.parametrize(
    ("allow", "exit_code", "expected"),
    ((True, 4, True), (False, 4, False)),
)
def test_stage_executor_preserves_ignored_lvs_result(
    tmp_path: Path, allow: bool, exit_code: int, expected: bool
) -> None:
    messages: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    executor = _executor(messages, warnings, errors)
    stage = _stage(
        tmp_path,
        "printf 'LVS completed. INCORRECT.\\n'; exit %d" % exit_code,
        check="lvs",
    )

    if expected:
        assert executor.run(
            stage,
            dry_run=False,
            environment={},
            pass_fds=(),
            allow_lvs_mismatch=allow,
        ) is True
        assert warnings
    else:
        with pytest.raises(RuntimeError, match="Stage fixture failed"):
            executor.run(
                stage,
                dry_run=False,
                environment={},
                pass_fds=(),
                allow_lvs_mismatch=allow,
            )


def test_stage_executor_rejects_stale_or_empty_expected_output(tmp_path: Path) -> None:
    output = tmp_path / "output.txt"
    output.write_text("stale\n")
    messages: list[str] = []
    errors: list[str] = []
    executor = _executor(messages, [], errors)

    with pytest.raises(RuntimeError, match="did not update expected output"):
        executor.run(
            _stage(tmp_path, "true", expected_paths=(output,)),
            dry_run=False,
            environment={},
            pass_fds=(),
            allow_lvs_mismatch=False,
        )

    with pytest.raises(RuntimeError, match="did not create a non-empty output"):
        executor.run(
            _stage(
                tmp_path,
                ": > empty.txt",
                expected_paths=(tmp_path / "empty.txt",),
                require_nonempty_outputs=True,
            ),
            dry_run=False,
            environment={},
            pass_fds=(),
            allow_lvs_mismatch=False,
        )


def test_stage_executor_rejects_missing_required_command_file(tmp_path: Path) -> None:
    errors: list[str] = []
    executor = _executor([], [], errors)
    stage = _stage(tmp_path, "true", required_file=tmp_path / "missing.cmd")

    with pytest.raises(FileNotFoundError, match="Missing command file"):
        executor.run(
            stage,
            dry_run=False,
            environment={},
            pass_fds=(),
            allow_lvs_mismatch=False,
        )


def test_stage_executor_closes_stdin_and_reports_spawn_error(tmp_path: Path, monkeypatch) -> None:
    errors: list[str] = []
    executor = _executor([], [], errors)
    stage = _stage(tmp_path, "true", stdin_file=tmp_path / "input.txt")
    (tmp_path / "input.txt").write_text("input\n")

    captured: dict[str, object] = {}

    def spawn(*args, **kwargs):
        captured.update(kwargs)
        raise OSError("fixture spawn failure")

    monkeypatch.setattr(subprocess, "Popen", spawn)
    with pytest.raises(RuntimeError, match="could not start"):
        executor.run(
            stage,
            dry_run=False,
            environment=os.environ.copy(),
            pass_fds=(),
            allow_lvs_mismatch=False,
        )
    assert captured["stdin"].closed is True


def test_stage_executor_passes_run_lock_descriptors(tmp_path: Path, monkeypatch) -> None:
    messages: list[str] = []
    executor = _executor(messages, [], [])
    stage = _stage(tmp_path, "true")
    captured: dict[str, object] = {}
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)

    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setattr(subprocess, "Popen", spawn)
        assert executor.run(
            stage,
            dry_run=False,
            environment={},
            pass_fds=(read_fd, write_fd),
            allow_lvs_mismatch=False,
        ) is False
        assert captured["pass_fds"] == (read_fd, write_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)
