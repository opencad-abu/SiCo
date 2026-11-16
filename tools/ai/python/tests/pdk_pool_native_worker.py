"""Private acceptance: real live IPC baseline versus compiled dbAccess pools."""
import json
import os
from pathlib import Path
import sys
import threading
import time

from cadai.controller import RequestDispatcher
from cadai.mcp import SocketClient
from cadai.pdk_bridge import PdkBridge
from cadai.pdk_preparation import PdkPreparation
from cadai.process_monitor import process_scope, snapshot
from cadai.runtime import RuntimePaths
from cadai.socket_server import UnixRequestServer
from cadai.transport import VirtuosoTransport


def main():
    root = Path(sys.argv[1])
    runtime = RuntimePaths.create(runtime_base=root)
    transport = VirtuosoTransport()
    transport.start()
    dispatcher = RequestDispatcher(transport, runtime, root, timeout=60)
    token = "pdk-pool-native-" + os.urandom(16).hex()
    server = UnixRequestServer(runtime.socket, token, dispatcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = SocketClient(runtime.socket, token, 60, runtime)
    report = {"runs": []}
    try:
        transport.send({"event": "session.ready", "control_protocol": "control.v2"})
        bridge = PdkBridge(client)
        baseline = None
        for size in (0, 1, 4, 8, 16, 32):
            directory = root / ("live" if size == 0 else str(size))
            environment = dict(os.environ, SICO_PDK_WORKERS=str(size or 1))
            preparation = PdkPreparation(bridge, workspace=directory, environment=environment)
            if size == 0:
                preparation.pool_factory = None
            started = time.monotonic()
            if size == 0:
                captures = root / "live-captures"
                captures.mkdir(mode=0o700)
                # Ensure the exporter records the live bridge's established session identity.
                bridge.capture("libraries", {})
                bridge.call("pdkPoolNativeBaseline(" + json.dumps(str(captures)) + " " +
                            json.dumps(os.environ["CAD_AI_PDK_TEST_LIBRARY"]) + ")")
                class LiveFiles:
                    def capture(self, operation, target):
                        if operation == "device":
                            return json.loads((captures / (target["cell"] + ".json")).read_text())
                        return bridge.capture(operation, target)
                preparation.bridge = LiveFiles()
            peak = {"workers": 0, "rss_bytes": 0}
            stop = threading.Event()

            def monitor():
                while not stop.wait(0.05):
                    running = [r for r in snapshot(str(directory)) if r["state"] == "running"]
                    peak["workers"] = max(peak["workers"], sum(not r["parent_id"] for r in running))
                    peak["rss_bytes"] = max(peak["rss_bytes"], sum(r["rss"] or 0 for r in running))

            thread = threading.Thread(target=monitor, daemon=True)
            thread.start()
            try:
                with process_scope(str(directory)):
                    job = preparation.prepare({"library": os.environ["CAD_AI_PDK_TEST_LIBRARY"],
                                               "choice_confirmed": True})
                    batches = 0
                    while job["status"] == "collecting":
                        job = preparation.advance({k: job[k] for k in ("collection_ref", "next_batch")})
                        batches += 1
                        assert batches < 1000, job
                assert job["status"] == "ready", job
                payload = json.loads(Path(job["path"]).read_text())["payload"]
                devices = {k: v["digest"] for k, v in payload["devices"].items()}
                if baseline is None:
                    baseline = devices
                mismatches = sorted(k for k in set(devices) | set(baseline)
                                    if devices.get(k) != baseline.get(k))
                value = {"workers_requested": size, "seconds": round(time.monotonic() - started, 3),
                         "mode": "live_file_export" if size == 0 else "dbAccess_pool",
                         "peak": peak, "batches": batches, "devices": len(devices),
                         "excluded": payload["excluded"], "mismatches": mismatches,
                         "path": job["path"], "coverage": payload["coverage"]}
                report["runs"].append(value)
                (root / "report.json").write_text(json.dumps(report, indent=2))
                print("PDK_BENCH " + json.dumps({k: value[k] for k in (
                    "workers_requested", "seconds", "devices", "mismatches", "peak")}),
                      file=sys.stderr, flush=True)
                assert not mismatches, value
                if size:
                    sources = {v["source"] for v in payload["collection"]["devices"].values()}
                    assert sources == {"background_dbAccess_worker"}, sources
                    assert peak["workers"] <= size
            finally:
                preparation.close()
                stop.set()
                thread.join(timeout=2)
            assert not [r for r in snapshot(str(directory)) if r["state"] == "running"]
        report["passed"] = True
        (root / "report.json").write_text(json.dumps(report, indent=2))
        (root / "success").touch()
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        transport.close()
        runtime.cleanup()
        (root / "finished").touch()


if __name__ == "__main__":
    main()
