"""Versioned, read-only project and pilot profiles."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .errors import ProfileError


_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_MODULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+/-]*$")
_PROJECT_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")
_FORBIDDEN_PROJECT_ENV = {
    "HOME",
    "PATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONPATH",
    "CAD_AI_TOKEN",
    "SICO_AI_TOKEN",
    "CAD_CODEX_TOKEN",
}

_LDO_TOPOLOGIES = {"LDO_MASTER", "LDO_AON"}
_LDO_REQUIRED_MAPPINGS = {"amsLDO", "gpdk045", "analogLib", "basic"}
_LDO_REQUIRED_SECTIONS = {"tt", "ff", "ss"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    command: str
    version_args: tuple[str, ...] = ()
    version_pattern: str = ""
    path_pattern: str = ""


@dataclass(frozen=True)
class InputSpec:
    name: str
    path: Path
    kind: str = "file"


@dataclass(frozen=True)
class PilotSpec:
    name: str
    title: str
    purpose: str
    entry: str
    proves: tuple[str, ...]
    does_not_prove: tuple[str, ...]


@dataclass(frozen=True)
class StorageSpec:
    payload_env: str
    target_layout: str


@dataclass(frozen=True)
class Profile:
    name: str
    display_name: str
    workspace_root: Path
    setup_script: Path
    modules: tuple[str, ...]
    tools: tuple[ToolSpec, ...]
    inputs: tuple[InputSpec, ...]
    projects: Mapping[str, Mapping[str, Any]]
    pilots: Mapping[str, PilotSpec]
    source_path: Path
    storage: StorageSpec | None = None


def product_root() -> Path:
    return Path(__file__).resolve().parents[2]


def available_profiles() -> tuple[str, ...]:
    root = product_root() / "profiles"
    return tuple(path.stem for path in sorted(root.glob("*.json")))


def _required_text(value: Mapping[str, Any], key: str) -> str:
    text = str(value.get(key, "")).strip()
    if not text:
        raise ProfileError(f"profile field {key!r} must not be empty")
    return text


def _absolute_path(value: object, label: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        raise ProfileError(f"{label} must be an absolute path: {path}")
    if "xxxx28" in str(path).lower():
        raise ProfileError(f"{label} must not use the excluded workspace")
    return path


def _contains_symlink_below(base: Path, target: Path) -> bool:
    """Reject symlink components introduced below an approved base.

    Deployment aliases (stable mount points) may themselves be symlinks.  The
    profile stores the operator-facing path, so only components at or below
    the canonical workspace root are treated as untrusted substitutions.
    """

    try:
        base_real = base.resolve(strict=False)
        target_abs = target if target.is_absolute() else Path.cwd() / target
        relative = target_abs.resolve(strict=False).relative_to(base_real)
    except (OSError, ValueError):
        return True
    current = base_real
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return target.is_symlink()


def _validate_project_path(path: Path, *, workspace_root: Path, project_root: Path, label: str) -> None:
    """Validate containment and symlink policy for a project-owned path."""

    workspace_real = workspace_root.resolve(strict=False)
    project_real = project_root.resolve(strict=False)
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(workspace_real):
        raise ProfileError(f"{label} escaped workspace_root")
    if not resolved.is_relative_to(project_real):
        raise ProfileError(f"{label} escaped project root")
    if _contains_symlink_below(workspace_real, path):
        raise ProfileError(f"{label} contains a symlink component")


def _validate_ldo_project(project: Mapping[str, Any], *, workspace_root: Path, name: str) -> None:
    """Validate the explicit M3 LDO source/PDK mapping."""

    root_value = project.get("root")
    cds_value = project.get("cds_lib")
    source_cds_value = project.get("source_cds_lib")
    model_value = project.get("model_root")
    library = project.get("library")
    if not isinstance(root_value, str) or not isinstance(cds_value, str) or not isinstance(source_cds_value, str) or not isinstance(model_value, str):
        raise ProfileError("project 'ldo' requires root, cds_lib, source_cds_lib, and model_root")
    if library != "amsLDO":
        raise ProfileError("project 'ldo' library must be amsLDO")
    if project.get("read_only_structure_adapter") not in (None, "ldo.official_ams.v1"):
        raise ProfileError("project 'ldo' read_only_structure_adapter is unsupported")
    root = _absolute_path(root_value, "project ldo root")
    cds = _absolute_path(cds_value, "project ldo cds_lib")
    source_cds = _absolute_path(source_cds_value, "project ldo source_cds_lib")
    model = _absolute_path(model_value, "project ldo model_root")
    if not root.resolve(strict=False).is_relative_to(workspace_root.resolve(strict=False)):
        raise ProfileError("project 'ldo' root escaped workspace_root")
    if root.is_symlink() or _contains_symlink_below(workspace_root.resolve(strict=False), root):
        raise ProfileError("project 'ldo' root contains a symlink component")
    if not cds.resolve(strict=False).is_relative_to(workspace_root.resolve(strict=False)):
        raise ProfileError("project ldo cds_lib escaped workspace_root")
    if cds.is_symlink() or _contains_symlink_below(workspace_root.resolve(strict=False), cds):
        raise ProfileError("project ldo cds_lib contains a symlink component")
    _validate_project_path(source_cds, workspace_root=workspace_root, project_root=root, label="project ldo source_cds_lib")
    _validate_project_path(model, workspace_root=workspace_root, project_root=root, label="project ldo model_root")

    mapping_value = project.get("mapping_cds_lib")
    mapping_roots_value = project.get("mapping_allowed_roots")
    mapping_environment_value = project.get("mapping_environment", {})
    if not isinstance(mapping_value, str) or not isinstance(mapping_roots_value, list):
        raise ProfileError("project 'ldo' mapping_cds_lib and mapping_allowed_roots are required")
    mapping = _absolute_path(mapping_value, "project ldo mapping_cds_lib")
    workspace_real = workspace_root.resolve(strict=False)
    if not mapping.resolve(strict=False).is_relative_to(workspace_real):
        raise ProfileError("project ldo mapping_cds_lib escaped workspace_root")
    if mapping.is_symlink() or _contains_symlink_below(workspace_real, mapping):
        raise ProfileError("project ldo mapping_cds_lib contains a symlink component")
    if not mapping_roots_value or any(not isinstance(item, str) for item in mapping_roots_value):
        raise ProfileError("project 'ldo' mapping_allowed_roots must be a non-empty path array")
    mapping_roots = [_absolute_path(item, "project ldo mapping_allowed_root") for item in mapping_roots_value]
    if not any(mapping.resolve(strict=False).is_relative_to(item.resolve(strict=False)) for item in mapping_roots):
        raise ProfileError("project ldo mapping_cds_lib is outside mapping_allowed_roots")
    if not isinstance(mapping_environment_value, Mapping):
        raise ProfileError("project 'ldo' mapping_environment must be an object")
    for variable, value in mapping_environment_value.items():
        if not _PROJECT_ENV.fullmatch(str(variable)):
            raise ProfileError("project 'ldo' mapping_environment has an invalid variable")
        resolved = _absolute_path(value, f"project ldo mapping_environment {variable}")
        if not resolved.is_dir():
            raise ProfileError(f"project ldo mapping_environment {variable} is not a directory")

    mappings = project.get("required_library_mappings")
    if not isinstance(mappings, list) or set(str(item) for item in mappings) != _LDO_REQUIRED_MAPPINGS or len(mappings) != len(_LDO_REQUIRED_MAPPINGS):
        raise ProfileError("project 'ldo' required_library_mappings must be amsLDO/gpdk045/analogLib/basic exactly once")
    sections = project.get("required_model_sections")
    if not isinstance(sections, list) or set(str(item) for item in sections) != _LDO_REQUIRED_SECTIONS or len(sections) != len(_LDO_REQUIRED_SECTIONS):
        raise ProfileError("project 'ldo' required_model_sections must be tt/ff/ss exactly once")
    connect_rule = project.get("connect_rule")
    if not isinstance(connect_rule, Mapping):
        raise ProfileError("project 'ldo' connect_rule policy is required")
    rule_name = connect_rule.get("name")
    source_relative = connect_rule.get("source_relative_to_ams_home")
    digest = connect_rule.get("sha256")
    policy = connect_rule.get("policy")
    if (
        not isinstance(rule_name, str) or not rule_name
        or not isinstance(source_relative, str) or not source_relative
        or not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
        or policy != "approved_cadence_builtin_staged_read_only"
    ):
        raise ProfileError("project 'ldo' connect_rule must declare approved CR_full_fast source and sha256")
    topologies = project.get("topologies")
    if not isinstance(topologies, Mapping) or set(str(item) for item in topologies) != _LDO_TOPOLOGIES:
        raise ProfileError("project 'ldo' topologies must declare LDO_MASTER and LDO_AON")
    for topology in sorted(_LDO_TOPOLOGIES):
        contract = topologies[topology]
        if not isinstance(contract, Mapping):
            raise ProfileError(f"project 'ldo' topology {topology} must be an object")
        ports = contract.get("ports")
        if not isinstance(ports, list) or any(not isinstance(item, str) or not item for item in ports):
            raise ProfileError(f"project 'ldo' topology {topology} ports must be text array")
        expected = ["VDD", "VSS", "VOUT"] if topology == "LDO_MASTER" else ["VDD", "VSS", "EN", "VOUT"]
        if ports != expected:
            raise ProfileError(f"project 'ldo' topology {topology} ports do not match the approved contract")
        window = contract.get("vdd_window")
        if not isinstance(window, Mapping) or window.get("min") is None or window.get("max") is None:
            raise ProfileError(f"project 'ldo' topology {topology} vdd_window is required")
        try:
            lower = float(window["min"])
            upper = float(window["max"])
        except (TypeError, ValueError):
            raise ProfileError(f"project 'ldo' topology {topology} vdd_window is numeric")
        if not lower < upper:
            raise ProfileError(f"project 'ldo' topology {topology} vdd_window is invalid")
        polarity = contract.get("en_polarity")
        expected_polarity = None if topology == "LDO_MASTER" else "active_high"
        if polarity != expected_polarity:
            raise ProfileError(f"project 'ldo' topology {topology} en_polarity is invalid")


def load_profile(name: str = "amsverify") -> Profile:
    if not _NAME.fullmatch(name):
        raise ProfileError(f"invalid profile name: {name!r}")
    path = product_root() / "profiles" / f"{name}.json"
    if not path.is_file():
        raise ProfileError(f"profile does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"cannot read profile {path}: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ProfileError(f"unsupported profile schema: {path}")
    profile_name = _required_text(payload, "name")
    if profile_name != name:
        raise ProfileError(
            f"profile identity mismatch: requested={name} stored={profile_name}"
        )
    workspace_root = _absolute_path(payload.get("workspace_root", ""), "workspace_root")

    raw_modules = payload.get("modules", ())
    if not isinstance(raw_modules, list) or not raw_modules:
        raise ProfileError("profile modules must be a non-empty array")
    modules = tuple(str(item) for item in raw_modules)
    if any(not _MODULE.fullmatch(item) for item in modules):
        raise ProfileError(f"profile contains an invalid module name: {modules!r}")

    tools = tuple(
        ToolSpec(
            name=_required_text(item, "name"),
            command=_required_text(item, "command"),
            version_args=tuple(str(arg) for arg in item.get("version_args", ())),
            version_pattern=str(item.get("version_pattern", "")),
            path_pattern=str(item.get("path_pattern", "")),
        )
        for item in payload.get("tools", ())
    )
    if not tools:
        raise ProfileError("profile must declare at least one tool")

    inputs = tuple(
        InputSpec(
            name=_required_text(item, "name"),
            path=_absolute_path(item.get("path", ""), f"input {item.get('name', '')}"),
            kind=str(item.get("kind", "file")),
        )
        for item in payload.get("qualification_inputs", ())
    )
    raw_pilots = payload.get("pilots", {})
    if not isinstance(raw_pilots, Mapping):
        raise ProfileError("profile pilots must be an object")
    pilots = {
        str(pilot_name): PilotSpec(
            name=str(pilot_name),
            title=_required_text(item, "title"),
            purpose=_required_text(item, "purpose"),
            entry=_required_text(item, "entry"),
            proves=tuple(str(value) for value in item.get("proves", ())),
            does_not_prove=tuple(
                str(value) for value in item.get("does_not_prove", ())
            ),
        )
        for pilot_name, item in raw_pilots.items()
    }
    if set(pilots) != {"m0-e", "m0-s", "m1-ai"}:
        raise ProfileError("amsVerify profile must define m0-e, m0-s, and m1-ai pilots")

    raw_projects = payload.get("projects", {})
    if not isinstance(raw_projects, Mapping):
        raise ProfileError("profile projects must be an object")
    projects: dict[str, Mapping[str, Any]] = {}
    mapped_libraries: set[str] = set()
    for project_name, raw_project in raw_projects.items():
        if not isinstance(raw_project, Mapping):
            raise ProfileError(f"profile project {project_name!r} must be an object")
        project = dict(raw_project)
        root_value = project.get("root")
        project_root = (
            _absolute_path(root_value, f"project {project_name} root")
            if root_value is not None
            else None
        )
        if project_root is not None and not project_root.resolve(
            strict=False
        ).is_relative_to(workspace_root.resolve(strict=False)):
            raise ProfileError(f"project {project_name!r} root escaped workspace_root")
        library = str(project.get("library", "")).strip()
        cds_lib_value = project.get("cds_lib")
        if library or cds_lib_value is not None:
            if not library or project_root is None or cds_lib_value is None:
                raise ProfileError(
                    f"project {project_name!r} library mapping requires root, library, and cds_lib"
                )
            if library in mapped_libraries:
                raise ProfileError(
                    f"library {library!r} is mapped by multiple projects"
                )
            mapped_libraries.add(library)
            cds_lib = _absolute_path(cds_lib_value, f"project {project_name} cds_lib")
            approved_root = workspace_root if project_name == "ldo" else project_root
            if not cds_lib.resolve(strict=False).is_relative_to(approved_root.resolve(strict=False)):
                raise ProfileError(
                    f"project {project_name!r} cds_lib escaped approved root"
                )
            project["root"] = str(project_root)
            project["cds_lib"] = str(cds_lib)
            project["library"] = library
        raw_environment = project.get("environment", {})
        if not isinstance(raw_environment, Mapping):
            raise ProfileError(
                f"project {project_name!r} environment must be an object"
            )
        environment: dict[str, str] = {}
        for raw_variable, raw_value in raw_environment.items():
            variable = str(raw_variable)
            if (
                not _PROJECT_ENV.fullmatch(variable)
                or variable in _FORBIDDEN_PROJECT_ENV
            ):
                raise ProfileError(
                    f"project {project_name!r} contains forbidden environment name {variable!r}"
                )
            value = _absolute_path(
                raw_value, f"project {project_name} environment {variable}"
            )
            if not value.resolve(strict=False).is_relative_to(
                workspace_root.resolve(strict=False)
            ):
                raise ProfileError(
                    f"project {project_name!r} environment {variable!r} escaped workspace_root"
                )
            environment[variable] = str(value)
        project["environment"] = environment
        projects[str(project_name)] = project
    ldo_project = projects.get("ldo")
    if ldo_project is not None:
        _validate_ldo_project(ldo_project, workspace_root=workspace_root, name="ldo")
    raw_storage = payload.get("storage")
    storage = None
    if raw_storage is not None:
        if not isinstance(raw_storage, Mapping):
            raise ProfileError("profile storage must be an object")
        payload_env = _required_text(raw_storage, "payload_env")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", payload_env):
            raise ProfileError(f"invalid storage payload_env: {payload_env!r}")
        target_layout = _required_text(raw_storage, "target_layout")
        if target_layout != "{lib}.{cell}.{view}/runs/{run_id}":
            raise ProfileError("unsupported storage target_layout")
        storage = StorageSpec(payload_env=payload_env, target_layout=target_layout)
    return Profile(
        name=profile_name,
        display_name=_required_text(payload, "display_name"),
        workspace_root=workspace_root,
        setup_script=_absolute_path(payload.get("setup_script", ""), "setup_script"),
        modules=modules,
        tools=tools,
        inputs=inputs,
        projects=projects,
        pilots=pilots,
        source_path=path,
        storage=storage,
    )
