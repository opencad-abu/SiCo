"""Licensed acceptance worker launched BY Virtuoso through production AI_ipc.il."""
import json
import os
import sys
import threading
from pathlib import Path

from cadai.controller import RequestDispatcher
from cadai.mcp import McpServer, SocketClient
from cadai.runtime import RuntimePaths
from cadai.socket_server import UnixRequestServer
from cadai.transport import VirtuosoTransport


def main():
    root = Path(sys.argv[1])
    runtime = RuntimePaths.create(runtime_base=root)
    transport = VirtuosoTransport()
    transport.start()
    dispatcher = RequestDispatcher(transport, runtime, root, timeout=25)
    token = "isolated-pdk-acceptance-" + os.urandom(16).hex()
    socket = UnixRequestServer(runtime.socket, token, dispatcher)
    thread = threading.Thread(target=socket.serve_forever, daemon=True)
    thread.start()
    client = SocketClient(runtime.socket, token, 25, runtime)
    server = McpServer(client, workspace=root)
    trace = []

    def call(name, args):
        response = server.handle({"jsonrpc": "2.0", "id": len(trace) + 1, "method": "tools/call",
                                  "params": {"name": name, "arguments": args}})
        if "error" in response:
            raise AssertionError(response)
        data = json.loads(response["result"]["content"][0]["text"])
        trace.append({"name": name, "arguments": args, "result": data})
        assert not response["result"]["isError"], data
        return data

    try:
        transport.send({"event": "session.ready", "control_protocol": "control.v2"})
        search_args = {"library": os.environ.get("CAD_AI_PDK_TEST_LIBRARY", "analogLib"),
                       "cell": os.environ.get("CAD_AI_PDK_TEST_CELL", "res"), "page_size": 1}
        found = call("search_pdk_devices", search_args)
        assert found["items"], found
        ref = found["context"]["snapshot_ref"]
        dev = found["items"][0]["device_ref"]
        args = {"snapshot_ref": ref, "device_ref": dev, "page_size": 20}
        pages = [call("get_pdk_device", args)]
        while pages[-1]["page"]["next_cursor"]:
            pages.append(call("get_pdk_device", {**args, "cursor": pages[-1]["page"]["next_cursor"]}))
        assert all(p["device"]["revision"] == pages[0]["device"]["revision"] for p in pages)
        checked = call("revalidate_pdk_bindings", {"snapshot_ref": ref, "device_refs": [dev]})
        assert checked["status"] == "valid", checked
        directory = call("search_pdk_devices", {"library": search_args["library"], "page_size": 2})
        assert directory["page"]["next_cursor"]
        page2 = call("search_pdk_devices", {"library": search_args["library"], "page_size": 2,
            "snapshot_ref": directory["context"]["snapshot_ref"], "cursor": directory["page"]["next_cursor"]})
        assert not {x["device_ref"] for x in directory["items"]} & {x["device_ref"] for x in page2["items"]}
        # Real SKILL byte-string transport check, without touching any database.
        call("eval_skill", {"code": 'aiPdkTransfers["unicode-test"]="中文字符"'})
        encoded = call("eval_skill", {"code": 'aiPdkRead("unicode-test" 0)'})
        assert bytes.fromhex(json.loads(encoded["value"])["hex"]).decode() == "中文字符"
        call("eval_skill", {"code": 'aiPdkTransfers["unicode-test"]=nil'})
        # In-memory discovery/IPC metadata only, no OA or CDF writes.
        call("eval_skill", {"code": "aiIpcState['generation]=aiIpcState['generation]+1"})
        changed = call("revalidate_pdk_bindings", {"snapshot_ref": ref, "device_refs": [dev]})
        assert changed["status"] == "changed" and "session_generation" in changed["items"][0]["changes"]
        status = call("eval_skill", {"code": "aiPdkJson(aiPdkObj(list(list(\"ok\" t) "
                      "list(\"open_views\" length(dbGetOpenCellViews())) "
                      "list(\"transfers\" aiPdkTransferCount) list(\"pid\" ipcGetPid()))))"})
        state = json.loads(status["value"])
        assert state["open_views"] == 0 and state["transfers"] == 0
        assert state["pid"] == found["context"]["process_id"]
        (root / "success.json").write_text(json.dumps({"pid": state["pid"], "trace": trace}, indent=2))
    finally:
        (root / "trace.json").write_text(json.dumps(trace, indent=2))
        client.close()
        socket.shutdown()
        socket.server_close()
        transport.close()
        runtime.cleanup()
        (root / "finished").touch()


if __name__ == "__main__":
    main()
