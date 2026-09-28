"""Restricted Environment Modules output parsing and neutral base setup."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
from typing import Mapping, Optional

from .errors import RequestValidationError


MODULECMD_ENV = "MTS_NETLISTOR_MODULECMD"

# A selected project must not inherit the current Virtuoso project's setup.
# Keep only process identity, display/locale state, Environment Modules
# discovery, and site-wide license selectors.  The project modulefile then
# rebuilds every EDA/project-specific value in a detached snapshot.
_NEUTRAL_ENVIRONMENT_KEYS = frozenset(
    {
        "DBUS_SESSION_BUS_ADDRESS",
        "DISPLAY",
        "HOME",
        "HOST",
        "HOSTNAME",
        "LANG",
        "LANGUAGE",
        "LOGNAME",
        "MODULEPATH",
        "MODULESHOME",
        "MODULES_CMD",
        "MODULES_COLOR",
        "MODULES_REDIRECT_OUTPUT",
        "MODULES_RUN_QUARANTINE",
        "MODULES_SILENT_SHELL_DEBUG",
        "MODULES_VERBOSITY",
        "NLS_LANG",
        "SHELL",
        "TERM",
        "TMP",
        "TMPDIR",
        "TEMP",
        "TZ",
        "USER",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "__MODULES_LMINIT",
    }
)
_NEUTRAL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_LICENSE_ENVIRONMENT_KEYS = frozenset(
    {
        "CDS_LIC_FILE",
        "CDS_LIC_ONLY",
        "CDS_LIC_QUEUE",
        "LM_LICENSE_FILE",
        "MGLS_LICENSE_FILE",
        "SALT_LICENSE_SERVER",
        "SNPSLMD_LICENSE_FILE",
        "CLIOLMD_LICENSE_FILE",
    }
)


def find_modulecmd(
    explicit: str | Path | None = None,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the Environment Modules command without invoking a shell."""

    env = os.environ if environment is None else environment
    candidate = str(explicit or env.get(MODULECMD_ENV, "")).strip()
    choices = [candidate] if candidate else []
    found = shutil.which("modulecmd", path=env.get("PATH"))
    if found:
        choices.append(found)
    choices.append("/bin/modulecmd")
    for value in choices:
        if not value:
            continue
        path = Path(value).expanduser()
        if path.parent == Path("."):
            resolved = shutil.which(value, path=env.get("PATH"))
            if resolved:
                return resolved
        elif path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    raise RequestValidationError(
        "Environment Modules modulecmd is unavailable; set " + MODULECMD_ENV
    )


def _literal_string(node: ast.AST) -> str:
    try:
        value = ast.literal_eval(node)
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(
            "modulecmd environment key/value is not a string literal"
        ) from exc
    if not isinstance(value, str):
        raise RequestValidationError(
            "modulecmd environment key/value is not a string"
        )
    return value


