"""Validated dummy-fill rule profile.

The profile is deliberately an adapter only.  Foundry geometry rules stay in
the Calibre deck; this file maps selected metal layers and Calibre result names
to OpenAccess layer-purpose pairs.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping


SCHEMA = "sico.dmyfill.profile.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]*$")


class ProfileError(ValueError):
    """The dummy-fill profile is malformed or internally inconsistent."""


@dataclass(frozen=True)
class RuleTarget:
    """OpenAccess layer-purpose pair emitted by one Calibre result rule."""

    layer: str
    purpose: str


@dataclass(frozen=True)
class LayerRule:
    """Deck switches associated with one selectable metal layer."""

    name: str
    fill_define: str
    thickness_define: str | None = None


@dataclass(frozen=True)
class FillProfile:
    """Site profile for a foundry dummy-fill deck."""

    path: Path
    deck: Path
    layers: Mapping[str, LayerRule]
    rules: Mapping[str, RuleTarget]
    layer_map: Path | None = None
    dm_on_corner: bool = False
    with_sealring: bool = False
    region_margin: float = 0.0

    @property
    def layer_names(self) -> tuple[str, ...]:
        return tuple(self.layers)

    @property
    def rule_names(self) -> tuple[str, ...]:
        return tuple(self.rules)

    def require_layers(self, names: tuple[str, ...]) -> tuple[LayerRule, ...]:
        unknown = [name for name in names if name not in self.layers]
        if unknown:
            raise ProfileError(
                "profile has no dummy-fill layer: " + ", ".join(sorted(unknown))
            )
        return tuple(self.layers[name] for name in names)


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{field} must be a non-empty string")
    return value.strip()


def _identifier(value: Any, field: str) -> str:
    text = _string(value, field)
    if not _IDENTIFIER.fullmatch(text):
        raise ProfileError(f"{field} must be a SKILL identifier: {text!r}")
    return text


def _symbol(value: Any, field: str) -> str:
    text = _string(value, field)
    if not _SYMBOL.fullmatch(text):
        raise ProfileError(f"{field} must be a deck symbol: {text!r}")
    return text


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ProfileError(f"{field} must be true or false")
    return value


def _number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ProfileError(f"{field} must be a number")
    if value < 0:
        raise ProfileError(f"{field} must not be negative")
    return float(value)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProfileError(f"{field} must be an object")
    return value


def _parse_layer(name: str, value: Any) -> LayerRule:
    row = _mapping(value, f"layers.{name}")
    unknown = set(row) - {"fill_define", "thickness_define"}
    if unknown:
        raise ProfileError(
            f"layers.{name} has unknown fields: {', '.join(sorted(unknown))}"
        )
    fill_define = _symbol(row.get("fill_define"), f"layers.{name}.fill_define")
    thickness = row.get("thickness_define")
    if thickness is not None:
        thickness = _symbol(thickness, f"layers.{name}.thickness_define")
    return LayerRule(_identifier(name, "layer name"), fill_define, thickness)


def _parse_rule(name: str, value: Any, layers: Mapping[str, LayerRule]) -> RuleTarget:
    row = _mapping(value, f"rules.{name}")
    unknown = set(row) - {"layer", "purpose"}
    if unknown:
        raise ProfileError(
            f"rules.{name} has unknown fields: {', '.join(sorted(unknown))}"
        )
    layer = _identifier(row.get("layer"), f"rules.{name}.layer")
    if layer not in layers:
        raise ProfileError(f"rules.{name} references unknown layer: {layer}")
    purpose = _identifier(row.get("purpose"), f"rules.{name}.purpose")
    return RuleTarget(layer, purpose)


def load_profile(path: str | Path) -> FillProfile:
    """Load a strict JSON profile and resolve its deck relative to the profile."""
    profile_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"cannot read fill profile {profile_path}: {exc}") from exc
    value = _mapping(raw, "profile")
    if value.get("schema") != SCHEMA:
        raise ProfileError(f"profile schema must be {SCHEMA}")
    deck_value = _string(value.get("deck"), "deck")
    deck = Path(deck_value).expanduser()
    if not deck.is_absolute():
        deck = (profile_path.parent / deck).resolve()
    layer_map_value = value.get("layer_map")
    layer_map = None
    if layer_map_value is not None:
        layer_map = Path(_string(layer_map_value, "layer_map")).expanduser()
        if not layer_map.is_absolute():
            layer_map = (profile_path.parent / layer_map).resolve()
    layers_value = _mapping(value.get("layers"), "layers")
    rules_value = _mapping(value.get("rules"), "rules")
    if not layers_value:
        raise ProfileError("layers must not be empty")
    if not rules_value:
        raise ProfileError("rules must not be empty")
    layers = {str(name): _parse_layer(str(name), row) for name, row in layers_value.items()}
    rules = {
        str(name): _parse_rule(str(name), row, layers)
        for name, row in rules_value.items()
    }
    options = _mapping(value.get("options", {}), "options")
    unknown_options = set(options) - {
        "dm_on_corner",
        "with_sealring",
        "region_margin",
    }
    if unknown_options:
        raise ProfileError(
            "options has unknown fields: " + ", ".join(sorted(unknown_options))
        )
    return FillProfile(
        path=profile_path,
        deck=deck,
        layers=layers,
        rules=rules,
        layer_map=layer_map,
        dm_on_corner=_boolean(options.get("dm_on_corner", False), "options.dm_on_corner"),
        with_sealring=_boolean(options.get("with_sealring", False), "options.with_sealring"),
        region_margin=_number(options.get("region_margin", 0.0), "options.region_margin"),
    )
