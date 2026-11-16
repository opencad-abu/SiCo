"""Resolve a foundry deck or a PDK dummy-fill rule TOML."""

from __future__ import annotations

from pathlib import Path

from .discover import (
    Discovery,
    DiscoveryError,
    _absolute,
    _env,
    _rule_path,
    resolve_profile,
)
from .profile import ProfileError
from .rules import RuleError, RuleFillSpec, load_rule_spec


def _pdk_rule_candidate() -> Path | None:
    pdk = _env("PROJ_PDK_HOME")
    if not pdk:
        return None
    path = Path(pdk).expanduser().resolve() / "dmyfill.toml"
    return path if path.is_file() else None


def _rule_spec(value: str | Path, layer_map: Path | None) -> RuleFillSpec:
    try:
        return load_rule_spec(_rule_path(str(value)), layer_map)
    except RuleError as exc:
        raise DiscoveryError(str(exc)) from exc


def resolve_fill(
    explicit: str | Path | None = None,
    deck: str | Path | None = None,
    toml: str | Path | None = None,
    layer_map: str | Path | None = None,
) -> Discovery:
    """Resolve a deck first, then a PDK rule TOML, with explicit precedence."""
    if explicit:
        return resolve_profile(explicit, layer_map=layer_map)
    deck_value = deck or _env("DMYFILL_FILE")
    toml_value = toml or _env("DMYFILL_TOML")
    map_value = layer_map or _env("TECH_LAYER_MAP")
    resolved_map = _absolute(map_value) if map_value else None
    if deck_value:
        try:
            found = resolve_profile(deck=deck_value, layer_map=layer_map)
        except (DiscoveryError, OSError, ProfileError) as exc:
            raise DiscoveryError(
                f"DMYFILL_FILE/deck is unusable: {exc}"
            ) from exc
        if toml_value:
            return Discovery(
                found.profile,
                found.source,
                found.deck_source,
                note=found.note,
                warnings=(
                    "DMYFILL_TOML ignored: DMYFILL_FILE has priority",
                ),
            )
        return found
    if toml_value:
        return Discovery(
            None,
            "rule",
            deck_source=_rule_path(str(toml_value)),
            rule=_rule_spec(str(toml_value), resolved_map),
        )
    try:
        return resolve_profile(layer_map=layer_map)
    except DiscoveryError as deck_error:
        candidate = _pdk_rule_candidate()
        if candidate is not None:
            return Discovery(
                None,
                "pdk-rule",
                deck_source=candidate,
                rule=_rule_spec(candidate, resolved_map),
            )
        raise DiscoveryError(
            f"{deck_error}; DMYFILL_TOML is not set and no PDK "
            "dmyfill.toml was found"
        ) from deck_error
