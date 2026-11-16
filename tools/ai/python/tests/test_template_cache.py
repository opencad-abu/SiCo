"""P4 pure cache contract and dependency-key tests."""

from __future__ import annotations

import pytest

from cadai.template_cache import (
    MAX_ENTRY_BYTES,
    ByteLRUCache,
    TemplateCache,
    cache_enabled_from_environment,
    content_cache_key,
    plan_cache_key,
    search_cache_key,
)
from cadai.template_schema import TemplateError


def test_lru_is_bounded_by_bytes_and_evicts_oldest():
    cache = ByteLRUCache(60, max_entry_bytes=80)
    assert cache.put("a", {"value": "a" * 20}) == "miss"
    assert cache.put("b", {"value": "b" * 20}) == "evicted"
    assert cache.get("a") is None
    assert cache.get("b")["value"] == "b" * 20
    assert cache.bytes_used <= 100
    assert cache.stats["evictions"] >= 1


def test_lru_returns_defensive_copies_and_skips_oversize_values():
    cache = ByteLRUCache(1024, max_entry_bytes=64)
    value = {"nested": ["stable"]}
    assert cache.put("x", value) == "miss"
    value["nested"].append("caller")
    read = cache.get("x")
    read["nested"].append("reader")
    assert cache.get("x") == {"nested": ["stable"]}
    assert cache.put("large", {"value": "x" * MAX_ENTRY_BYTES}) == "fallback"
    assert cache.get("large") is None


def test_disabled_cache_is_semantically_empty():
    cache = TemplateCache(enabled=False)
    assert cache.get("search", "key") is None
    assert cache.put("search", "key", {"status": "matched"}) == "disabled"
    assert cache.event("search") == "disabled"
    assert all(row["entries"] == 0 for row in cache.stats().values())


def test_clear_removes_layer_and_content_aliases():
    cache = TemplateCache(content_bytes=4096, search_bytes=4096, plan_bytes=4096)
    record = {"template_ref": "tpl_" + "a" * 64, "topology": {"devices": []}}
    cache.content_put("snp_a", record["template_ref"], record, "d" * 64, "m" * 64)
    cache.put("search", "s", {"status": "no_match"})
    cache.clear("content")
    assert cache.content_get("snp_a", record["template_ref"], "m" * 64) is None
    assert cache.get("search", "s")["status"] == "no_match"
    cache.clear()
    assert cache.get("search", "s") is None


def test_content_lookup_is_bound_to_snapshot_member_digest_and_reader():
    cache = TemplateCache(content_bytes=4096, search_bytes=4096, plan_bytes=4096)
    record = {"template_ref": "tpl_" + "a" * 64, "topology": {"devices": []}}
    assert cache.content_put("snp_a", record["template_ref"], record, "d" * 64, "m" * 64) == "miss"
    assert cache.content_get("snp_a", record["template_ref"], "m" * 64) == record
    assert cache.content_get("snp_a", record["template_ref"], "other") is None
    assert cache.content_get("snp_b", record["template_ref"], "m" * 64) is None
    assert cache.content_get("snp_a", record["template_ref"], "m" * 64,
                             reader_schema_version="reader.v2") is None
    assert cache.content_put("snp_a", record["template_ref"], record, "d" * 64, "m" * 64,
                             reader_schema_version="reader.v2") == "miss"
    assert cache.content_get("snp_a", record["template_ref"], "m" * 64,
                             reader_schema_version="reader.v2") == record


def test_search_and_plan_keys_include_full_dependencies():
    base = {"snapshot_ref": "snp_a", "target": {"device": "nmos"}, "options": {"m": 1}}
    assert search_cache_key(base) != search_cache_key({**base, "options": {"m": 2}})
    assert search_cache_key(base) != search_cache_key({**base, "origins": ["private"]})
    assert plan_cache_key(base) != plan_cache_key({**base, "snapshot_ref": "snp_b"})
    assert content_cache_key("tpl_" + "a" * 64, "d" * 64, "reader.v1") != content_cache_key(
        "tpl_" + "a" * 64, "e" * 64, "reader.v1"
    )


def test_no_match_has_short_ttl_and_other_search_results_do_not():
    clock = [0.0]
    cache = TemplateCache(search_bytes=4096, clock=lambda: clock[0])
    negative = search_cache_key({"snapshot_ref": "snp", "target": "none"})
    positive = search_cache_key({"snapshot_ref": "snp", "target": "yes"})
    assert cache.put("search", negative, {"status": "no_match"}, negative=True) == "miss"
    assert cache.put("search", positive, {"status": "needs_mapping"}) == "miss"
    clock[0] = 29.0
    assert cache.get("search", negative)["status"] == "no_match"
    clock[0] = 31.0
    assert cache.get("search", negative) is None
    assert cache.get("search", positive)["status"] == "needs_mapping"


@pytest.mark.parametrize("value", ["wat", "maybe"])
def test_cache_environment_is_explicit(value, monkeypatch):
    monkeypatch.setenv("SICO_TEMPLATE_CACHE", value)
    with pytest.raises(TemplateError):
        cache_enabled_from_environment()


def test_cache_rejects_non_json_runtime_handles():
    cache = ByteLRUCache(1024)
    assert cache.put("handle", object()) == "fallback"
    assert cache.get("handle") is None
