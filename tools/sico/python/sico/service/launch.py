"""SKILL owns one bridge process and separate launchers for each PyQt desktop."""

from __future__ import annotations

from sicoenv import publish as publish_environment

import json
import os
import queue
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from cadenv import preserve_eda_temp_environment

from ..core.contracts import BoundContext
from ..interpreter import agent_command, cad_python
from ..storage.journal import open_private, private_dir
from ..storage.roots import agent_root, export_state_environment, state_root
from ..transport.bridge_client import BridgeClient
from ..transport.framing import strict_json
from ..transport.instance_bridge import InstanceBridge
from ..transport.relay import StdioLines, diagnostic_sink_from_environment, relay
from ..transport.skill_diagnostics import SkillDiagnostics
from ..transport.targets import TargetRegistry, submission


class DesktopPipe:
    """Keep startup controls until registration has reached the owned child."""

    def __init__(self):
        self.child = None
        self.pending = []
        self.targets = None
        self.stopping = False
        self.lock = threading.Lock()
        self.broker = None

    def __call__(self, kind):
        with self.lock:
            if self.stopping:
                return
            message = kind if isinstance(kind, dict) else {"kind": kind}
            self.stopping = message["kind"] == "shutdown"
            if message["kind"] == "submit":
                context = submission(message)
                if self.targets:
                    self.targets.add(context)
                if self.broker:
                    self.broker.register_target(context)
            if self.child is None:
                if self.stopping:
                    self.pending = [message]
                elif len(self.pending) < 24:
                    self.pending.append(message)
                else:
                    raise ValueError("Startup command queue is full")
            else:
                self._write(message)

    def _write(self, message):
        try:
            self.child.stdin.write((json.dumps(message) + "\n").encode())
            self.child.stdin.flush()
        except (OSError, ValueError):
            self.stopping = True

    def attach(self, child, registration):
        with self.lock:
            self.targets = TargetRegistry(BoundContext.from_record(registration["context"]))
            child.stdin.write((json.dumps(registration) + "\n").encode())
            child.stdin.flush()
            self.child = child
            for message in self.pending:
                if message["kind"] == "submit":
                    self.targets.add(submission(message))
                    if self.broker:
                        self.broker.register_target(submission(message))
                self._write(message)
            self.pending = []

    def close(self):
        self("shutdown")
        with self.lock:
            if self.child:
                try:
                    self.child.stdin.close()
                except (OSError, ValueError):
                    pass


def desktop_environment(launch_dir, session_id, environment=None):
    env = dict(os.environ if environment is None else environment)
    # Keep Qt/Python independent of the environment loaded by Virtuoso.
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "LD_PRELOAD",
        "LD_AUDIT",
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_SELECT",
        "QT_DIR",
        "QML2_IMPORT_PATH",
        "QT_XCB_NO_XI2",
        "QT_XCB_NO_XI2_MOUSE",
    ):
        env.pop(name, None)
    publish_environment(env, "SICO_PYTHON", cad_python(env))
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    state = state_root(launch_dir, create=True)
    root = agent_root(launch_dir, create=True) / "runtime"
    private_dir(root)
    runtime = root / session_id
    private_dir(runtime)
    export_state_environment(state, env)
    preserve_eda_temp_environment(env)
    for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME"):
        env[name] = str(runtime)
    publish_environment(env, "SICO_AI_ROUTER_DIAGNOSTIC_LOG", str(
        root.parent / "sessions" / session_id / "router-diagnostic.jsonl"
    ))
    return env


def launch_desktop(args):
    control = DesktopPipe()
    reader = StdioLines(sys.stdin.buffer, control=control)
    try:
        return _launch_desktop(args, reader, control)
    finally:
        control.close()
        reader.close()


def bridge_control(reader, bridge):
    """Keep lifecycle strings out of the native binding event handler."""
    def receive(message):
        if message == "shutdown":
            reader.closed.set()
        elif message == "show":
            return  # The instance bridge has no desktop to show.
        elif isinstance(message, dict):
            bridge.native_binding_event(message)
        else:
            raise ValueError("Invalid instance bridge control message")

    return receive


