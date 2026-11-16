"""Generate a generic Calibre fill deck from PDK dummy-fill rules."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Sequence

from .layer_map import LayerMap, load_layer_map
from .rules import RuleFillSpec, RuleLayer


class RuleFillError(ValueError):
    """The rule source cannot be converted into a valid fill deck."""


@dataclass(frozen=True)
class RuleDeck:
    """Generated SVRF and the metadata needed by the runner."""

    text: str
    rule_names: tuple[str, ...]
    layers: tuple[str, ...]


def _number(value: float) -> str:
    text = f"{value:.9f}".rstrip("0").rstrip(".")
    return text if text not in {"", "-0"} else "0"


def _quote(value: str | Path) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _tile(layer: RuleLayer) -> tuple[float, float]:
    tile = (layer.tile_min + layer.tile_max) / 2.0
    tile = max(tile, math.sqrt(layer.area_min))
    tile = min(tile, math.sqrt(layer.area_max), layer.width_max)
    if tile < layer.tile_min or tile > layer.tile_max:
        raise RuleFillError(f"cannot choose a tile for layer {layer.name}")
    pitch = tile / math.sqrt(layer.target_density)
    space = max(layer.space, pitch - tile)
    return tile, space


def _layer_index(name: str) -> int | None:
    match = re.search(r"([0-9]+)$", name)
    return int(match.group(1)) if match else None


def _unused_stream(layer_map: LayerMap) -> int:
    """Choose a virtual stream layer that cannot alias a PDK layer."""
    used = {entry.stream for entry in layer_map.entries.values()}
    # 500 is known to work in the TN65/GDS path.  Stay in the conservative
    # original-layer range; some Calibre builds treat very large virtual layer
    # numbers as special/empty layers.
    for candidate in range(500, 10000):
        if candidate not in used:
            return candidate
    raise RuleFillError("no unused stream layer is available for the fill window")


def _or_terms(
    lines: list[str],
    target: str,
    terms: Sequence[str],
) -> str:
    """Emit binary ``OR`` operations accepted by the Calibre SVRF parser.

    Calibre rejects an unparenthesized chain such as ``A OR B OR C`` with
    LOC2.  Keeping each expression binary is also easier to diagnose than a
    deeply nested parenthesis expression.
    """
    if not terms:
        raise RuleFillError(f"no block terms for {target}")
    if len(terms) == 1:
        lines.append(f"{target} = COPY {terms[0]}")
        return target
    previous = terms[0]
    for index, term in enumerate(terms[1:], 1):
        result = target if index == len(terms) - 1 else f"{target}_OR_{index}"
        lines.append(f"{result} = {previous} OR {term}")
        previous = result
    return target


def generate_rule_deck(
    spec: RuleFillSpec,
    *,
    layout_path: str | Path,
    top_cell: str,
    output_rdb: str | Path,
    summary_path: str | Path,
    layers: tuple[str, ...],
    region: tuple[float, float, float, float],
) -> RuleDeck:
    """Generate a generic SVRF deck that emits fill bboxes to ASCII RDB."""
    selected = spec.require_layers(layers)
    if len(region) != 4 or not all(isinstance(value, (int, float)) for value in region):
        raise RuleFillError("rule fill requires a four-number region")
    x1, y1, x2, y2 = region
    if not (x1 < x2 and y1 < y2):
        raise RuleFillError("rule fill region must have positive area")
    layer_map = load_layer_map(spec.layer_map)
    window_stream = _unused_stream(layer_map)
    lines: list[str] = [
        f"LAYOUT PATH {_quote(Path(layout_path).expanduser().resolve())}",
        f"LAYOUT PRIMARY {_quote(top_cell)}",
        "LAYOUT SYSTEM GDSII",
        f"DRC RESULTS DATABASE {_quote(Path(output_rdb).expanduser().resolve())} ASCII",
        f"DRC SUMMARY REPORT {_quote(Path(summary_path).expanduser().resolve())}",
        "DRC MAXIMUM RESULTS ALL",
        f"LAYER ChipWindow {window_stream}",
        f"POLYGON {_number(x1)} {_number(y1)} {_number(x2)} {_number(y2)} ChipWindow",
        "CHIP = PUSH ChipWindow",
    ]
    if spec.edge:
        lines.append(f"CHIP_SAFE = SIZE CHIP BY -{_number(spec.edge)}")
    else:
        lines.append("CHIP_SAFE = COPY CHIP")

    input_symbols: dict[str, str] = {}
    for layer in selected:
        drawing = layer_map.require(layer.name, "drawing")
        symbol = f"IN_{layer.name}"
        input_symbols[layer.name] = symbol
        lines.append(f"LAYER {symbol} {drawing.stream} {drawing.datatype}")

    blocker_symbols: dict[str, str] = {}
    for name in spec.blockers:
        entry = layer_map.require(name, "drawing")
        symbol = f"BLK_{name}"
        blocker_symbols[name] = symbol
        lines.append(f"LAYER {symbol} {entry.stream} {entry.datatype}")

    for layer in selected:
        index = _layer_index(layer.name)
        if index is None:
            continue
        entry = layer_map.entries.get(("DMEXCL", f"dummy{index}"))
        if entry is None:
            continue
        symbol = f"BLK_DMEXCL_{index}"
        blocker_symbols[f"DMEXCL{index}"] = symbol
        lines.append(f"LAYER {symbol} {entry.stream} {entry.datatype}")

    for layer in selected:
        symbol = input_symbols[layer.name]
        metal_block = f"{layer.name}_METAL_BLOCK"
        block_terms = [metal_block]
        lines.append(
            f"{metal_block} = SIZE {symbol} BY {_number(layer.metal_space)}"
        )
        if spec.wide_width is not None and spec.wide_space is not None:
            wide = f"WIDE_{layer.name}"
            lines.append(f"{wide} = {symbol} WITH WIDTH > {_number(spec.wide_width)}")
            wide_block = f"{layer.name}_WIDE_BLOCK"
            lines.append(f"{wide_block} = SIZE {wide} BY {_number(spec.wide_space)}")
            block_terms.append(wide_block)
        for name, spacing in spec.blockers.items():
            blocker = blocker_symbols[name]
            if spacing:
                blocker_block = f"{layer.name}_{name}_BLOCK"
                lines.append(
                    f"{blocker_block} = SIZE {blocker} BY {_number(spacing)}"
                )
                block_terms.append(blocker_block)
            else:
                block_terms.append(blocker)
        dmexcl = blocker_symbols.get(f"DMEXCL{_layer_index(layer.name)}")
        if dmexcl:
            block_terms.append(dmexcl)
        tile, space = _tile(layer)
        region_name = f"REGION_{layer.name}"
        geom_name = f"GEOM_{layer.name}"
        block = _or_terms(lines, block_name(layer), block_terms)
        lines.extend(
            [
                f"{region_name} = CHIP_SAFE NOT {block}",
                (
                    f"{geom_name} = RECTANGLES {_number(tile)} {_number(tile)} "
                    f"{_number(space)} OFFSET 0 INSIDE OF LAYER {region_name}"
                ),
                f"{layer.name}_FILL {{ EXTENTS {geom_name} }}",
            ]
        )
    return RuleDeck(
        "\n".join(lines) + "\n",
        tuple(layer.rule_name for layer in selected),
        tuple(layer.name for layer in selected),
    )


def block_name(layer: RuleLayer) -> str:
    return f"{layer.name}_BLK"
