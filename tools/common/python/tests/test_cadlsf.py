from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from cadlsf import HostInfo, recommend_hosts


def test_model_is_immutable_and_derives_slot_metrics() -> None:
    host = HostInfo(
        "node01",
        "ok",
        True,
        max_slots=16,
        running_jobs=5,
        suspended_jobs=1,
        reserved_slots=2,
    )

    assert host.used_slots == 8
    assert host.free_slots == 8
    assert host.slot_utilization == 0.5
    with pytest.raises(FrozenInstanceError):
        host.name = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("available", "total", "expected"),
    (
        (51, 100, 0.49),
        (50, 100, 0.5),
        (20, 100, 0.8),
        (19, 100, 0.81),
        (110, 100, 0.0),
        (0, 100, 1.0),
        (50, 0, None),
        (50, None, None),
    ),
)
def test_memory_utilization_uses_host_capacity(
    available: int, total: int | None, expected: float | None
) -> None:
    host = HostInfo(
        "node01",
        "ok",
        True,
        memory_available_bytes=available,
        memory_total_bytes=total,
    )

    assert host.memory_utilization == expected


def test_recommendation_prefers_free_slots_and_explains_result() -> None:
    busy = HostInfo(
        "busy",
        "ok",
        True,
        max_slots=16,
        running_jobs=14,
        load_1m=8.0,
        cpu_utilization=0.8,
        memory_available_bytes=4 * 1024**3,
    )
    idle = HostInfo(
        "idle",
        "ok",
        True,
        max_slots=16,
        running_jobs=2,
        load_1m=2.0,
        cpu_utilization=0.2,
        memory_available_bytes=32 * 1024**3,
    )
    unavailable = HostInfo("closed", "closed", False, max_slots=32)

    recommendations = recommend_hosts((busy, unavailable, idle))

    assert [item.host.name for item in recommendations] == ["idle", "busy"]
    assert "14 free slots" in recommendations[0].reasons
    assert "20% CPU utilization" in recommendations[0].reasons
