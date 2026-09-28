"""Shared process and output primitives for CAD tool stages."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path


def run_logged_command(
    command: Sequence[str],
    *,
    cwd: Path,
    log_file: Path,
    env: Mapping[str, str],
    output_callback: Callable[[str], None] | None = None,
    dry_run: bool = False,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> int:
    """Run a command while teeing combined output to a log and callback."""
    if dry_run:
        return 0

    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8", errors="replace") as log:
        proc = popen(
            list(command),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env=dict(env),
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            log.flush()
            if output_callback is not None:
                output_callback(line)
        return proc.wait()


def run_stage_command(
    command: Sequence[str],
    *,
    label: str,
    required_file: Path | None,
    cwd: Path,
    log_file: Path,
    env: Mapping[str, str],
    output_callback: Callable[[str], None] | None = None,
    dry_run: bool = False,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> int:
    """Validate and run one generated CAD stage command."""
    if required_file is not None and not required_file.is_file():
        raise FileNotFoundError(
            f"Missing command file for {label}: {required_file}"
        )
    return run_logged_command(
        command,
        cwd=cwd,
        log_file=log_file,
        env=env,
        output_callback=output_callback,
        dry_run=dry_run,
        popen=popen,
    )


def publish_output(produced_file: Path, output_file: Path) -> None:
    """Atomically move a non-empty stage output to its public location."""
    if not produced_file.is_file() or produced_file.stat().st_size <= 0:
        raise RuntimeError(
            f"Stage did not create a non-empty output: {produced_file}"
        )
    try:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        produced_file.replace(output_file)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot publish stage output to {output_file}: {exc}"
        ) from exc


def publish_stage_output(
    produced_file: Path, output_file: Path, *, label: str
) -> None:
    """Publish a stage output while preserving a flow-specific error label."""
    try:
        publish_output(produced_file, output_file)
    except RuntimeError as exc:
        message = str(exc).replace(
            "Stage did not create a non-empty output",
            f"{label} did not create a non-empty output",
        )
        message = message.replace(
            "Cannot publish stage output",
            f"Cannot publish {label} output",
        )
        raise RuntimeError(message) from exc
