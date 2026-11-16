"""Read the geometry-bearing subset of a Calibre ASCII DRC results database."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class RdbError(ValueError):
    """The Calibre ASCII results database is malformed."""


@dataclass(frozen=True)
class RdbShape:
    """One Calibre result polygon in integer database units."""

    rule: str
    points: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class RdbDocument:
    """Top cell, precision, and geometry records from one ASCII RDB."""

    top_cell: str
    precision: int
    shapes: tuple[RdbShape, ...]

    def user_points(self, shape: RdbShape) -> tuple[tuple[float, float], ...]:
        if self.precision <= 0:
            raise RdbError("Calibre RDB precision must be positive")
        scale = float(self.precision)
        return tuple((x / scale, y / scale) for x, y in shape.points)


def _header(lines: list[str]) -> tuple[str, int]:
    if not lines:
        raise RdbError("Calibre ASCII RDB is empty")
    fields = lines[0].split()
    if len(fields) < 2:
        raise RdbError("Calibre ASCII RDB header must be '<top> <precision>'")
    try:
        precision = int(fields[1])
    except ValueError as exc:
        raise RdbError("Calibre ASCII RDB precision is not an integer") from exc
    if not fields[0] or precision <= 0:
        raise RdbError("Calibre ASCII RDB has an invalid header")
    return fields[0], precision


def _numbers(tokens: Iterable[str]) -> tuple[int, ...] | None:
    values = list(tokens)
    if not values:
        return None
    try:
        return tuple(int(value) for value in values)
    except ValueError:
        return None


def _record_points(
    lines: list[str], start: int, count: int
) -> tuple[tuple[tuple[int, int], ...], int]:
    points: list[tuple[int, int]] = []
    index = start
    while index < len(lines) and len(points) < count:
        fields = lines[index].split()
        if fields and fields[0] in _KNOWN_RECORD_HEADS:
            break
        if len(fields) == 2:
            pair = _numbers(fields)
            if pair is not None:
                points.append((pair[0], pair[1]))
                index += 1
                continue
        if len(fields) > 2 and len(fields) % 2 == 0:
            pair = _numbers(fields)
            if pair is not None:
                for offset in range(0, len(pair), 2):
                    points.append((pair[offset], pair[offset + 1]))
                    if len(points) == count:
                        break
                index += 1
                continue
        index += 1
    if len(points) != count:
        raise RdbError(
            f"Calibre RDB record expected {count} points, found {len(points)}"
        )
    return tuple(points), index


_KNOWN_RECORD_HEADS = frozenset({"p", "r", "b"})


def parse_rdb_text(
    text: str, rule_names: Iterable[str]
) -> RdbDocument:
    """Parse polygons for a known set of fill rule names.

    Calibre's ASCII RDB is intentionally simple: a header, then one section per
    rulecheck.  Records beginning with ``p`` carry a polygon vertex count and
    coordinate pairs; ``r`` records carry a rectangle's two corners.
    """
    lines = text.splitlines()
    top_cell, precision = _header(lines)
    known = {str(name) for name in rule_names}
    if not known:
        raise RdbError("at least one Calibre rule name is required")
    shapes: list[RdbShape] = []
    current: str | None = None
    index = 1
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped in known:
            current = stripped
            index += 1
            continue
        if current is None:
            index += 1
            continue
        fields = stripped.split()
        if not fields:
            index += 1
            continue
        head = fields[0]
        if head in {"p", "b"}:
            if len(fields) < 3:
                raise RdbError(f"Calibre RDB {head} record has no vertex count")
            try:
                count = int(fields[2])
            except ValueError as exc:
                raise RdbError("Calibre RDB vertex count is not an integer") from exc
            if count < 3:
                raise RdbError("Calibre RDB polygon has fewer than three vertices")
            points, index = _record_points(lines, index + 1, count)
            shapes.append(RdbShape(current, points))
            continue
        if head == "r":
            values = _numbers(fields[2:])
            if values is None or len(values) < 4:
                raise RdbError("Calibre RDB rectangle record is incomplete")
            x1, y1, x2, y2 = values[:4]
            shapes.append(
                RdbShape(current, ((x1, y1), (x2, y1), (x2, y2), (x1, y2)))
            )
            index += 1
            continue
        index += 1
    return RdbDocument(top_cell, precision, tuple(shapes))


def parse_rdb(path: str | Path, rule_names: Iterable[str]) -> RdbDocument:
    rdb_path = Path(path).expanduser().resolve()
    try:
        text = rdb_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RdbError(f"cannot read Calibre RDB {rdb_path}: {exc}") from exc
    return parse_rdb_text(text, rule_names)
