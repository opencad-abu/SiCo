"""Validate the configured DRC group and check selection."""

from __future__ import annotations

from rcepy.config import RceConfig
from .rule_syntax import RULE_NAME


def selected_rule_names(cfg: RceConfig) -> tuple[str, ...]:
    """Return validated group and check names when Rule Select is enabled."""
    enabled = cfg.flag("drc", "rule_select_enable")
    if not enabled:
        return ()

    selections: list[str] = []
    seen: set[str] = set()
    for option in ("rule_select_groups", "rule_select_checks"):
        configured = cfg.get("drc", option, default=[])
        if not isinstance(configured, (list, tuple)):
            raise ValueError(f"drc.{option} must be a list of strings")
        for index, value in enumerate(configured):
            if not isinstance(value, str):
                raise ValueError(f"drc.{option}[{index}] must be a string")
            name = value.strip()
            if not RULE_NAME.fullmatch(name):
                raise ValueError(
                    f"Invalid DRC rule selection at drc.{option}[{index}]: "
                    f"{value!r}"
                )
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                selections.append(name)

    if not selections:
        raise ValueError(
            "DRC Rule Select is enabled but no rule group or check is selected"
        )
    return tuple(selections)


def selected_rule_groups(cfg: RceConfig) -> tuple[str, ...]:
    """Backward-compatible alias for validated Rule Select names."""
    return selected_rule_names(cfg)
