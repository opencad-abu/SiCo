from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from cadlsf.cache import (
    CacheReadResult,
    DEFAULT_MONITOR_CAPACITY_TTL_SECONDS,
    DEFAULT_MONITOR_MEMBERSHIP_TTL_SECONDS,
    DEFAULT_MONITOR_QUEUE_TTL_SECONDS,
    MonitorTopologyCache,
    default_monitor_cache_root,
)
from cadlsf.model import QueueInfo


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _cache(
    root: Path,
    clock: FakeClock,
    **overrides: object,
) -> MonitorTopologyCache:
    values = {
        "user": "demo",
        "command_signature": (
            "/opt/lsf/bin/bqueues",
            "/opt/lsf/bin/bhosts",
            "/opt/lsf/bin/lsload",
            "/opt/lsf/bin/lshosts",
        ),
        "environment_identity": (
            "LSF_ENVDIR=/opt/lsf/conf",
            "LSF_CLUSTER_NAME=production",
        ),
        "clock": clock,
    }
    values.update(overrides)
    return MonitorTopologyCache(root, **values)  # type: ignore[arg-type]


def _queues() -> tuple[QueueInfo, ...]:
    return (
        QueueInfo("normal", "Open:Active", True, 12, 2, 10),
        QueueInfo("overnight", "Open:Active", True, 3, 0, 3),
    )


def test_default_monitor_cache_root_retains_inherited_launch_identity(tmp_path: Path) -> None:
    launch = tmp_path / "launch"
    launch.mkdir()
    assert default_monitor_cache_root(
        {"SICO_TEMP_DIR": str(launch / ".sico")},
        cwd=tmp_path,
        user_id=1001,
    ) == (
        launch / ".sico" / "lsf" / "1001"
    )


