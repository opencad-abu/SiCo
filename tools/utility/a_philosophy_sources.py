"""Read the Git worktree source inventory, including staged and untracked files."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
from pathlib import Path
import subprocess
import tokenize


EXTENSIONS = {
    ".py",
    ".il",
    ".ils",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".sh",
    ".ocn",
    ".cmake",
}
EXCLUDED = {
    ".git",
    ".cad",
    "reference",
    "references",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "training",
    "logs",
    "output",
    "cache",
    ".venv",
    "venv",
}


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)


def read_python(path: Path) -> str:
    with tokenize.open(path) as stream:
        return stream.read()


def excluded(path: Path) -> bool:
    return any(
        part in EXCLUDED or part.startswith((".", "build-", "logs_"))
        for part in path.parts[:-1]
    )


def is_vendor(path: Path) -> bool:
    return any(
        part in {"vendor", "_vendor", "third_party", "third-party"}
        or part.endswith("_vendor")
        for part in path.parts
    )


def source_file(path: Path) -> bool:
    return (
        path.suffix in EXTENSIONS
        or path.name == "CMakeLists.txt"
        or path.name.endswith(".il.src")
    ) and not excluded(path)


@dataclass(frozen=True)
class SourceRow:
    path: str
    lines: int
    test: bool
    vendored: bool
    status: str
    sha256: str

    def record(self):
        return asdict(self)


def source_rows(root: Path) -> tuple[list[SourceRow], list[str]]:
    def names(*args):
        return set(git(root, *args).decode().strip("\0").split("\0")) - {""}

    tracked = names("ls-files", "-z")
    untracked = names("ls-files", "--others", "--exclude-standard", "-z")
    staged = names("diff", "--cached", "--name-only", "--diff-filter=A", "-z")
    rows, errors = [], []
    for value in sorted(tracked | untracked):
        path = Path(value)
        if not source_file(path):
            continue
        source = root / path
        if source.is_symlink():
            errors.append(f"source symlink requires review: {value}")
            continue
        if not source.exists():  # Worktree deletion is not a parse failure.
            continue
        try:
            data = source.read_bytes()
            lines = len(data.splitlines())
        except OSError as exc:
            errors.append(f"{value}: source read failed: {exc}")
            continue
        test = path.name.startswith("test_") or bool(
            {"test", "tests", "testing"} & set(path.parts)
        )
        status = (
            "staged-new"
            if value in staged
            else "tracked"
            if value in tracked
            else "untracked"
        )
        rows.append(
            SourceRow(
                value,
                lines,
                test,
                is_vendor(path),
                status,
                hashlib.sha256(data).hexdigest(),
            )
        )
    return rows, errors


def check_vendors(root, rows, manifest):
    """A directory name alone never grants a third-party exemption."""
    entries = manifest.get("vendored_sources", [])
    pins = {item["path"]: item for item in entries}
    errors = [] if len(entries) == len(pins) else ["duplicate vendored source pin"]
    actual = {row.path: row for row in rows if row.vendored}
    for path, row in actual.items():
        pin = pins.get(path)
        if not pin:
            errors.append(f"unreviewed vendored source: {path}")
        elif pin.get("sha256") != row.sha256:
            errors.append(f"vendored source pin changed: {path}")
    for path, pin in pins.items():
        if path not in actual:
            errors.append(f"stale vendored source pin: {path}")
        if not pin.get("owner") or not (root / pin.get("license", "")).is_file():
            errors.append(f"vendored source requires owner and license: {path}")
    return errors


def base_json(root: Path, ref: str, path: Path):
    import json

    relative = path.resolve().relative_to(root.resolve()).as_posix()
    try:
        return json.loads(git(root, "show", f"{ref}:{relative}"))
    except subprocess.CalledProcessError:
        return None  # First introduction has no previous manifest.
