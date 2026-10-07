"""Physical PVT raw-output file-kind contract."""

from pathlib import Path

_RAW_OUTPUT_KINDS = {"psf": "directory", "shm": "directory", "fsdb": "file"}


def raw_output_kind(raw_output: str) -> str:
    """Return the manifest kind required by this simulator output format."""
    return _RAW_OUTPUT_KINDS[raw_output]


def matches_raw_output_kind(path: Path, kind: str) -> bool:
    try:
        if kind == "directory":
            return path.is_dir()
        if kind == "file":
            return path.is_file()
        return False
    except (OSError, RuntimeError):
        return False


def raw_output_is_empty(path: Path, kind: str) -> bool:
    if kind == "file":
        return path.stat().st_size <= 0
    return not any(path.iterdir())
