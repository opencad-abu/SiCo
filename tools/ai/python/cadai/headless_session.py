"""Development-only Xvfb/Virtuoso session harness for private capture and tests.

Calls the production MCP tool inside a private process with read-only source
views and no editor window. It loads source SKILL and depends on a development
checkout; it is not a runtime session service or native distribution input.
Production modules must not import this harness. Scope is declared in
``deploy/python_native_build.json``.
"""

import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .controller import RequestDispatcher
from .mcp import McpServer
from .runtime import RuntimePaths

# Source-tree layout: tools/ai/python/cadai/headless_session.py -> tools/ai.
# A compiled acceptance root overrides the skill/fixture location.
AI_ROOT = (
    Path(os.environ["CAD_AI_COMPILED_ROOT"])
    if os.environ.get("CAD_AI_COMPILED_ROOT")
    else Path(__file__).resolve().parents[2]
)
IC_LIBS = Path(
    os.environ.get(
        "SICO_AI_IC_LIBS",
        "/software/eda/cadence/installs/IC23.10.130/tools/dfII/etc/cdslib",
    )
)

# Modules a generic owned session loads; capture-specific modules are loaded
# later by the collector's ``attach``.
MODULES = [
    "AI_protocol.il",
    "AI_eval.il",
    "AI_circuit.il",
    "AI_circuit_context.il",
    "AI_circuit_task.il",
    "AI_circuit_extensions.il",
    "AI_circuit_create_check.il",
    "AI_circuit_text.il", "AI_circuit_preview.il", "AI_circuit_create_build.il",
    "AI_circuit_create.il",
]


class HeadlessUnavailable(RuntimeError):
    """Virtuoso or Xvfb is not available for a headless session."""


class HeadlessError(RuntimeError):
    """The headless session died or answered with a SKILL failure."""


class NativeBridge:
    """Replay-file bridge to one owned Virtuoso process (no window)."""

    def __init__(self, directory, process):
        self.directory, self.process = directory, process

    def wait_file(self, path, timeout=50):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise HeadlessError("Virtuoso exited; inspect CDS.log")
            if path.exists():
                return
            time.sleep(0.1)
        raise TimeoutError("Virtuoso response timeout: " + str(path))

    def request(self, payload, timeout):
        if payload["method"] not in {"eval_skill_native", "eval_skill"}:
            raise HeadlessError("unsupported bridge method: " + str(payload["method"]))
        evaluator = (
            "aiEvalExpression"
            if payload["method"] == "eval_skill"
            else "aiEvalNativeExpression"
        )
        self.evaluate_path(payload["source_path"], payload["output_path"],
                           evaluator=evaluator, timeout=timeout)
        return True, {"result_path": payload["output_path"]}

    def evaluate_path(self, source, output, evaluator="aiEvalNativeExpression", timeout=50):
        done = self.directory / (uuid.uuid4().hex + ".done")
        command = self.directory / "command.il"
        pending = self.directory / "pending.il"
        pending.write_text('progn(' + evaluator + '(' + json.dumps(str(source)) + ' '
                           + json.dumps(str(output)) + ') aiWriteTextFile('
                           + json.dumps(str(done)) + ' "done"))')
        pending.replace(command)
        self.wait_file(done, timeout=timeout)

    def skill(self, code):
        tag = uuid.uuid4().hex
        source, output = self.directory / (tag + ".il"), self.directory / (tag + ".json")
        source.write_text(code)
        self.evaluate_path(source, output)
        result = json.loads(output.read_text())
        if not result.get("ok"):
            raise HeadlessError(json.dumps(result, ensure_ascii=False))
        return result["value"]


class DispatchClient:
    """Transport adapter: one dispatcher call per request id."""

    def __init__(self, dispatcher):
        self.dispatcher = dispatcher

    def call(self, name, args):
        return True, self.dispatcher(uuid.uuid4().hex, name, args)


def mcp_call(server, name, args):
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": args}})
    if "error" in response:
        raise HeadlessError(json.dumps(response, ensure_ascii=False))
    return json.loads(response["result"]["content"][0]["text"])


@contextmanager
def session(path, *, worklib="genericWork"):
    """Spawn one owned Xvfb + Virtuoso session bound to ``path``."""
    binary = shutil.which(os.environ.get("SICO_AI_VIRTUOSO", "virtuoso"))
    xvfb = shutil.which("xvfb-run")
    if not binary or not xvfb:
        raise HeadlessUnavailable("Virtuoso/Xvfb unavailable")
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    (path / "cds.lib").write_text(
        f"DEFINE analogLib {IC_LIBS}/artist/analogLib\nDEFINE basic {IC_LIBS}/basic\n"
    )
    if os.environ.get("SICO_AI_COMPILED_ROOT"):
        context = AI_ROOT / "context/cadAiFixCtx.cxt"
        lines = [
            f'unless(loadContext({json.dumps(str(context))} t) '
            'error("Compiled context unavailable"))',
            'unless(callInitProc("cadAiFixCtx") error("Compiled context initialization failed"))',
        ]
    else:
        lines = [f"load({json.dumps(str(AI_ROOT / 'skill' / m))})" for m in MODULES]
    lines += [
        f'ddCreateLib({json.dumps(worklib)} {json.dumps(str(path / "library"))})',
        'procedure(createTestPoll() when(isFile("command.il") load("command.il") '
        'deleteFile("command.il")) '
        'hiRegTimer("createTestPoll()" 1))',
        "createTestPoll()",
        'aiWriteTextFile("ready" "yes")',
    ]
    (path / "run.il").write_text("\n".join(lines) + "\n")
    process = None
    try:
        with (path / "process.log").open("w") as log:
            environment = dict(os.environ, PWD=str(path))
            # The source-test runner forces Qt offscreen for PyQt tests;
            # Virtuoso's Qt/X11 stack must bind to the owned Xvfb display.
            environment.pop("QT_QPA_PLATFORM", None)
            process = subprocess.Popen(
                [xvfb, "-a", "-s", "-screen 0 1440x1000x24 -nolisten tcp",
                 binary, "-nocdsinit", "-restore", "run.il", "-log", "CDS.log"],
                cwd=path,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        bridge = NativeBridge(path, process)
        # Session startup under a loaded machine can exceed the request default;
        # the ready handshake is bounded separately from request waits.
        bridge.wait_file(path / "ready", timeout=180)
        (path / "spool").mkdir(mode=0o700)
        server = McpServer(
            DispatchClient(
                RequestDispatcher(
                    bridge,
                    RuntimePaths(path, path / "socket", path / "spool"),
                    path,
                    timeout=float(os.environ.get("SICO_AI_REQUEST_TIMEOUT", "300")),
                )
            ),
            workspace=path,
        )
        yield bridge, server, process
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
