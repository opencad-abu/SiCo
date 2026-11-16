"""End-to-end checks for the formal offline MCP catalog report."""

from mcp_catalog_report import build, diff


def test_report_layers_are_consistent_and_groups_resolve():
    built = build()
    layers = built["layers"]
    canonical = layers["canonical"]["measurement"]
    wire = layers["canonical_wire"]["measurement"]
    host = layers["host"]["measurement"]
    assert canonical["count"] == wire["count"] > 0
    assert host["count"] > canonical["count"]
    # The wire projection never grows; large circuit schemas are summarized
    # while their full contracts remain available through mcp_help.
    assert wire["array_compact_bytes"] < canonical["array_compact_bytes"]
    assert wire["field_bytes"]["inputSchema"] < canonical["field_bytes"]["inputSchema"]
    # The SiCo host carries every canonical tool; the automatic product surface
    # is the complete host-supported catalog.
    assert built["exposure"]["canonical_missing_in_host"] == []
    desktop = built["exposure"]["sico_desktop"]
    assert desktop["profile"] == "desktop_compat"
    unsupported = set(desktop["host_unsupported"])
    host_names = {row["name"] for row in host["tools"]}
    desktop_names = {row["name"] for row in desktop["measurement"]["tools"]}
    supported_names = host_names - unsupported
    outside_default = set(desktop["outside_default"])
    assert desktop_names == supported_names
    assert outside_default == set()
    # The runtime declarations resolve against the host catalog without blanks.
    task_groups = built["task_groups"]
    assert task_groups["contract"] == "sico.mcp.task_groups.v1"
    assert "runtime declarations" in task_groups["source"]
    assert {entry["id"] for entry in task_groups["groups"]} >= {
        "virtuoso_ui", "cds_reference", "schematic_read", "testbench_create"}
    for entry in task_groups["groups"]:
        assert entry["missing"] == []
        assert entry["conditional_missing"] == []
        assert entry["tool_count"] > 0
        assert entry["status"] in {"draft", "reviewed"}
        assert entry["digest"].startswith("sha256:")
    assert task_groups["union"]["count"] > 0
    # The runtime profiles resolve against the same host catalog.
    profiles = built["tool_profiles"]
    assert profiles["contract"] == "sico.mcp.tool_profiles.v1"
    by_id = {entry["id"]: entry for entry in profiles["profiles"]}
    assert {"desktop_compat", "desktop_core", "circuit", "simulation",
            "pdk_maintenance", "physical_flow", "verification",
            "adaptive"} <= set(by_id)
    assert by_id["desktop_compat"]["tool_count"] == len(supported_names)
    assert by_id["desktop_compat"]["tool_count"] == len(desktop_names)
    assert by_id["desktop_core"]["tool_count"] < len(desktop_names)
    assert by_id["desktop_core"]["missing"] == []
    assert by_id["verification"]["unavailable"] == [
        "live_model_events", "live_model_status"]


def test_report_is_deterministic_for_one_source_tree():
    first, second = build(), build()
    assert first["layers"]["host"]["measurement"] == second["layers"]["host"]["measurement"]
    assert first["task_groups"]["union"] == second["task_groups"]["union"]


def test_diff_reports_layer_deltas():
    def report(count, name, array_bytes):
        return {
            "source": {"commit": "old" if count == 1 else "new"},
            "layers": {"host": {"measurement": {
                "count": count,
                "array_compact_bytes": array_bytes,
                "tools": [{"name": name, "sha256": name, "content_sha256": name}],
            }}},
        }

    result = diff(report(1, "old_tool", 100), report(2, "old_tool", 250))
    assert result["layers"]["host"]["added"] == []
    assert result["layers"]["host"]["count"] == [1, 2]
    assert result["layers"]["host"]["array_compact_bytes"] == [100, 250]


def test_diff_marks_layers_missing_on_either_side():
    result = diff({"source": {}, "layers": {"host": {"measurement": {
        "count": 1, "array_compact_bytes": 1, "tools": []}}}},
        {"source": {}, "layers": {"canonical": {"measurement": {
            "count": 1, "array_compact_bytes": 1, "tools": []}}}})
    assert result["layers"]["host"]["present"] == [True, False]
    assert result["layers"]["canonical"]["present"] == [False, True]