def apply_python_modulecmd(
    output: str,
    environment: dict[str, str],
    *,
    assigned_keys: Optional[set[str]] = None,
    require_status: bool = False,
) -> bool:
    """Apply only the literal environment dialect emitted by modulecmd."""

    try:
        tree = ast.parse(output, mode="exec")
    except SyntaxError as exc:
        raise RequestValidationError(f"modulecmd returned invalid Python: {exc}") from exc
    status = True
    status_seen = False
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            if (
                len(statement.names) != 1
                or statement.names[0].name != "os"
                or statement.names[0].asname is not None
            ):
                raise RequestValidationError("modulecmd emitted an unexpected import")
            continue
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1:
                raise RequestValidationError("modulecmd emitted an unsupported assignment")
            target = statement.targets[0]
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and isinstance(target.value.value, ast.Name)
                and target.value.value.id == "os"
                and target.value.attr == "environ"
            ):
                key_node = target.slice
                if isinstance(key_node, ast.Index):  # pragma: no cover - 3.8 AST
                    key_node = key_node.value
                key = _literal_string(key_node)
                environment[key] = _literal_string(statement.value)
                if assigned_keys is not None:
                    assigned_keys.add(key)
                continue
            if isinstance(target, ast.Name) and target.id == "_mlstatus":
                value = ast.literal_eval(statement.value)
                if not isinstance(value, bool):
                    raise RequestValidationError(
                        "modulecmd returned an invalid status marker"
                    )
                status = value
                status_seen = True
                continue
            raise RequestValidationError("modulecmd emitted an unsupported assignment")
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "pop"
                and isinstance(call.func.value, ast.Attribute)
                and isinstance(call.func.value.value, ast.Name)
                and call.func.value.value.id == "os"
                and call.func.value.attr == "environ"
                and 1 <= len(call.args) <= 2
                and not call.keywords
            ):
                if len(call.args) == 2:
                    try:
                        default = ast.literal_eval(call.args[1])
                    except (TypeError, ValueError) as exc:
                        raise RequestValidationError(
                            "modulecmd emitted an invalid os.environ.pop default"
                        ) from exc
                    if default is not None:
                        raise RequestValidationError(
                            "modulecmd emitted an invalid os.environ.pop default"
                        )
                environment.pop(_literal_string(call.args[0]), None)
                continue
            raise RequestValidationError("modulecmd emitted an unsupported call")
        if isinstance(statement, ast.Delete) and len(statement.targets) == 1:
            target = statement.targets[0]
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and isinstance(target.value.value, ast.Name)
                and target.value.value.id == "os"
                and target.value.attr == "environ"
            ):
                key_node = target.slice
                if isinstance(key_node, ast.Index):  # pragma: no cover - 3.8 AST
                    key_node = key_node.value
                environment.pop(_literal_string(key_node), None)
                continue
            raise RequestValidationError("modulecmd emitted an unsupported delete")
        if isinstance(statement, (ast.Pass, ast.Expr)):
            if isinstance(statement, ast.Expr) and not isinstance(
                statement.value, ast.Constant
            ):
                raise RequestValidationError(
                    "modulecmd emitted an unsupported expression"
                )
            continue
        raise RequestValidationError("modulecmd emitted unsupported Python")
    if require_status and not status_seen:
        raise RequestValidationError("modulecmd did not return a status marker")
    return status


def _is_license_environment_key(name: str) -> bool:
    upper = name.upper()
    return (
        upper in _LICENSE_ENVIRONMENT_KEYS
        or upper.startswith("FLEXLM_")
        or "LICENSE" in upper
        or upper.endswith("_LICENSE_FILE")
        or upper.endswith("_LICENSE_SERVER")
    )


def neutral_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Build a project-independent base for one module evaluation."""

    child = {
        key: str(value)
        for key, value in base.items()
        if key in _NEUTRAL_ENVIRONMENT_KEYS
        or key.startswith("LC_")
        or _is_license_environment_key(key)
    }
    child["PATH"] = _NEUTRAL_PATH
    child.setdefault("USER", os.environ.get("USER", ""))
    child.setdefault("HOME", os.environ.get("HOME", ""))
    for key in ("LOADEDMODULES", "_LMFILES_"):
        child.pop(key, None)
    return child


def prepend_cadence_paths(environment: dict[str, str]) -> None:
    """Make the Cadence executables selected by the module discoverable."""

    paths: list[str] = []
    for variable in ("CDSHOME", "CDSROOT", "CDSDIR"):
        root = str(environment.get(variable, "")).strip()
        if not root:
            continue
        root_path = Path(root).expanduser()
        for candidate in (
            root_path / "tools" / "dfII" / "bin",
            root_path / "tools" / "bin",
            root_path / "bin",
        ):
            if candidate.is_dir():
                rendered = str(candidate.resolve())
                if rendered not in paths:
                    paths.append(rendered)
    for rendered in environment.get("PATH", "").split(os.pathsep):
        if rendered and rendered not in paths:
            paths.append(rendered)
    environment["PATH"] = os.pathsep.join(paths)


__all__ = [
    "MODULECMD_ENV",
    "apply_python_modulecmd",
    "find_modulecmd",
    "neutral_environment",
    "prepend_cadence_paths",
]
