"""Process-independent LSF monitor topology cache."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import fcntl
from sicolock import lock as state_lock
import hashlib
import json
import math
import os
from pathlib import Path
from threading import Lock, RLock
import time
from typing import Callable, Iterator, Mapping, Sequence

from cadgui.transfer import atomic_publish_text
from .cache_types import (
    CacheReadResult, _T, DEFAULT_MONITOR_CAPACITY_TTL_SECONDS,
    DEFAULT_MONITOR_MEMBERSHIP_TTL_SECONDS, DEFAULT_MONITOR_QUEUE_TTL_SECONDS,
    _MONITOR_CACHE_SCHEMA_VERSION,
)
from .model import QueueInfo
from .monitor_cache_codecs import (
    decode_queues, decode_host_memberships, decode_host_capacities,
)

class MonitorTopologyCache:
    """Long-lived monitor cache, shared across Virtuoso processes."""

    def __init__(
        self,
        root: Path,
        *,
        user: str,
        command_signature: Sequence[str],
        environment_identity: Sequence[str] = (),
        queue_ttl: float = DEFAULT_MONITOR_QUEUE_TTL_SECONDS,
        membership_ttl: float = DEFAULT_MONITOR_MEMBERSHIP_TTL_SECONDS,
        capacity_ttl: float = DEFAULT_MONITOR_CAPACITY_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        for label, value in (
            ("queue", queue_ttl),
            ("membership", membership_ttl),
            ("capacity", capacity_ttl),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"LSF monitor {label} cache TTL must be positive")
        if not isinstance(user, str) or not user:
            raise ValueError("LSF monitor cache user must be non-empty")
        if not all(isinstance(item, str) for item in command_signature):
            raise ValueError("LSF monitor command signature must contain strings")
        if not all(isinstance(item, str) for item in environment_identity):
            raise ValueError("LSF monitor environment identity must contain strings")
        self.root = Path(root).expanduser()
        self.user = user
        self.command_signature = tuple(command_signature)
        self.environment_identity = tuple(environment_identity)
        self.queue_ttl = float(queue_ttl)
        self.membership_ttl = float(membership_ttl)
        self.capacity_ttl = float(capacity_ttl)
        self._clock = clock
        self._write_lock = RLock()
        self._root_lock = Lock()
        self._root_ready = False
        identity = json.dumps(
            self._metadata(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._cache_key = hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:16]

    @property
    def cache_path(self) -> Path:
        return self.root / f"monitor-{self._cache_key}.json"

    @property
    def lock_path(self) -> Path:
        return self.root / f"monitor-{self._cache_key}.lock"

    def _metadata(self) -> dict[str, object]:
        return {
            "schema_version": _MONITOR_CACHE_SCHEMA_VERSION,
            "user": self.user,
            "command_signature": list(self.command_signature),
            "environment_identity": list(self.environment_identity),
        }

    def _ensure_root(self) -> bool:
        if self._root_ready:
            return True
        with self._root_lock:
            if self._root_ready:
                return True
            try:
                self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.root.chmod(0o700)
            except OSError:
                return False
            self._root_ready = True
        return True

    def _read(self) -> dict[str, object] | None:
        try:
            if self.cache_path.stat().st_mode & 0o077:
                return None
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        expected = self._metadata()
        if any(payload.get(name) != value for name, value in expected.items()):
            return None
        return payload

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            state_lock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                state_lock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _store(
        self,
        section: str,
        timestamp_field: str,
        value: object,
    ) -> bool:
        if not self._ensure_root():
            return False
        refreshed_at = self._clock()
        if (
            not isinstance(refreshed_at, (int, float))
            or isinstance(refreshed_at, bool)
            or not math.isfinite(refreshed_at)
        ):
            return False
        try:
            with self._write_lock, self._exclusive_lock():
                payload = self._read()
                if payload is None:
                    payload = self._metadata()
                payload[section] = value
                payload[timestamp_field] = float(refreshed_at)
                text = json.dumps(
                    payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                atomic_publish_text(self.cache_path, text + "\n")
                self.cache_path.chmod(0o600)
        except OSError:
            return False
        return True

    def _result(
        self,
        *,
        section: str,
        timestamp_field: str,
        ttl: float,
        decoder: Callable[[object], _T | None],
    ) -> CacheReadResult[_T]:
        payload = self._read()
        if payload is None:
            return CacheReadResult(None, None, False)
        value = decoder(payload.get(section))
        if value is None:
            return CacheReadResult(None, None, False)
        raw_timestamp = payload.get(timestamp_field)
        refreshed_at = (
            float(raw_timestamp)
            if isinstance(raw_timestamp, (int, float))
            and not isinstance(raw_timestamp, bool)
            and math.isfinite(raw_timestamp)
            else None
        )
        if refreshed_at is None:
            return CacheReadResult(value, None, False)
        age = self._clock() - refreshed_at
        fresh = math.isfinite(age) and 0 <= age <= ttl
        return CacheReadResult(value, refreshed_at, fresh)

    def load_queues(self) -> CacheReadResult[tuple[QueueInfo, ...]]:
        return self._result(
            section="queues",
            timestamp_field="queue_refreshed_at",
            ttl=self.queue_ttl,
            decoder=decode_queues,
        )

    def store_queues(self, queues: Sequence[QueueInfo]) -> bool:
        try:
            normalized = tuple(queues)
            serialized = [asdict(queue) for queue in normalized]
        except (TypeError, ValueError):
            return False
        if decode_queues(serialized) != normalized:
            return False
        return self._store("queues", "queue_refreshed_at", serialized)

    def load_host_memberships(
        self,
    ) -> CacheReadResult[dict[str, tuple[str, ...]]]:
        return self._result(
            section="host_memberships",
            timestamp_field="topology_refreshed_at",
            ttl=self.membership_ttl,
            decoder=decode_host_memberships,
        )

    def store_host_memberships(
        self, memberships: Mapping[str, Sequence[str]]
    ) -> bool:
        serialized: dict[str, list[str]] = {}
        try:
            for host, queues in memberships.items():
                serialized[host] = list(dict.fromkeys(queues))
        except (AttributeError, TypeError):
            return False
        if decode_host_memberships(serialized) is None:
            return False
        return self._store(
            "host_memberships", "topology_refreshed_at", serialized
        )

    def load_host_capacities(
        self,
    ) -> CacheReadResult[dict[str, int | None]]:
        return self._result(
            section="host_capacities",
            timestamp_field="capacity_refreshed_at",
            ttl=self.capacity_ttl,
            decoder=decode_host_capacities,
        )

    def store_host_capacities(
        self, capacities: Mapping[str, int | None]
    ) -> bool:
        try:
            serialized = dict(capacities)
        except (TypeError, ValueError):
            return False
        if decode_host_capacities(serialized) is None:
            return False
        return self._store(
            "host_capacities", "capacity_refreshed_at", serialized
        )
