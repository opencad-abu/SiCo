"""Single stage execution and output validation for the RCE backend."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NoReturn

from .stage_result import (
    is_completed_lvs_mismatch,
    path_signature,
)


@dataclass(frozen=True)
class Stage:
    """Immutable command and output contract for one RCE stage."""

    name: str
    command: list[str]
    cwd: Path
    log_file: Path
    required_file: Path | None = None
    check: str | None = None
    stdin_file: Path | None = None
    expected_paths: tuple[Path, ...] = ()
    require_nonempty_outputs: bool = False


class StageExecutor:
    """Run one stage while keeping process and output checks together."""

    def __init__(
        self,
        *,
        fail: Callable[[str], NoReturn],
        info: Callable[[str], None],
        warn: Callable[[str], None],
        output: Callable[[str], None],
    ) -> None:
        self._fail = fail
        self._info = info
        self._warn = warn
        self._output = output

    def run(
        self,
        stage: Stage,
        *,
        dry_run: bool,
        environment: dict[str, str],
        pass_fds: tuple[int, ...],
        allow_lvs_mismatch: bool,
    ) -> bool:
        """Run a stage and return whether an ignored LVS mismatch was found."""

        if stage.required_file and not stage.required_file.is_file():
            raise FileNotFoundError(
                f"Missing command file for stage {stage.name}: {stage.required_file}"
            )
        self._info(f"Running {stage.name}: {' '.join(stage.command)}")
        if stage.name == "cdl":
            (stage.cwd / "control").touch()
        if dry_run:
            return False

        before = {path: path_signature(path) for path in stage.expected_paths}
        with stage.log_file.open("w", encoding="utf-8", errors="replace") as log:
            stdin = (
                stage.stdin_file.open("r", encoding="utf-8", errors="replace")
                if stage.stdin_file
                else None
            )
            try:
                try:
                    proc = subprocess.Popen(
                        stage.command,
                        cwd=str(stage.cwd),
                        stdin=stdin,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        errors="replace",
                        env=environment,
                        pass_fds=pass_fds,
                    )
                except OSError as exc:
                    self._fail(f"Stage {stage.name} could not start: {exc}")
                assert proc.stdout is not None
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                    self._output(line)
                exit_code = proc.wait()
            finally:
                if stdin:
                    stdin.close()

        ignored = False
        if stage.check == "lvs":
            ignored = self._check_lvs(stage.log_file, exit_code, allow_lvs_mismatch)
        if exit_code != 0 and not ignored:
            self._fail(f"Stage {stage.name} failed with exit code {exit_code}")
        elif stage.check == "xrc":
            self._check_xrc(stage.log_file)
        self._check_outputs(stage, before)
        return ignored

    def _check_lvs(
        self, log_file: Path, exit_code: int, allow_mismatch: bool
    ) -> bool:
        text = log_file.read_text(errors="ignore") if log_file.is_file() else ""
        if allow_mismatch and is_completed_lvs_mismatch(text):
            self._warn(
                "LVS completed INCORRECT; continuing because lvs.ignore_error=true. "
                "Output is for non-signoff debug analysis only."
            )
            return True
        if exit_code == 0 and "LVS completed. CORRECT." not in text:
            self._fail(
                "LVS did not complete cleanly. Set lvs.ignore_error=true only "
                "for non-signoff debug runs."
            )
        return False

    def _check_xrc(self, log_file: Path) -> None:
        text = log_file.read_text(errors="ignore") if log_file.is_file() else ""
        match = re.search(r"\bxRC Errors\s*=\s*(\d+)", text, re.IGNORECASE)
        if not match or int(match.group(1)) != 0:
            self._fail(
                f"Calibre XRC reported errors or an incomplete summary in {log_file}"
            )

    def _check_outputs(
        self, stage: Stage, before: dict[Path, tuple[int, int, int] | None]
    ) -> None:
        for path in stage.expected_paths:
            after = path_signature(path)
            if after is None:
                self._fail(f"Stage {stage.name} did not create expected output: {path}")
            if stage.require_nonempty_outputs and path.is_file() and after[1] == 0:
                self._fail(
                    f"Stage {stage.name} did not create a non-empty output: {path}"
                )
            if before[path] is not None and before[path] == after:
                self._fail(f"Stage {stage.name} did not update expected output: {path}")


__all__ = ["Stage", "StageExecutor"]
