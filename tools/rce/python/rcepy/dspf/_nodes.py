"""Bounded node identity and ownership cache used during DSPF ingest."""

from __future__ import annotations

from typing import NamedTuple

from ._sqlite import sqlite3


_CACHE_SIZE = 100_000
_INSERT_BUFFER_SIZE = 5000
_SEEN_BYTES = 8 * 1024 * 1024


class _NodeState(NamedTuple):
    node_id: int
    net_id: int | None
    ownership: int
    kind: str
    x: float | None
    y: float | None
    layer: str | None
    source_line: int | None


class NodeStore:
    """Resolve node ids with bounded memory and batched database writes."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.cache: dict[tuple[int, str], _NodeState] = {}
        row = connection.execute("SELECT COALESCE(MAX(id),0) FROM node").fetchone()
        self.next_id = int(row[0]) + 1
        self.query_before_insert = self.next_id != 1
        self.seen: bytearray | None = None
        self.dirty: dict[int, _NodeState] = {}
        self.pending: dict[int, tuple[int, str, _NodeState]] = {}

    def get_element(
        self,
        subcircuit_id: int,
        name: str,
        net_id: int | None,
        confidence: int,
        ground: bool,
        source_line: int,
    ) -> int:
        """Return an endpoint id without running merge logic for stable nodes."""
        key = (subcircuit_id, name)
        cached = self.cache.get(key)
        if cached is not None:
            ownership_changes = net_id is not None and (
                confidence > cached.ownership
                or (confidence == cached.ownership and cached.net_id is None)
            )
            kind_changes = ground and cached.kind != "ground"
            if not ownership_changes and not kind_changes:
                if self.seen is not None:
                    self.cache.pop(key)
                    self.cache[key] = cached
                return cached.node_id
        return self.get(
            subcircuit_id,
            name,
            net_id=net_id,
            confidence=confidence,
            kind="ground" if ground else "internal",
            source_line=source_line,
        )

    def get(
        self, subcircuit_id: int, name: str, *, net_id: int | None = None,
        confidence: int = 0, kind: str = "internal", x: float | None = None,
        y: float | None = None, layer: str | None = None,
        source_line: int | None = None,
    ) -> int:
        key = (subcircuit_id, name)
        cached = self.cache.get(key)
        if cached is None:
            row = None
            if self.query_before_insert or (
                self.seen is not None and self._possibly_seen(key)
            ):
                self.flush()
                row = self.connection.execute(
                    "SELECT id,net_id,ownership,kind,x,y,layer,source_line "
                    "FROM node WHERE subcircuit_id=? AND name=?",
                    key,
                ).fetchone()
            if row is None:
                cached = _NodeState(
                    self.next_id, net_id, confidence, kind,
                    x, y, layer, source_line,
                )
                self.next_id += 1
                self.pending[cached.node_id] = (subcircuit_id, name, cached)
                self._remember(key, cached)
                if len(self.pending) >= _INSERT_BUFFER_SIZE:
                    self.flush()
                return cached.node_id
            cached = _NodeState(
                row["id"], row["net_id"], row["ownership"], row["kind"],
                row["x"], row["y"], row["layer"], row["source_line"],
            )
        node_id = cached.node_id
        old_net = cached.net_id
        old_confidence = cached.ownership
        ownership_changes = net_id is not None and (
            confidence > old_confidence
            or (confidence == old_confidence and old_net is None)
        )
        kind_changes = (
            cached.kind != "ground"
            and kind != "internal"
            and kind != cached.kind
        )
        details_change = (
            kind_changes
            or (x is not None and x != cached.x)
            or (y is not None and y != cached.y)
            or (layer is not None and layer != cached.layer)
        )
        if ownership_changes or details_change:
            assigned_net = net_id if ownership_changes else old_net
            assigned_confidence = confidence if ownership_changes else old_confidence
            assigned_kind = kind if kind_changes else cached.kind
            updated = _NodeState(
                node_id,
                assigned_net,
                assigned_confidence,
                assigned_kind,
                x if x is not None else cached.x,
                y if y is not None else cached.y,
                layer if layer is not None else cached.layer,
                source_line if source_line is not None else cached.source_line,
            )
            if updated != cached:
                cached = updated
                if node_id in self.pending:
                    self.pending[node_id] = (subcircuit_id, name, cached)
                else:
                    self.dirty[node_id] = cached
        self._remember(key, cached)
        return node_id

    def flush(self) -> None:
        if self.pending:
            self.connection.executemany(
                "INSERT INTO node(id,subcircuit_id,name,net_id,ownership,kind,x,y,layer,source_line) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    (
                        state.node_id, subcircuit_id, name, state.net_id,
                        state.ownership, state.kind, state.x, state.y,
                        state.layer, state.source_line,
                    )
                    for subcircuit_id, name, state in self.pending.values()
                ),
            )
            self.pending.clear()
        if self.dirty:
            self.connection.executemany(
                "UPDATE node SET net_id=?,ownership=?,kind=?,x=?,y=?,layer=?,source_line=? "
                "WHERE id=?",
                (
                    (
                        state.net_id, state.ownership, state.kind, state.x,
                        state.y, state.layer, state.source_line, state.node_id,
                    )
                    for state in self.dirty.values()
                )
            )
            self.dirty.clear()

    def begin_empty_scope(self) -> None:
        """Release identities from a completed subcircuit before a new one."""
        self.flush()
        self.cache.clear()
        self.seen = None
        self.query_before_insert = False

    def _possibly_seen(self, key: tuple[int, str]) -> bool:
        # The Bloom filter is allocated only when a subcircuit exceeds the
        # exact cache. False positives cost a SELECT but cannot merge nodes.
        assert self.seen is not None
        value = hash(key)
        mask = len(self.seen) * 8 - 1
        first = value & mask
        second = ((value >> 32) ^ (value * 0x9E3779B1)) & mask
        first_byte, first_bit = divmod(first, 8)
        second_byte, second_bit = divmod(second, 8)
        first_mask = 1 << first_bit
        second_mask = 1 << second_bit
        result = bool(
            self.seen[first_byte] & first_mask
            and self.seen[second_byte] & second_mask
        )
        self.seen[first_byte] |= first_mask
        self.seen[second_byte] |= second_mask
        return result

    def _remember(
        self, key: tuple[int, str], value: _NodeState,
    ) -> None:
        if self.seen is not None:
            self.cache.pop(key, None)
        self.cache[key] = value
        if len(self.cache) > _CACHE_SIZE:
            if self.seen is None:
                self.seen = bytearray(_SEEN_BYTES)
                for seen_key in self.cache:
                    self._possibly_seen(seen_key)
            oldest = next(iter(self.cache))
            evicted = self.cache.pop(oldest)
            if evicted.node_id in self.pending or evicted.node_id in self.dirty:
                self.flush()
