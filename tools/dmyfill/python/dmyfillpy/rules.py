"""Strict, compact PDK dummy-fill rule input."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

try:
    import tomllib
except ImportError:  # Python 3.9 runtime
    import tomli as tomllib

from .profile import RuleTarget


SCHEMA = "sico.dmyfill.rules.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class RuleError(ValueError):
    """The PDK dummy-fill rule TOML is malformed or inconsistent."""


@dataclass(frozen=True)
class RuleLayer:
    """Geometry parameters for one selectable metal layer."""

    name: str
    width_min: float
    width_max: float
    space: float
    metal_space: float
    area_min: float
    area_max: float
    tile_min: float
    tile_max: float
    target_density: float

    @property
    def rule_name(self) -> str:
        return f"{self.name}_FILL"


@dataclass(frozen=True)
class RuleFillSpec:
    """Validated rule source used to generate a generic fill deck."""

    path: Path
    layer_map: Path
    source: str
    unit: str
    target_density: float
    edge: float
    wide_width: float | None
    wide_space: float | None
    blockers: Mapping[str, float]
    layers: Mapping[str, RuleLayer]

    @property
    def layer_names(self) -> tuple[str, ...]:
        return tuple(self.layers)

    @property
    def rule_names(self) -> tuple[str, ...]:
        return tuple(layer.rule_name for layer in self.layers.values())

    @property
    def rules(self) -> Mapping[str, RuleTarget]:
        return {
            layer.rule_name: RuleTarget(layer.name, "dummy")
            for layer in self.layers.values()
        }

    def require_layers(self, names: tuple[str, ...]) -> tuple[RuleLayer, ...]:
        unknown = [name for name in names if name not in self.layers]
        if unknown:
            raise RuleError(
                "rule source has no dummy-fill layer: "
                + ", ".join(sorted(unknown))
            )
        return tuple(self.layers[name] for name in names)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RuleError(f"{field} must be a table")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuleError(f"{field} must be a non-empty string")
    return value.strip()


def _identifier(value: Any, field: str) -> str:
    text = _string(value, field)
    if not _IDENTIFIER.fullmatch(text):
        raise RuleError(f"{field} must be a SKILL identifier: {text!r}")
    return text


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RuleError(f"{field} must be a number")
    result = float(value)
    if result < 0 or (positive and result == 0):
        raise RuleError(f"{field} must be {'positive' if positive else 'non-negative'}")
    return result


def _range(value: Any, field: str, *, positive: bool = False) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise RuleError(f"{field} must be a two-number array")
    low = _number(value[0], f"{field}[0]", positive=positive)
    high = _number(value[1], f"{field}[1]", positive=positive)
    if low > high:
        raise RuleError(f"{field} must be ordered low to high")
    return low, high


def _blockers(value: Any) -> Mapping[str, float]:
    if value is None:
        return {}
    table = _mapping(value, "defaults.blockers")
    result: dict[str, float] = {}
    for name, spacing in table.items():
        layer = _identifier(name, f"defaults.blockers.{name}")
        result[layer] = _number(
            spacing, f"defaults.blockers.{layer}", positive=False
        )
    return result


def _required(
    row: Mapping[str, Any],
    field: str,
    owner: str,
) -> Any:
    if field not in row:
        raise RuleError(f"{owner}.{field} is required")
    return row[field]


def _layer_from_group(
    name: str,
    row: Mapping[str, Any],
    *,
    target_density: float,
) -> RuleLayer:
    owner = f"groups.{name}"
    width_min, width_max = _range(
        _required(row, "width", owner),
        f"{owner}.width",
        positive=True,
    )
    space = _number(
        _required(row, "space", owner),
        f"{owner}.space",
        positive=True,
    )
    metal_space = _number(
        row.get("metal_space", space),
        f"{owner}.metal_space",
        positive=True,
    )
    area_min, area_max = _range(
        _required(row, "area", owner),
        f"{owner}.area",
        positive=True,
    )
    tile_min, tile_max = _range(
        _required(row, "tile", owner),
        f"{owner}.tile",
        positive=True,
    )
    group_density = row.get("target_density", target_density)
    density = _number(
        group_density, f"groups.{name}.target_density", positive=True
    )
    if density > 1:
        raise RuleError(f"groups.{name}.target_density must be <= 1")
    if tile_min < width_min or tile_max > width_max:
        raise RuleError(f"groups.{name}.tile must lie within width")
    if tile_max * tile_max < area_min:
        raise RuleError(f"groups.{name}.tile cannot satisfy minimum area")
    if tile_min * tile_min > area_max:
        raise RuleError(f"groups.{name}.tile cannot satisfy maximum area")
    return RuleLayer(
        name="",
        width_min=width_min,
        width_max=width_max,
        space=space,
        metal_space=metal_space,
        area_min=area_min,
        area_max=area_max,
        tile_min=tile_min,
        tile_max=tile_max,
        target_density=density,
    )


def load_rule_spec(
    path: str | Path,
    layer_map: str | Path | None = None,
) -> RuleFillSpec:
    """Load and strictly validate a PDK rule TOML."""
    source_path = Path(path).expanduser().resolve()
    try:
        data = tomllib.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuleError(f"cannot read dummy-fill rules {source_path}: {exc}") from exc
    root = _mapping(data, "rules")
    unknown = set(root) - {
        "format",
        "unit",
        "source",
        "target_density",
        "edge",
        "defaults",
        "groups",
    }
    if unknown:
        raise RuleError("unknown top-level fields: " + ", ".join(sorted(unknown)))
    if root.get("format") != SCHEMA:
        raise RuleError(f"format must be {SCHEMA}")
    unit = _string(root.get("unit"), "unit")
    if unit != "um":
        raise RuleError("unit must be 'um'")
    source = _string(root.get("source"), "source")
    target_density = _number(
        root.get("target_density"), "target_density", positive=True
    )
    if target_density > 1:
        raise RuleError("target_density must be <= 1")
    edge = _number(root.get("edge"), "edge", positive=False)
    defaults = _mapping(root.get("defaults", {}), "defaults")
    unknown = set(defaults) - {"wide_width", "wide_space", "blockers"}
    if unknown:
        raise RuleError(
            "unknown defaults fields: " + ", ".join(sorted(unknown))
        )
    wide_width = defaults.get("wide_width")
    wide_space = defaults.get("wide_space")
    if (wide_width is None) != (wide_space is None):
        raise RuleError("defaults.wide_width and wide_space must be given together")
    if wide_width is not None:
        wide_width = _number(wide_width, "defaults.wide_width", positive=True)
        wide_space = _number(wide_space, "defaults.wide_space", positive=True)
    blockers = _blockers(defaults.get("blockers"))
    groups = _mapping(root.get("groups"), "groups")
    if not groups:
        raise RuleError("groups must not be empty")
    layers: dict[str, RuleLayer] = {}
    for group_name, group_value in groups.items():
        row = _mapping(group_value, f"groups.{group_name}")
        unknown = set(row) - {
            "layers",
            "width",
            "space",
            "metal_space",
            "area",
            "tile",
            "target_density",
        }
        if unknown:
            raise RuleError(
                f"groups.{group_name} has unknown fields: "
                + ", ".join(sorted(unknown))
            )
        names = row.get("layers")
        if not isinstance(names, list) or not names:
            raise RuleError(f"groups.{group_name}.layers must be a non-empty array")
        layer = _layer_from_group(str(group_name), row, target_density=target_density)
        for name in names:
            layer_name = _identifier(name, f"groups.{group_name}.layers")
            if layer_name in layers:
                raise RuleError(f"layer is declared more than once: {layer_name}")
            layers[layer_name] = RuleLayer(
                name=layer_name,
                width_min=layer.width_min,
                width_max=layer.width_max,
                space=layer.space,
                metal_space=layer.metal_space,
                area_min=layer.area_min,
                area_max=layer.area_max,
                tile_min=layer.tile_min,
                tile_max=layer.tile_max,
                target_density=layer.target_density,
            )
    map_path = Path(layer_map).expanduser().resolve() if layer_map else None
    if map_path is None:
        raise RuleError("TECH_LAYER_MAP is required for dummy-fill rules")
    if not map_path.is_file():
        raise RuleError(f"TECH_LAYER_MAP is unavailable: {map_path}")
    return RuleFillSpec(
        path=source_path,
        layer_map=map_path,
        source=source,
        unit=unit,
        target_density=target_density,
        edge=edge,
        wide_width=wide_width,
        wide_space=wide_space,
        blockers=blockers,
        layers=layers,
    )
