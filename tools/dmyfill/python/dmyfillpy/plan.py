"""Convert Calibre RDB polygons into an undo-friendly SKILL data plan."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping

from .profile import FillProfile, RuleTarget
from .rdb import RdbDocument


_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class PlanError(ValueError):
    """A fill plan cannot be represented as editable OA shapes."""


@dataclass(frozen=True)
class PlannedShape:
    """One editable polygon in Virtuoso user units."""

    layer: str
    purpose: str
    points: tuple[tuple[float, float], ...]
    rule: str


def _canonical(points: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float], ...]:
    unique = list(points)
    if len(unique) > 1 and unique[0] == unique[-1]:
        unique.pop()
    if len(unique) < 3:
        raise PlanError("fill polygon has fewer than three distinct vertices")

    def rotations(values):
        for index in range(len(values)):
            yield tuple(values[index:] + values[:index])

    candidates = list(rotations(unique))
    candidates.extend(rotations(list(reversed(unique))))
    return min(candidates)


def _is_rectangle(points: tuple[tuple[float, float], ...]) -> bool:
    if len(points) != 4:
        return False
    xs = {point[0] for point in points}
    ys = {point[1] for point in points}
    return len(xs) == 2 and len(ys) == 2


def _format_number(value: float) -> str:
    rendered = f"{value:.9f}".rstrip("0").rstrip(".")
    return rendered if rendered not in {"", "-0"} else "0"


def _safe_name(value: str, field: str) -> str:
    if not _SAFE_NAME.fullmatch(value):
        raise PlanError(f"{field} is not a safe SKILL identifier: {value!r}")
    return value


def build_shapes(
    document: RdbDocument,
    profile_or_rules: FillProfile | Mapping[str, RuleTarget],
) -> tuple[PlannedShape, ...]:
    """Map RDB rule names to LPPs, deduplicate, and preserve source order."""
    rules = (
        profile_or_rules.rules
        if isinstance(profile_or_rules, FillProfile)
        else profile_or_rules
    )
    seen: set[tuple[str, str, tuple[tuple[float, float], ...]]] = set()
    result: list[PlannedShape] = []
    for shape in document.shapes:
        target = rules.get(shape.rule)
        if target is None:
            continue
        points = _canonical(document.user_points(shape))
        key = (target.layer, target.purpose, points)
        if key in seen:
            continue
        seen.add(key)
        result.append(PlannedShape(target.layer, target.purpose, points, shape.rule))
    return tuple(result)


def render_plan(shapes: tuple[PlannedShape, ...], *, run_id: str, top_cell: str) -> str:
    """Render a data-only plan consumed by ``SICO_dmyfillApplyPlan``."""
    safe_run = _safe_name(run_id, "run id")
    cell = str(top_cell).strip()
    if not cell:
        raise PlanError("top cell must be non-empty")
    lines = [
        "# dmyfill-plan v1",
        f"# run {safe_run}",
        f"# cell {cell}",
        f"# count {len(shapes)}",
    ]
    for shape in shapes:
        layer = _safe_name(shape.layer, "layer")
        purpose = _safe_name(shape.purpose, "purpose")
        if _is_rectangle(shape.points):
            xs = [point[0] for point in shape.points]
            ys = [point[1] for point in shape.points]
            lines.append(
                "RECT "
                + " ".join(
                    (
                        layer,
                        purpose,
                        _format_number(min(xs)),
                        _format_number(min(ys)),
                        _format_number(max(xs)),
                        _format_number(max(ys)),
                    )
                )
            )
        else:
            points = " ".join(
                f"{_format_number(point[0])} {_format_number(point[1])}"
                for point in shape.points
            )
            lines.append(f"POLY {layer} {purpose} {points}")
    return "\n".join(lines) + "\n"


def write_plan(
    path: str | Path,
    shapes: tuple[PlannedShape, ...],
    *,
    run_id: str,
    top_cell: str,
) -> Path:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_plan(shapes, run_id=run_id, top_cell=top_cell),
        encoding="utf-8",
    )
    return output
