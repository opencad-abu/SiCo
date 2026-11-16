"""Resolve an existing SiCo DRC/LVS TOML into DataHub-visible inputs and outputs."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from ..codec import ArtifactRef, artifact_ref
from ..errors import HubError
from .provenance import RunsetIdentity
from .runset import runset_identity

_KINDS = frozenset({"drc", "lvs"})


@dataclass(frozen=True)
class FlowRequest:
    kind: str
    config: ArtifactRef
    work_root: Path
    run_dir: Path
    log_dir: Path
    db_dir: Path
    top_cell: str
    layout_cell: str
    source_cell: str
    source_path: str
    run_mode: str
    runset: ArtifactRef
    runset_identity: RunsetIdentity
    layer_map: Optional[ArtifactRef]
    source_layout: Optional[ArtifactRef]
    layout_path: str
    svdb_dir: str
    options: Mapping[str, object]

    @property
    def results(self):
        return self.db_dir / "drc" / "cal_drc.out"

    @property
    def summary(self):
        return self.log_dir / "cal_drc.sum"

    @property
    def report(self):
        return self.log_dir / "lvs.report"

    @property
    def svdb_path(self):
        return Path(self.svdb_dir) if self.svdb_dir else None


def _work_root(value):
    if not isinstance(value, str) or not value:
        raise HubError("invalid_arguments", "Invalid Calibre work root")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise HubError("invalid_arguments", "Calibre work root must be absolute")
    try:
        path = path.resolve()
    except OSError as exc:
        raise HubError("invalid_arguments", "Calibre work root is unavailable") from exc
    if not path.is_dir():
        raise HubError("invalid_arguments", "Calibre work root is unavailable")
    return path


def _config_path(value):
    if not isinstance(value, str) or not value:
        raise HubError("invalid_arguments", "Invalid Calibre config path")
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise HubError("invalid_arguments", "Calibre config path must name an absolute file")
    return path.resolve()


def load_flow_config(path):
    source = Path(__file__).resolve().parents[4]
    for relative in ("rce/python", "common/python"):
        root = source / relative
        if root.is_dir() and str(root) not in sys.path:
            sys.path.insert(0, str(root))
    try:
        from rcepy.config import load_config
    except ImportError as exc:
        raise HubError(
            "capability_not_available", "SiCo flow configuration support is unavailable"
        ) from exc
    try:
        return load_config(path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HubError("invalid_arguments", "Calibre flow configuration is invalid") from exc


def _contained(path, root):
    path = Path(path).resolve()
    return path == root or root in path.parents


def _optional_artifact(path):
    if not path:
        return None
    path = Path(path).expanduser()
    if not path.is_absolute() or not path.is_file():
        return None
    return artifact_ref(path)


def _selected_values(cfg, name):
    value = cfg.get("drc", name, default=[])
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def resolve_flow(kind, config_path, work_root, *, tool_version=""):
    """Resolve one flow without launching it and reject output outside the work root."""
    if kind not in _KINDS:
        raise HubError("invalid_arguments", "Unsupported Calibre run kind")
    root = _work_root(work_root)
    config_path = _config_path(config_path)
    cfg = load_flow_config(config_path)
    try:
        run_dir = cfg.run_dir.resolve()
        context = cfg.context()
        run_mode = cfg.calibre_run_mode(kind)
        configured_runset = cfg.path(kind, "runset_file")
        layer_map_path = cfg.path("input", "layout", "layer_map")
        source_layout_path = cfg.path("input", "gds", "file")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HubError("invalid_arguments", "Calibre flow paths are invalid") from exc
    if not _contained(run_dir, root):
        raise HubError("invalid_arguments", "Calibre run directory escapes the work root")
    if not configured_runset or not Path(configured_runset).is_file():
        raise HubError("invalid_arguments", "Calibre runset is unavailable")
    identity = runset_identity(configured_runset, tool_version=tool_version)
    layer_map = _optional_artifact(layer_map_path)
    source_layout = _optional_artifact(source_layout_path)
    options = {
        "input_type": context.input_type,
        "run_mode": run_mode,
    }
    groups = _selected_values(cfg, "rule_select_groups")
    checks = _selected_values(cfg, "rule_select_checks")
    if groups:
        options["rule_select_groups"] = list(groups[:200])
    if checks:
        options["rule_select_checks"] = list(checks[:200])
    return FlowRequest(
        kind=kind,
        config=artifact_ref(config_path),
        work_root=root,
        run_dir=run_dir,
        log_dir=context.log_dir.resolve(),
        db_dir=context.db_dir.resolve(),
        top_cell=context.top_cell or context.layout_cell,
        layout_cell=context.layout_cell,
        source_cell=context.source_cell,
        source_path=context.source_path,
        run_mode=run_mode,
        runset=identity.runset,
        runset_identity=identity,
        layer_map=layer_map,
        source_layout=source_layout,
        layout_path=str(context.layout_path or ""),
        svdb_dir=str(context.svdb_dir or ""),
        options=options,
    )


__all__ = ["FlowRequest", "load_flow_config", "resolve_flow"]
