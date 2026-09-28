"""Archive prior run artifacts and remove only scratch owned by this invocation."""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .config import DesignContext
from .pathutil import cad_temp_dir

_BACKUP_TAG_PATTERN = r"\d{2}-\d{2}-\d{2}-\d{2}-\d{2}(?:-\d+)?(?:\.\d+)?"
_RUN_BACKUP_PATTERN = re.compile(
    rf"^(?:(?:db|log)\.{_BACKUP_TAG_PATTERN}|"
    rf".+\.(?:dspf|spf|sp|spef|scs|sqr|agl)\.{_BACKUP_TAG_PATTERN})$",
    re.IGNORECASE,
)


def archive_previous_outputs(ctx: DesignContext, outputs: set[Path]) -> None:
    targets = [path for path in (ctx.db_dir, ctx.log_dir) if path.exists()]
    for output in outputs:
        covered = any(output == root or root in output.parents for root in (ctx.db_dir, ctx.log_dir))
        if output.is_file() and not covered:
            targets.append(output)
    if not targets:
        return
    tag = datetime.now().strftime("%m-%d-%H-%M-%S")
    for path in targets:
        backup = path.with_name(f"{path.name}.{tag}")
        counter = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.{tag}.{counter}")
            counter += 1
        shutil.move(str(path), str(backup))


def archive_completed_config(run_dir: Path, log_dir: Path, config_path: Path) -> Path | None:
    try:
        config_in_run_root = config_path.resolve().parent == run_dir.resolve()
    except OSError:
        config_in_run_root = False
    if config_in_run_root and config_path.is_file():
        archived_config = log_dir / config_path.name
        if archived_config.exists():
            archived_config.unlink()
        shutil.move(str(config_path), str(archived_config))
        return archived_config
    return None


def protected_root_names(
    run_dir: Path, *,
    managed_names: tuple[str, ...],
    initial_root_names: set[str],
    output_paths: tuple[Path, ...],
) -> set[str]:
    keep_names = set(managed_names) | initial_root_names
    cad_root = cad_temp_dir()
    try:
        cad_in_run_root = os.path.samefile(cad_root.parent, run_dir)
    except OSError:
        cad_in_run_root = False
    if cad_in_run_root:
        keep_names.add(cad_root.name)
    for output_path in output_paths:
        try:
            relative_output = output_path.resolve().relative_to(
                run_dir.resolve()
            )
        except ValueError:
            pass
        else:
            if relative_output.parts:
                keep_names.add(relative_output.parts[0])

    return keep_names


def clean_completed_run(run_dir: Path, keep_names: set[str]) -> None:
    for entry in run_dir.iterdir():
        if entry.name in keep_names:
            continue
        # Both the SKILL launcher and the direct Python entry point
        # archive an existing run with this timestamp convention.
        # Those archives are user recovery data, not tool scratch.
        if _RUN_BACKUP_PATTERN.fullmatch(entry.name):
            continue
        if entry.is_file() and entry.suffix.casefold() == ".dspf":
            continue
        # Delete only known scratch created by this invocation. Unknown
        # files can be user inputs, reports, or concurrent user work.
        if entry.name != "qrcTemp" and not (
            entry.name.startswith("_xrc") and entry.name.endswith(".cal_")
        ):
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
