import json

from test_layout_backend_host import manifest, request

from cadai.layout.mcp_server import _TOOLS, LayoutMcpSession


def workspace(tmp_path):
    root = tmp_path / "project"
    directory = root / ".sico/layout/backends/fake-layout"
    directory.mkdir(parents=True)
    (directory / "backend.json").write_text(
        json.dumps(manifest(tmp_path), indent=2) + "\n", encoding="utf-8"
    )
    return root


def call(session, name, arguments=None):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    response = session.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params,
    })
    return response, json.loads(response["result"]["content"][0]["text"])


def core_request(tmp_path):
    value = request(tmp_path)
    return {
        key: value[key]
        for key in ("action", "source", "process_view_ref", "inputs", "constraints",
                    "outputs", "options", "resources", "timeout_sec")
    }


def test_standalone_layout_mcp_lists_and_runs_job(tmp_path):
    session = LayoutMcpSession(workspace(tmp_path))
    try:
        init = session.handle({
            "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {},
        })
        assert init["result"]["serverInfo"]["name"]
        assert "list_layout_backends" in {row["name"] for row in
                                          session.handle({
                                              "jsonrpc": "2.0", "id": 1,
                                              "method": "tools/list",
                                          })["result"]["tools"]}
        _, listed = call(session, "list_layout_backends")
        assert listed["backends"][0]["id"] == "fake-layout"
        _, started = call(session, "start_layout_job", {
            "backend_id": "fake-layout", "request": core_request(tmp_path),
        })
        _, status = call(session, "get_layout_job", {"job_id": started["job_id"]})
        assert status["state"] == "succeeded"
        assert status["result"]["artifacts"][0]["role"] == "layout"
        layer_map = tmp_path / "layer.map"
        layer_map.write_text("M1 61 0\n", encoding="utf-8")
        _, plan = call(session, "plan_layout_import", {
            "job_id": started["job_id"],
            "target_library": "WORK",
            "target_cell": "FAKE",
            "target_view": "layout_pnr",
            "layer_map": str(layer_map),
        })
        assert plan["plan"]["target"]["cell"] == "FAKE"
        _, status = call(session, "get_layout_import_status", {
            "plan_file": plan["plan"]["plan_file"],
        })
        assert status["status"]["state"] == "planned"
    finally:
        session.close()


def test_standalone_layout_mcp_rejects_unknown_tool(tmp_path):
    session = LayoutMcpSession(workspace(tmp_path))
    try:
        response, detail = call(session, "not_a_layout_tool")
        assert response["result"]["isError"] is True
        assert detail["code"] == "unknown_layout_tool"
        assert "not_a_layout_tool" not in _TOOLS
    finally:
        session.close()
