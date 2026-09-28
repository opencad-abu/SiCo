"""Manifest and local Python command fixtures for batch execution tests."""

from __future__ import annotations

from pathlib import Path
import sys


def _quote(value: str | Path) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_manifest(
    root: Path,
    commands: list[str],
    *,
    parallel: int = 2,
) -> Path:
    temp_dir = root / "launch" / ".sico"
    temp_dir.parent.mkdir()
    status_json = root / "status.json"
    status_tsv = root / "status.tsv"
    lines = [
        "[batch]",
        "schema_version = 1",
        'batch_id = "test-batch"',
        'flow = "DRC"',
        f"parallel_cells = {parallel}",
        f"temp_dir = {_quote(temp_dir)}",
        f"status_json = {_quote(status_json)}",
        f"status_tsv = {_quote(status_tsv)}",
    ]
    for index, command in enumerate(commands, start=1):
        run_dir = root / f"run-{index}"
        run_dir.mkdir()
        config = run_dir / "drc.toml"
        config.write_text("[run]\n", encoding="utf-8")
        lines.extend(
            [
                "",
                "[[tasks]]",
                f'id = "{index:03d}"',
                f'label = "lib/cell{index}/layout"',
                f"command = {_quote(command)}",
                'cancel_command = ""',
                f"run_dir = {_quote(run_dir)}",
                f"config = {_quote(config)}",
                f"launch_log = {_quote(run_dir / 'task.log')}",
                'result_path = ""',
                "publication_required = false",
            ]
        )
    manifest = root / "batch.toml"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def _python_command(source: str) -> str:
    import shlex

    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"
