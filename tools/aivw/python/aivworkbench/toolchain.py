"""Safe module loading and EDA tool qualification."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from typing import Mapping, Sequence

from .errors import EnvironmentError
from .profiles import ToolSpec


_MODULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+/-]*$")
_MODULE_LOAD = re.compile(r"^\s*module\s+load\s+(.+?)\s*$")
_EXPORT = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.+?)\s*$")


@dataclass(frozen=True)
class ToolResult:
    name: str
    command: str
    path: str
    returncode: int | None
    version_output: str
    status: str
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "command": self.command,
            "path": self.path,
            "returncode": self.returncode,
            "version_output": self.version_output,
            "status": self.status,
            "detail": self.detail,
        }


def detach_mps_environment(environment: dict[str, str]) -> None:
    for name in tuple(environment):
        if name.startswith("CDS_MPS_"):
            environment.pop(name, None)


def parse_setup_modules(path: Path) -> tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise EnvironmentError(f"cannot read setup script {path}: {exc}") from exc
    modules: list[str] = []
    for line in lines:
        stripped = line.split("#", 1)[0].rstrip()
        match = _MODULE_LOAD.match(stripped)
        if not match:
            continue
        try:
            values = shlex.split(match.group(1), posix=True)
        except ValueError as exc:
            raise EnvironmentError(f"invalid module declaration in {path}: {line}") from exc
        for value in values:
            if not _MODULE.fullmatch(value):
                raise EnvironmentError(f"unsafe module name in {path}: {value!r}")
            modules.append(value)
    return tuple(modules)


def parse_setup_exports(path: Path, allowed_names: Sequence[str]) -> dict[str, str]:
    """Read literal, whitelisted ``export NAME=value`` declarations only."""
    allowed = set(allowed_names)
    if not allowed or any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) for name in allowed):
        raise EnvironmentError(f"invalid setup export whitelist: {sorted(allowed)!r}")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise EnvironmentError(f"cannot read setup script {path}: {exc}") from exc
    values: dict[str, str] = {}
    for line in lines:
        match = _EXPORT.match(line.split("#", 1)[0].rstrip())
        if not match or match.group(1) not in allowed:
            continue
        name, expression = match.groups()
        try:
            parsed = shlex.split(expression, posix=True)
        except ValueError as exc:
            raise EnvironmentError(f"invalid export declaration in {path}: {line}") from exc
        if len(parsed) != 1 or any(token in expression for token in ("$", "`", ";", "$(")):
            raise EnvironmentError(f"{name} must be one literal setup value")
        if name in values:
            raise EnvironmentError(f"duplicate setup export for {name}")
        values[name] = parsed[0]
    missing = sorted(allowed - set(values))
    if missing:
        raise EnvironmentError(f"setup script is missing required export(s): {', '.join(missing)}")
    return values


def capture_module_environment(
    modules: Sequence[str],
    *,
    base_environment: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, str]:
    values = tuple(str(item) for item in modules)
    if not values or any(not _MODULE.fullmatch(item) for item in values):
        raise EnvironmentError(f"invalid module list: {values!r}")
    script = (
        "set -e\n"
        "module purge >/dev/null 2>&1\n"
        + "module load "
        + " ".join(shlex.quote(item) for item in values)
        + "\nenv -0\n"
    )
    try:
        completed = subprocess.run(
            ["bash", "-lc", script],
            env=dict(os.environ if base_environment is None else base_environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnvironmentError(f"cannot load EDA modules {values!r}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise EnvironmentError(
            f"module load failed for {values!r}: {detail[-1] if detail else completed.returncode}"
        )
    environment: dict[str, str] = {}
    for record in completed.stdout.split(b"\0"):
        if b"=" not in record:
            continue
        raw_name, raw_value = record.split(b"=", 1)
        name = raw_name.decode("utf-8", errors="strict")
        environment[name] = raw_value.decode("utf-8", errors="surrogateescape")
    if "PATH" not in environment:
        raise EnvironmentError("module environment did not contain PATH")
    detach_mps_environment(environment)
    return environment


def safe_environment_summary(environment: Mapping[str, str]) -> dict[str, object]:
    names = ("CDSHOME", "AMSHOME", "SPECTRE_HOME", "UVM_HOME")
    selected = {name: environment[name] for name in names if environment.get(name)}
    digest_text = "\0".join(f"{key}={selected[key]}" for key in sorted(selected))
    return {
        "selected": selected,
        "selected_sha256": hashlib.sha256(digest_text.encode("utf-8")).hexdigest(),
        "mps_detached": not any(name.startswith("CDS_MPS_") for name in environment),
    }


def probe_tools(
    specs: Sequence[ToolSpec],
    environment: Mapping[str, str],
    *,
    timeout: float = 20.0,
) -> tuple[ToolResult, ...]:
    results: list[ToolResult] = []
    for spec in specs:
        resolved = shutil.which(spec.command, path=environment.get("PATH"))
        if not resolved:
            results.append(
                ToolResult(spec.name, spec.command, "", None, "", "BLOCKED", "not on PATH")
            )
            continue
        # Cadence installs expose several commands as symlinks to a generic
        # wrapper that dispatches by argv[0].  Resolving that symlink and then
        # executing ``.cdnWrapper*`` loses the original tool identity.  Keep
        # the absolute launcher path returned by PATH; provenance can still
        # hash or inspect the symlink separately without changing execution.
        path = os.path.abspath(resolved)
        if spec.path_pattern and not re.search(spec.path_pattern, path):
            results.append(
                ToolResult(
                    spec.name,
                    spec.command,
                    path,
                    None,
                    "",
                    "BLOCKED",
                    f"path does not match {spec.path_pattern!r}",
                )
            )
            continue
        if not spec.version_args:
            results.append(ToolResult(spec.name, spec.command, path, 0, "", "PASS"))
            continue
        try:
            completed = subprocess.run(
                [path, *spec.version_args],
                env=dict(environment),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append(
                ToolResult(spec.name, spec.command, path, None, "", "BLOCKED", str(exc))
            )
            continue
        output = "\n".join(
            item.strip() for item in (completed.stdout, completed.stderr) if item.strip()
        )
        matched = not spec.version_pattern or bool(re.search(spec.version_pattern, output))
        status = "PASS" if completed.returncode == 0 and matched else "BLOCKED"
        detail = "" if status == "PASS" else "version command failed or output did not match profile"
        results.append(
            ToolResult(
                spec.name,
                spec.command,
                path,
                completed.returncode,
                output,
                status,
                detail,
            )
        )
    return tuple(results)
