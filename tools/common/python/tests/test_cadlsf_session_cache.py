from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
import pytest
from cadlsf.cache import SessionTopologyCache, default_cache_root


def test_session_cache_rejects_other_session_user_and_command(tmp_path: Path) -> None:
    root = tmp_path / ".cad" / "lsf"
    original = SessionTopologyCache(
        root,
        session_pid=1234,
        session_start_time="5678",
        user="demo",
        command_signature=("bqueues", "bhosts", "lsload"),
    )
    original.store_queue_names(("normal",))
    original.store_host_membership("node01", ("normal",))

    for changed in (
        {"session_start_time": "9999"},
        {"user": "other"},
        {"command_signature": ("site-bqueues", "bhosts", "lsload")},
    ):
        values = {
            "session_pid": 1234,
            "session_start_time": "5678",
            "user": "demo",
            "command_signature": ("bqueues", "bhosts", "lsload"),
            **changed,
        }
        candidate = SessionTopologyCache(root, **values)
        assert candidate.load_queue_names() is None
        assert candidate.load_host_memberships() == {}


def test_session_cache_host_shards_merge_across_instances(tmp_path: Path) -> None:
    root = tmp_path / ".cad" / "lsf"
    values = {
        "session_pid": 1234,
        "session_start_time": "5678",
        "user": "demo",
        "command_signature": ("bqueues", "bhosts", "lsload"),
    }
    first = SessionTopologyCache(root, **values)
    second = SessionTopologyCache(root, **values)

    assert first.store_host_membership("node01", ("normal", "batch"))
    assert second.store_host_membership("node02", ("batch",))

    assert first.load_host_memberships() == {
        "node01": ("normal", "batch"),
        "node02": ("batch",),
    }


def test_session_cache_reuses_memberships_from_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = SessionTopologyCache(
        tmp_path / ".cad" / "lsf",
        session_pid=1234,
        session_start_time="5678",
        user="demo",
        command_signature=("bqueues", "bhosts", "lsload"),
    )
    assert cache.store_host_membership("node01", ("normal", "batch"))
    monkeypatch.setattr(
        cache,
        "_read",
        lambda _path: pytest.fail("memory-cached membership read from disk"),
    )

    assert cache.load_host_memberships(("node01",)) == {
        "node01": ("normal", "batch")
    }


def test_session_cache_host_shards_publish_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".cad" / "lsf"
    cache = SessionTopologyCache(
        root,
        session_pid=1234,
        session_start_time="5678",
        user="demo",
        command_signature=("bqueues", "bhosts", "lsload"),
    )
    active = 0
    peak = 0
    lock = threading.Lock()
    import cadlsf.session_cache as cache_module

    original = cache_module.atomic_publish_text

    def delayed_publish(
        path: Path,
        text: str,
        *,
        refuse_existing: bool = False,
    ) -> Path:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)
            return original(
                path, text, refuse_existing=refuse_existing
            )
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(cache_module, "atomic_publish_text", delayed_publish)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda index: cache.store_host_membership(
                    f"node{index:02d}", ("normal",)
                ),
                range(8),
            )
        )

    assert all(results)
    assert peak == 4
    assert len(cache.load_host_memberships()) == 8


def test_session_cache_same_host_first_publisher_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = {
        "root": tmp_path / ".cad" / "lsf",
        "session_pid": 1234,
        "session_start_time": "5678",
        "user": "demo",
        "command_signature": ("bqueues", "bhosts", "lsload"),
    }
    first = SessionTopologyCache(**values)
    second = SessionTopologyCache(**values)
    import cadlsf.session_cache as cache_module

    original = cache_module.atomic_publish_text
    barrier = threading.Barrier(2)

    def overlapping_publish(
        path: Path,
        text: str,
        *,
        refuse_existing: bool = False,
    ) -> Path:
        barrier.wait(timeout=2)
        return original(path, text, refuse_existing=refuse_existing)

    monkeypatch.setattr(
        cache_module, "atomic_publish_text", overlapping_publish
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda item: item[0].store_host_membership(
                    "node01", (item[1],)
                ),
                ((first, "normal"), (second, "batch")),
            )
        )

    assert results == (True, True)
    fresh = SessionTopologyCache(**values)
    memberships = [
        cache.load_host_memberships(("node01",))["node01"]
        for cache in (first, second, fresh)
    ]
    assert memberships[0] == memberships[1] == memberships[2]
    assert memberships[0] in {("normal",), ("batch",)}


def test_default_cache_root_uses_session_temp_directory(tmp_path: Path) -> None:
    (tmp_path / ".sico").mkdir(mode=0o700)
    assert default_cache_root(
        {"SICO_TEMP_DIR": str(tmp_path / ".sico")}, user_id=1001
    ) == (
        tmp_path / ".sico" / "lsf" / "1001"
    )
    assert default_cache_root({}, cwd=tmp_path, user_id=1001) == (
        tmp_path / ".sico" / "lsf" / "1001"
    )
    (tmp_path / ".sico").rmdir()
    assert default_cache_root({}, cwd=tmp_path, user_id=1001) == (
        tmp_path / ".sico" / "lsf" / "1001"
    )
