"""Virtuoso-session-scoped LSF topology cache."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from threading import Lock, RLock
import time
from typing import Callable, Sequence

from cadgui.transfer import atomic_publish_text
from .cache_types import DEFAULT_MEMBERSHIP_TTL_SECONDS, _CACHE_SCHEMA_VERSION

class SessionTopologyCache:
    def __init__(
        self,
        root: Path,
        *,
        session_pid: int,
        session_start_time: str,
        user: str,
        command_signature: Sequence[str],
        membership_ttl: float = DEFAULT_MEMBERSHIP_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not math.isfinite(membership_ttl) or membership_ttl <= 0:
            raise ValueError("LSF membership cache TTL must be positive")
        self.root = Path(root).expanduser()
        self.session_pid = session_pid
        self.session_start_time = session_start_time
        self.user = user
        self.command_signature = tuple(command_signature)
        self.membership_ttl = float(membership_ttl)
        self._clock = clock
        self._clock_lock = Lock()
        self._memory_lock = RLock()
        self._root_lock = Lock()
        self._root_ready = False
        self._queue_names: tuple[str, ...] | None = None
        self._host_memberships: dict[str, tuple[str, ...]] = {}
        self._host_membership_times: dict[str, float] = {}
        identity = json.dumps(
            self._metadata(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._session_key = hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:16]

    @property
    def queue_path(self) -> Path:
        return self.root / (
            f"topology-{self.session_pid}-{self._session_key}-queues.json"
        )

    def membership_path(self, host: str) -> Path:
        digest = hashlib.sha256(host.encode("utf-8")).hexdigest()[:16]
        return self.root / (
            f"topology-{self.session_pid}-{self._session_key}-"
            f"membership-{digest}.json"
        )

    def _metadata(self) -> dict[str, object]:
        return {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "session_pid": self.session_pid,
            "session_start_time": self.session_start_time,
            "user": self.user,
            "command_signature": list(self.command_signature),
            "membership_ttl": self.membership_ttl,
        }

    def _fresh_membership(
        self, payload: dict[str, object] | None
    ) -> tuple[str, tuple[str, ...], float] | None:
        if payload is None:
            return None
        host = payload.get("host")
        queues = payload.get("queues")
        cached_at = payload.get("cached_at")
        if (
            not isinstance(host, str)
            or not host
            or not isinstance(queues, list)
            or not all(isinstance(queue, str) and queue for queue in queues)
            or not isinstance(cached_at, (int, float))
            or not math.isfinite(cached_at)
        ):
            return None
        age = self._clock() - float(cached_at)
        if age < 0 or age > self.membership_ttl:
            return None
        return host, tuple(dict.fromkeys(queues)), float(cached_at)

    def _read(self, path: Path) -> dict[str, object] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        expected = self._metadata()
        if any(payload.get(name) != value for name, value in expected.items()):
            return None
        return payload

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

    def _publish_once(self, path: Path, payload: dict[str, object]) -> bool:
        if not self._ensure_root():
            return False
        try:
            text = json.dumps(
                {**self._metadata(), **payload},
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            atomic_publish_text(path, text + "\n", refuse_existing=True)
            path.chmod(0o600)
        except FileExistsError:
            # Session topology is immutable: the first valid publisher wins.
            return True
        except OSError:
            return False
        return True

    def load_queue_names(self) -> tuple[str, ...] | None:
        with self._memory_lock:
            if self._queue_names is not None:
                return self._queue_names
        payload = self._read(self.queue_path)
        queues = payload.get("queues") if payload is not None else None
        if not isinstance(queues, list) or not all(
            isinstance(queue, str) and queue for queue in queues
        ):
            return None
        normalized = tuple(queues)
        with self._memory_lock:
            self._queue_names = normalized
        return normalized

    def store_queue_names(self, queues: Sequence[str]) -> bool:
        normalized = tuple(queues)
        if not self._publish_once(
            self.queue_path, {"queues": list(normalized)}
        ):
            return False
        payload = self._read(self.queue_path)
        effective = payload.get("queues") if payload is not None else None
        if not isinstance(effective, list) or not all(
            isinstance(queue, str) and queue for queue in effective
        ):
            return False
        with self._memory_lock:
            self._queue_names = tuple(effective)
        return True

    def load_host_memberships(
        self, hosts: Sequence[str] | None = None
    ) -> dict[str, tuple[str, ...]]:
        now = self._clock()
        with self._memory_lock:
            stale = [
                host
                for host, cached_at in self._host_membership_times.items()
                if now - cached_at < 0
                or now - cached_at > self.membership_ttl
            ]
            for host in stale:
                self._host_memberships.pop(host, None)
                self._host_membership_times.pop(host, None)
        if hosts is None:
            paths = tuple(self.root.glob(
                f"topology-{self.session_pid}-{self._session_key}-"
                "membership-*.json"
            ))
            with self._memory_lock:
                result = dict(self._host_memberships)
        else:
            requested = tuple(dict.fromkeys(hosts))
            with self._memory_lock:
                result = {
                    host: self._host_memberships[host]
                    for host in requested
                    if host in self._host_memberships
                }
            paths = tuple(
                self.membership_path(host)
                for host in requested
                if host not in result
            )
        for path in paths:
            membership = self._fresh_membership(self._read(path))
            if membership is None:
                continue
            host, queues, cached_at = membership
            if path != self.membership_path(host):
                continue
            result[host] = queues
            with self._memory_lock:
                self._host_membership_times[host] = cached_at
        with self._memory_lock:
            self._host_memberships.update(result)
        return result

    def store_host_membership(
        self, host: str, queues: Sequence[str]
    ) -> bool:
        if not isinstance(host, str) or not host:
            return False
        normalized = tuple(dict.fromkeys(queues))
        path = self.membership_path(host)
        existing = self._fresh_membership(self._read(path))
        if existing is not None and existing[0] != host:
            existing = None
        if existing is None and not self._ensure_root():
            return False
        if existing is None:
            if not self._publish_membership(path, host, normalized):
                return False
        effective = self._fresh_membership(self._read(path))
        if effective is None or effective[0] != host:
            return False
        _, effective_queues, effective_cached_at = effective
        with self._memory_lock:
            self._host_memberships[host] = effective_queues
            self._host_membership_times[host] = effective_cached_at
        return True

    def _next_cached_at(self) -> float:
        with self._clock_lock:
            return self._clock()

    def _publish_membership(
        self, path: Path, host: str, queues: tuple[str, ...]
    ) -> bool:
        text = json.dumps(
            {
                **self._metadata(),
                "host": host,
                "queues": list(queues),
                "cached_at": self._next_cached_at(),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            atomic_publish_text(path, text + "\n", refuse_existing=True)
            path.chmod(0o600)
            return True
        except FileExistsError:
            existing = self._fresh_membership(self._read(path))
            if existing is not None and existing[0] == host:
                return True
            try:
                atomic_publish_text(path, text + "\n")
                path.chmod(0o600)
                return True
            except OSError:
                return False
        except OSError:
            return False
