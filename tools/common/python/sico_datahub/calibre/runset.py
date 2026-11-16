"""Calibre runset identity and DRC rule catalog through existing drcpy owners."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ..codec import artifact_ref
from ..errors import HubError
from .catalog import RuleCatalog
from .provenance import RunsetIdentity

_LAYER = re.compile(
    r"^\s*(?:tvf::)?LAYER\s+(?:\"([^\"]+)\"|([A-Za-z0-9_.:+@-]+))",
    re.IGNORECASE | re.MULTILINE,
)


def _drcpy():
    try:
        from drcpy import rule_expansion, rule_select, rule_sources, rule_syntax
    except ImportError:
        tools_root = Path(__file__).resolve().parents[4]
        for relative in ("drc/python", "rce/python", "common/python"):
            source = tools_root / relative
            if source.is_dir() and str(source) not in sys.path:
                sys.path.insert(0, str(source))
        try:
            from drcpy import rule_expansion, rule_select, rule_sources, rule_syntax
        except ImportError as exc:
            raise HubError(
                "capability_not_available", "DRC rule discovery is unavailable"
            ) from exc
    return rule_select, rule_sources, rule_syntax, rule_expansion


def _runset_path(path):
    path = Path(path).expanduser().absolute()
    if not path.is_file():
        raise HubError("invalid_arguments", "DRC/LVS runset is unavailable")
    return path


def runset_identity(path, *, tool_version="") -> RunsetIdentity:
    path = _runset_path(path)
    _rule_select, rule_sources, _rule_syntax, _rule_expansion = _drcpy()
    sources = rule_sources.read_rule_sources(path)
    return RunsetIdentity(
        artifact_ref(path),
        sources.fingerprint,
        len(sources.texts),
        tool_version,
    )


def _layers(text):
    result = []
    seen = set()
    for match in _LAYER.finditer(text):
        name = match.group(1) or match.group(2)
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            result.append(name)
            if len(result) >= 200:
                break
    return tuple(result)


def catalog_drc_runset(
    path,
    *,
    calibre=None,
    timeout=60.0,
    cache_dir=None,
    expand_tvf=True,
    tool_version="",
) -> RuleCatalog:
    """Build a non-authoritative catalog; Calibre remains the DRC authority."""
    path = _runset_path(path)
    rule_select, rule_sources, rule_syntax, _rule_expansion = _drcpy()
    try:
        sources = rule_sources.read_rule_sources(path)
        flattened = rule_sources.flatten_static_includes(path, sources.texts)
        info = rule_select.discover_rule_groups(
            path,
            calibre=calibre,
            timeout=timeout,
            cache_dir=cache_dir,
            expand_tvf=expand_tvf,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise HubError("read_failed", "DRC runset catalog failed") from exc
    return RuleCatalog(
        runset=artifact_ref(path),
        dependencies_sha256=sources.fingerprint,
        groups=tuple(info.groups),
        counts=dict(info.counts),
        members={
            name: tuple(values) for name, values in (info.members or {}).items()
        },
        layers=_layers(rule_syntax.strip_comments(flattened)),
        source=info.source,
        error=info.error or "",
        tool_version=tool_version,
    )


def expand_tvf_runset(
    path, *, calibre, timeout, cache_dir, dependency_sha256
):
    """Expand a compile-time TVF deck through Calibre's authoritative preprocessor."""
    path = _runset_path(path)
    _rule_select, _rule_sources, _rule_syntax, rule_expansion = _drcpy()
    try:
        return rule_expansion.expand_tvf_source(
            path,
            calibre=calibre,
            timeout=timeout,
            cache_dir=cache_dir,
            dependency_fingerprint=dependency_sha256,
        )
    except subprocess.TimeoutExpired as exc:
        raise HubError("timeout", "Calibre TVF expansion timed out") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HubError("read_failed", "Calibre TVF expansion failed") from exc


__all__ = ["catalog_drc_runset", "expand_tvf_runset", "runset_identity"]