def launch_bridge(args):
    from ..transport.router_archive import capture_archive_owner

    reader = StdioLines(sys.stdin.buffer)
    write_lock = threading.Lock()

    class LockedSink:
        def write(self, text):
            with write_lock:
                sys.stdout.write(text)
                sys.stdout.flush()

        def flush(self):
            pass

    sink = LockedSink()
    try:
        registration = strict_json(reader.next())
        context = BoundContext.from_record(registration["context"])
        if not os.path.samefile(args.launch_dir, context.snapshot["cwd"]):
            raise ValueError("Bridge launch directory mismatch")
        with InstanceBridge(
            args.launch_dir,
            context,
            archive_owner=capture_archive_owner(context.instance_id),
            target_released=lambda target: sink.write("@released " + target + "\n"),
        ) as bridge:
            reader.control = bridge_control(reader, bridge)
            with SkillDiagnostics(bridge.path.with_suffix(".skill.jsonl"), bridge.router) as stages:
                sink.write(stages.configuration())
                relay(
                    *bridge.broker.address,
                    bridge.broker.token,
                    reader=reader,
                    registration=registration,
                    targets=bridge.targets,
                    sink=sink,
                    router=bridge.router,
                    diagnostic=diagnostic_sink_from_environment(
                        {"SICO_AI_DIAGNOSTIC_LOG": str(bridge.path.with_suffix(".relay.jsonl"))}
                    ),
                )
        return 0
    finally:
        reader.close()


def _launch_desktop(args, reader, control):
    write_lock = threading.Lock()
    stopped = threading.Event()

    registration = strict_json(reader.next())
    context = BoundContext.from_record(registration["context"])
    session_id = args.session or uuid.uuid4().hex
    launch_dir = Path(args.launch_dir).absolute()
    if not os.path.samefile(launch_dir, context.snapshot["cwd"]):
        raise ValueError("Virtuoso launch directory and Agent launch directory disagree")
    client = BridgeClient.discover(launch_dir, context)
    try:
        client.register_target(context)
        registration = dict(registration, bridge=client.descriptor)
        control.broker = client
        return _run_desktop_child(
            args,
            reader,
            control,
            registration,
            context,
            session_id,
            launch_dir,
            write_lock,
            stopped,
        )
    finally:
        client.close()


def _run_desktop_child(
    args, reader, control, registration, context, session_id, launch_dir, write_lock, stopped
):
    command = agent_command(
        "desktop-worker",
        "--launch-dir",
        str(launch_dir),
        "--session",
        session_id,
    )
    if args.provider_config:
        command += ["--provider-config", args.provider_config]
    environment = desktop_environment(launch_dir, session_id)
    # Keep Python/thread/Qt diagnostics off the host control pipes and available
    # after an unexpected session shutdown. This is private runtime evidence.
    log_path = Path(environment["TMPDIR"]) / "desktop.log"
    with os.fdopen(open_private(log_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND), "ab") as log:
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            env=environment,
            cwd=launch_dir,
        )
    output = None
    monitor_thread = None
    try:
        control.attach(child, dict(registration, environment=dict(os.environ)))
        output = StdioLines(child.stdout, backpressure=True)
        try:
            ready = strict_json(output.next(timeout=20))
        except EOFError:
            if control.stopping or reader.closed.is_set():
                return 0  # Host cancelled while desktop bootstrap was still pending.
            raise
        if ready.get("kind") != "ready":
            raise ValueError("Invalid desktop startup response")
        expected = {
            key: registration["bridge"][key]
            for key in ("instance_id", "generation", "bridge_id", "router_id")
        }
        expected.update(session_id=session_id, target_id=context.target_id)
        if any(ready.get(key) != value for key, value in expected.items()):
            raise ValueError("Desktop bridge or session identity mismatch")

        def monitor():
            try:
                while not stopped.is_set():
                    try:
                        notice = strict_json(output.lines.get(timeout=0.1))
                    except queue.Empty:
                        if output.closed.is_set():
                            return
                        continue
                    kind = notice.get("kind")
                    if kind in {"shown", "hidden"}:
                        with write_lock:
                            sys.stdout.write("@" + notice["kind"] + " " + session_id + "\n")
                            sys.stdout.flush()
                    elif kind in {"accepted", "rejected", "released", "new_session", "reconnect"}:
                        from ..core.contracts import identifier

                        value = identifier(notice.get("id", notice.get("target_id")))
                        if kind == "reconnect" and control.targets.get(value) is None:
                            raise ValueError("Reconnect target was not registered")
                        if kind in {"released", "rejected"}:
                            target = control.targets.get(value)
                            if target:
                                control.broker.release_target(target)
                            control.targets.release(value)
                        if kind == "released":
                            continue  # Only the instance owner may retire a shared SKILL target.
                        with write_lock:
                            sys.stdout.write("@" + kind + " " + value + "\n")
                            sys.stdout.flush()
            except (OSError, ValueError):
                return
            finally:
                stopped.set()
                reader.closed.set()

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()
        while (not stopped.is_set() and not reader.closed.is_set() and not control.stopping
               and child.poll() is None):
            time.sleep(0.05)
    finally:
        control.close()
        reader.close()
        # Normally the child bounds its own explicit shutdown at five seconds.
        # Also reap a child whose Qt loop cannot receive the shutdown request.
        try:
            child.wait(timeout=6)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        stopped.set()
        if monitor_thread:
            monitor_thread.join(timeout=1)
        if output:
            output.close()
        child.stdout.close()
    return child.returncode