def test_monitor_cache_round_trips_sections_and_secures_files(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    root = default_monitor_cache_root({}, cwd=tmp_path, user_id=os.geteuid(), create=True)
    cache = _cache(root, clock)

    assert cache.load_queues() == CacheReadResult(None, None, False)
    assert cache.load_host_memberships() == CacheReadResult(None, None, False)
    assert cache.load_host_capacities() == CacheReadResult(None, None, False)
    assert cache.store_queues(_queues())
    clock.now += 10
    assert cache.store_host_memberships(
        {"node01": ("normal", "overnight"), "node02": ("normal",)}
    )
    clock.now += 10
    assert cache.store_host_capacities(
        {"node01": 64 * 1024**3, "node02": None}
    )

    assert cache.load_queues() == CacheReadResult(_queues(), 1_000_000.0, True)
    assert cache.load_host_memberships() == CacheReadResult(
        {
            "node01": ("normal", "overnight"),
            "node02": ("normal",),
        },
        1_000_010.0,
        True,
    )
    assert cache.load_host_capacities() == CacheReadResult(
        {"node01": 64 * 1024**3, "node02": None},
        1_000_020.0,
        True,
    )
    assert root.stat().st_mode & 0o777 == 0o700
    assert cache.cache_path.stat().st_mode & 0o777 == 0o600
    assert cache.lock_path.stat().st_mode & 0o777 == 0o600


def test_monitor_cache_is_reused_across_virtuoso_processes(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    root = tmp_path / ".cad" / "lsf" / "1001"
    virtuoso_100 = _cache(root, clock)
    assert virtuoso_100.store_queues(_queues())
    assert virtuoso_100.store_host_memberships({"node01": ("normal",)})

    virtuoso_200 = _cache(root, clock)

    assert virtuoso_200.cache_path == virtuoso_100.cache_path
    assert virtuoso_200.load_queues().value == _queues()
    assert virtuoso_200.load_host_memberships().value == {
        "node01": ("normal",)
    }


@pytest.mark.parametrize(
    ("section", "ttl"),
    (
        ("queues", DEFAULT_MONITOR_QUEUE_TTL_SECONDS),
        ("memberships", DEFAULT_MONITOR_MEMBERSHIP_TTL_SECONDS),
        ("capacities", DEFAULT_MONITOR_CAPACITY_TTL_SECONDS),
    ),
)
def test_monitor_cache_ttl_boundary_keeps_stale_value(
    tmp_path: Path, section: str, ttl: float
) -> None:
    clock = FakeClock()
    cache = _cache(tmp_path / "cache", clock)
    if section == "queues":
        assert cache.store_queues(_queues())
        load = cache.load_queues
    elif section == "memberships":
        assert cache.store_host_memberships({"node01": ("normal",)})
        load = cache.load_host_memberships
    else:
        assert cache.store_host_capacities({"node01": 1024})
        load = cache.load_host_capacities

    clock.now += ttl
    assert load().fresh
    clock.now += 0.001
    expired = load()

    assert expired.value is not None
    assert expired.refreshed_at == 1_000_000.0
    assert not expired.fresh


def test_monitor_cache_reads_do_not_extend_timestamps(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = _cache(tmp_path / "cache", clock)
    assert cache.store_queues(_queues())
    before = cache.cache_path.read_bytes()
    before_mtime = cache.cache_path.stat().st_mtime_ns

    clock.now += DEFAULT_MONITOR_QUEUE_TTL_SECONDS - 1
    assert cache.load_queues().refreshed_at == 1_000_000.0
    clock.now += 2
    assert cache.load_queues() == CacheReadResult(
        _queues(), 1_000_000.0, False
    )

    assert cache.cache_path.read_bytes() == before
    assert cache.cache_path.stat().st_mtime_ns == before_mtime


def test_monitor_cache_store_preserves_other_sections(tmp_path: Path) -> None:
    clock = FakeClock()
    root = tmp_path / "cache"
    first = _cache(root, clock)
    second = _cache(root, clock)
    assert first.store_queues(_queues())
    clock.now += 1
    assert second.store_host_capacities({"node01": 4096})
    clock.now += 1
    assert first.store_host_memberships({"node01": ("normal",)})

    fresh = _cache(root, clock)
    assert fresh.load_queues().value == _queues()
    assert fresh.load_host_memberships().value == {"node01": ("normal",)}
    assert fresh.load_host_capacities().value == {"node01": 4096}


def test_monitor_cache_concurrent_section_writes_do_not_lose_data(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    root = tmp_path / "cache"
    queue_cache = _cache(root, clock)
    membership_cache = _cache(root, clock)
    capacity_cache = _cache(root, clock)

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = tuple(
            future.result()
            for future in (
                executor.submit(queue_cache.store_queues, _queues()),
                executor.submit(
                    membership_cache.store_host_memberships,
                    {"node01": ("normal",)},
                ),
                executor.submit(
                    capacity_cache.store_host_capacities,
                    {"node01": 4096},
                ),
            )
        )

    assert results == (True, True, True)
    fresh = _cache(root, clock)
    assert fresh.load_queues().value == _queues()
    assert fresh.load_host_memberships().value == {"node01": ("normal",)}
    assert fresh.load_host_capacities().value == {"node01": 4096}


def test_monitor_cache_concurrent_section_writes_do_not_lose_data(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    root = tmp_path / "cache"
    caches = tuple(_cache(root, clock) for _index in range(3))
    actions = (
        lambda: caches[0].store_queues(_queues()),
        lambda: caches[1].store_host_memberships(
            {"node01": ("normal",)}
        ),
        lambda: caches[2].store_host_capacities({"node01": 4096}),
    )

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = tuple(executor.map(lambda action: action(), actions))

    assert results == (True, True, True)
    fresh = _cache(root, clock)
    assert fresh.load_queues().value == _queues()
    assert fresh.load_host_memberships().value == {"node01": ("normal",)}
    assert fresh.load_host_capacities().value == {"node01": 4096}


@pytest.mark.parametrize(
    "changed",
    (
        {"user": "other"},
        {"command_signature": ("site-bqueues", "bhosts", "lsload")},
        {"environment_identity": ("LSF_ENVDIR=/another/cluster",)},
    ),
)
def test_monitor_cache_identity_isolates_user_commands_and_environment(
    tmp_path: Path, changed: dict[str, object]
) -> None:
    clock = FakeClock()
    original = _cache(tmp_path / "cache", clock)
    assert original.store_queues(_queues())

    candidate = _cache(tmp_path / "cache", clock, **changed)

    assert candidate.cache_path != original.cache_path
    assert candidate.load_queues() == CacheReadResult(None, None, False)


def test_monitor_cache_ignores_old_session_files(tmp_path: Path) -> None:
    clock = FakeClock()
    root = tmp_path / "cache"
    root.mkdir(parents=True)
    (root / "topology-1234-deadbeef-queues.json").write_text(
        '{"queues":["normal"]}\n', encoding="utf-8"
    )

    cache = _cache(root, clock)

    assert cache.load_queues() == CacheReadResult(None, None, False)


def test_monitor_cache_corruption_degrades_and_next_store_recovers(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    cache = _cache(tmp_path / "cache", clock)
    assert cache.store_queues(_queues())
    cache.cache_path.write_text("{not json\n", encoding="utf-8")

    assert cache.load_queues() == CacheReadResult(None, None, False)
    assert cache.load_host_memberships() == CacheReadResult(None, None, False)
    clock.now += 1
    assert cache.store_host_memberships({"node01": ("normal",)})

    assert cache.load_queues() == CacheReadResult(None, None, False)
    assert cache.load_host_memberships() == CacheReadResult(
        {"node01": ("normal",)}, 1_000_001.0, True
    )


def test_monitor_cache_rejects_overly_permissive_file(tmp_path: Path) -> None:
    cache = _cache(tmp_path / "cache", FakeClock())
    assert cache.store_queues(_queues())
    cache.cache_path.chmod(0o644)

    assert cache.load_queues() == CacheReadResult(None, None, False)


def test_monitor_cache_rejects_malformed_section_without_hiding_valid_one(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    cache = _cache(tmp_path / "cache", clock)
    assert cache.store_queues(_queues())
    payload = json.loads(cache.cache_path.read_text(encoding="utf-8"))
    payload["host_capacities"] = {"node01": -1}
    payload["capacity_refreshed_at"] = clock.now
    cache.cache_path.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.load_queues().value == _queues()
    assert cache.load_host_capacities() == CacheReadResult(None, None, False)


def test_monitor_cache_write_failure_is_nonfatal(tmp_path: Path) -> None:
    clock = FakeClock()
    blocked_root = tmp_path / "not-a-directory"
    blocked_root.write_text("blocked", encoding="utf-8")
    cache = _cache(blocked_root, clock)

    assert not cache.store_queues(_queues())
    assert cache.load_queues() == CacheReadResult(None, None, False)


@pytest.mark.parametrize("ttl", (0.0, -1.0, float("inf"), float("nan")))
def test_monitor_cache_rejects_invalid_ttls(tmp_path: Path, ttl: float) -> None:
    with pytest.raises(ValueError, match="TTL must be positive"):
        _cache(tmp_path / "cache", FakeClock(), queue_ttl=ttl)
