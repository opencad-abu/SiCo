"""Resource baseline helpers stay deterministic and source-local."""

from utility import resource_baseline
from sico.codex.rpc_queue import BoundedMessageQueue


def test_percentiles_are_ordered_and_empty_safe():
    assert resource_baseline._percentiles([]) == {
        "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}
    values = resource_baseline._percentiles([4.0, 1.0, 3.0, 2.0, 5.0])
    assert values["p50_ms"] == 3.0
    assert values["p95_ms"] >= values["p50_ms"]
    assert values["p99_ms"] >= values["p95_ms"]
    assert values["max_ms"] == 5.0


def test_budgets_and_source_paths_are_declared():
    assert all(value > 0 for value in resource_baseline.BUDGETS.values())
    assert resource_baseline._paths()
    assert all(path.is_dir() for path in resource_baseline._paths())


def test_queue_probe_reports_a_real_byte_admission_limit():
    report = resource_baseline._queue_probe(
        BoundedMessageQueue, 64 * 1024 * 1024, 64 * 1024 * 1024)
    assert report == {
        "probe_max_items": 8, "probe_max_bytes": 4096,
        "rpc_max_items": 4096, "rpc_max_bytes": 64 * 1024 * 1024,
        "rpc_pending_bytes": 64 * 1024 * 1024,
        "blocked_at_limit": True, "queued_bytes_after_get": 0,
    }
