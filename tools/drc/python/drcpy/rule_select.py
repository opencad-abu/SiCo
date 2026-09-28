"""DRC rule discovery orchestration and legacy public imports.

Re-exported types, parsing and config functions reference their sole owners.
Remove these compatibility imports in the next incompatible API version after
supported consumers have migrated to rule_groups and rule_select_config."""

from __future__ import annotations

import subprocess
from pathlib import Path
from .rule_groups import RuleGroupInfo, parse_rule_groups, group_definitions, group_check_members
from .rule_sources import read_rule_sources, flatten_static_includes
from .rule_expansion import looks_like_tvf, expand_tvf_source
from .rule_select_config import selected_rule_names, selected_rule_groups


def read_rule_groups(path: str | Path) -> list[str]:
    """Read a rule file and return its statically discoverable groups."""
    rule_path = Path(path).expanduser().resolve()
    if not rule_path.is_file():
        raise FileNotFoundError(f"Cannot access DRC rule file: {rule_path}")
    sources = read_rule_sources(rule_path)
    return parse_rule_groups(flatten_static_includes(rule_path, sources.texts))


def discover_rule_groups(
    path: str | Path,
    *,
    calibre: str | Path | None = None,
    timeout: float = 60.0,
    cache_dir: str | Path | None = None,
    expand_tvf: bool = True,
) -> RuleGroupInfo:
    """Discover groups using Calibre TVF expansion, with static fallback.

    ``calibre -E`` is the authoritative way to expand compile-time TVF loops.
    It is optional because licensing/tool availability varies by host.  The
    static source parser is always available as a deterministic fallback.
    ``counts`` are best-effort matches for UI display, not Calibre compile
    results.
    """
    rule_path = Path(path).expanduser().resolve()
    if not rule_path.is_file():
        raise FileNotFoundError(f"Cannot access DRC rule file: {rule_path}")
    if timeout <= 0:
        raise ValueError("Rule-group discovery timeout must be positive")
    if cache_dir is not None and not str(cache_dir).strip():
        raise ValueError("Rule-group cache directory must not be empty")

    sources = read_rule_sources(rule_path)
    source_text = flatten_static_includes(rule_path, sources.texts)
    static_definitions = group_definitions(source_text)
    static_groups = [name for name, _ in static_definitions]
    static_members = group_check_members(source_text, static_definitions)
    static_counts = {name: len(members) for name, members in static_members.items()}
    has_tvf_source = any(
        text is not None and looks_like_tvf(text) for text in sources.texts.values()
    )
    if not expand_tvf or not has_tvf_source:
        return RuleGroupInfo(
            tuple(static_groups), static_counts, "static", members=static_members
        )

    try:
        expanded, cached = expand_tvf_source(
            rule_path,
            calibre=calibre,
            timeout=timeout,
            cache_dir=cache_dir,
            dependency_fingerprint=sources.fingerprint,
        )
        expanded_text = expanded.read_text(errors="ignore")
        definitions = group_definitions(expanded_text)
        groups = [name for name, _ in definitions]
        members = group_check_members(expanded_text, definitions)
        counts = {name: len(checks) for name, checks in members.items()}
        if groups:
            return RuleGroupInfo(
                tuple(groups), counts, "calibre", cached=cached, members=members
            )
        error = "Calibre expansion produced no GROUP statements"
    except subprocess.TimeoutExpired:
        error = f"Calibre TVF expansion timed out after {timeout:g} seconds"
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        error = str(exc) or exc.__class__.__name__
    return RuleGroupInfo(
        tuple(static_groups), static_counts, "static", error, members=static_members
    )


__all__ = [
    "RuleGroupInfo", "discover_rule_groups", "parse_rule_groups", "read_rule_groups",
    "selected_rule_groups", "selected_rule_names",
]
