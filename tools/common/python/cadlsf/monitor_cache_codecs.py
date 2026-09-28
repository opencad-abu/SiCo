"""Pure decoders for persisted Monitor topology sections."""

from __future__ import annotations

from .model import QueueInfo

def decode_queues(value: object) -> tuple[QueueInfo, ...] | None:
    if not isinstance(value, list):
        return None
    queues: list[QueueInfo] = []
    fields = {
        "name",
        "status",
        "is_open",
        "total_jobs",
        "pending_jobs",
        "running_jobs",
    }
    for item in value:
        if not isinstance(item, dict) or set(item) != fields:
            return None
        if (
            not isinstance(item["name"], str)
            or not item["name"]
            or not isinstance(item["status"], str)
            or not isinstance(item["is_open"], bool)
        ):
            return None
        for name in ("total_jobs", "pending_jobs", "running_jobs"):
            count = item[name]
            if count is not None and (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
            ):
                return None
        queues.append(QueueInfo(**item))
    return tuple(queues)

def decode_host_memberships(
    value: object,
) -> dict[str, tuple[str, ...]] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, tuple[str, ...]] = {}
    for host, queues in value.items():
        if (
            not isinstance(host, str)
            or not host
            or not isinstance(queues, list)
            or not all(isinstance(queue, str) and queue for queue in queues)
        ):
            return None
        result[host] = tuple(dict.fromkeys(queues))
    return result

def decode_host_capacities(
    value: object,
) -> dict[str, int | None] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, int | None] = {}
    for host, capacity in value.items():
        if not isinstance(host, str) or not host:
            return None
        if capacity is not None and (
            not isinstance(capacity, int)
            or isinstance(capacity, bool)
            or capacity < 0
        ):
            return None
        result[host] = capacity
    return result
