"""Calibre TVF expansion with dependency-bound atomic cache publication."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from rcepy.pathutil import cad_temp_dir, restore_eda_temp_environment


def looks_like_tvf(text: str) -> bool:
    prefix = text[:4096]
    return bool(re.search(r"^\s*#!\s*tvf\b|\btvf::|\bforeach\b|\bset\s+", prefix, re.I | re.M))


def expand_tvf_source(
    path: Path,
    *,
    calibre: str | Path | None,
    timeout: float,
    cache_dir: str | Path | None,
    dependency_fingerprint: str,
) -> tuple[Path, bool]:
    executable = str(calibre or os.environ.get("CALIBRE_BIN") or "calibre")
    root = (
        Path(cache_dir).expanduser().resolve()
        if cache_dir is not None
        else default_cache_dir(create=True)
    )
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        if cache_dir is not None:
            raise
        raise
    executable_key = _executable_fingerprint(executable)
    cache_key = hashlib.sha256(
        f"{path}\0{executable_key}\0{dependency_fingerprint}".encode()
    ).hexdigest()[:24]
    cache = root / f"{path.stem}.{cache_key}.svrf"
    if cache.is_file() and cache.stat().st_size:
        return cache, True
    descriptor, output_name = tempfile.mkstemp(
        prefix=".rule-groups-", suffix=".svrf", dir=root
    )
    os.close(descriptor)
    output = Path(output_name)
    output.unlink(missing_ok=True)
    try:
        command = [executable, "-E", str(output), str(path)]
        completed = subprocess.run(
            command,
            cwd=str(path.parent),
            env=calibre_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stdout.strip().splitlines()
            raise RuntimeError(
                f"Calibre TVF expansion failed (exit {completed.returncode})"
                + (f": {detail[-1]}" if detail else "")
            )
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("Calibre TVF expansion did not produce an output file")
        os.replace(output, cache)
        return cache, False
    finally:
        output.unlink(missing_ok=True)


def default_cache_dir(*, create=False) -> Path:
    configured = os.environ.get("DRC_RULE_GROUP_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return cad_temp_dir("drc-rule-groups", create=create)


def calibre_environment() -> dict[str, str]:
    env = restore_eda_temp_environment()
    if "DRC_ORIG_LD_LIBRARY_PATH" in env:
        env["LD_LIBRARY_PATH"] = env["DRC_ORIG_LD_LIBRARY_PATH"]
    return env


def _executable_fingerprint(executable: str) -> str:
    candidate = Path(executable).expanduser()
    if not candidate.is_file():
        resolved = shutil.which(executable)
        if not resolved:
            return executable
        candidate = Path(resolved)
    stat = candidate.stat()
    return f"{candidate.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
