"""Lifecycle owner for one Virtuoso, terminal, and AI agent MCP session.

Request hooks keep dispatch, unknown-execution state and session resources here.
Stateless helpers receive only paths, values and required I/O operations.
Compatibility aliases retain their original import and injection locations;
retire only the registered aliases after supported consumers migrate.
"""

from __future__ import annotations

from sicoenv import read as environment_setting, publish as publish_environment

import math
import os
import secrets
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sicostate import export_environment
from sicotemp import ai_directory

from .agent_profile import INTERACTIVE_PROFILE, validate_profile
from .aivw_tool import run_aivw_recipe
from .codex_adapter import CodexCliAdapter
from .controller_artifact import materialize_snapshot_artifact
from .controller_live import dispatch_live, run_live_recipe
from .controller_live import send_transport_event as _send_transport_event
from .controller_live import snapshot_binding_from_aivw as _snapshot_binding_from_aivw
from .controller_paths import session_install_roots as _session_install_roots
from .controller_paths import session_roots as _session_roots
from .controller_result import bounded_result, result_payload
from .controller_source import (
    config_binding_source,
    eval_source,
    materialize_skill_file,
    snapshot_source,
    template_source,
)
from .launch import (
    build_claude_command,
    build_claude_mcp_config,
    build_codex_command,
    install_packaged_codex_skills,
    prepare_agent_runtime_environment,
    prepare_codex_gateway,
    prepare_terminal_environment,
    private_codex_home,
    production_python,
    resolve_agent_executable,
    resolve_executable,
    sanitized_terminal_environment,
)
from .live_model import LiveModelSession
from .protocol import ProtocolError
from .runtime import (
    RuntimePaths,
    new_token,
    write_spool,
)
from .socket_server import RequestFailure, UnixRequestServer
from .terminal_control import (
    TERMINAL_CONTROL_FD_ENV,
    TERMINAL_SHUTDOWN_TIMEOUT,
)
from .terminal_control import (
    TerminalControl as _TerminalControl,
)
from .terminal_control import (
    shutdown_terminal as _shutdown_terminal,
)
from .terminal_control import (
    wait_for_terminal as _wait_for_terminal,
)
from .transport import TransportClosed, VirtuosoTransport

INLINE_RESULT_BYTES = 65_536
DEFAULT_REQUEST_TIMEOUT = 300.0
RESULT_VISIBILITY_TIMEOUT = 2.0
SCHEMATIC_SNAPSHOT_METHOD = "snapshot_schematic"
CONFIG_BINDING_METHOD = "inspect_config_binding"
SCHEMATIC_SNAPSHOT_MAX_BYTES = 256 * 1024 * 1024


def _snapshot_generation_from_aivw(value: Mapping[str, Any]) -> str | None:
    """Backward-compatible accessor for callers that only need the hash."""

    binding = _snapshot_binding_from_aivw(value)
    return binding["source_generation"] if binding is not None else None


class RequestDispatcher:
    def __init__(
        self,
        transport: VirtuosoTransport,
        runtime: RuntimePaths,
        workspace: Path,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        live_session: LiveModelSession | None = None,
    ) -> None:
        self.transport = transport
        self.runtime = runtime
        self.workspace = workspace
        self.timeout = timeout
        self._execution_status_unknown = threading.Event()
        self.live_session = live_session

    def attach_live_session(self, session: LiveModelSession) -> None:
        self.live_session = session

    @property
    def runtime_cleanup_safe(self) -> bool:
        return not self._execution_status_unknown.is_set()

    def __call__(self, request_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method.startswith("live_model_"):
            return self._dispatch_live(method, params)
        if method not in {
            "eval_skill",
            "eval_skill_native",
            "load_skill_file",
            "get_context",
            SCHEMATIC_SNAPSHOT_METHOD,
            CONFIG_BINDING_METHOD,
            "capture_circuit_template",
        }:
            raise RequestFailure("unknown_method", f"unsupported Virtuoso method: {method}")
        # Do not stat a not-yet-created result on the reader's NAS client:
        # that can cache ENOENT before Virtuoso creates it on another host.
        # Socket request IDs are separately deduplicated; the random suffix
        # gives each dispatch a fresh result path without a negative lookup.
        result_name = f"result-{request_id}-{secrets.token_hex(12)}.json"
        result_path = self.runtime.virtuoso_spool / result_name
        request: dict[str, Any] = {
            "id": request_id,
            "method": method,
            "output_path": str(result_path),
        }
        snapshot_path: Path | None = None
        if method in {"eval_skill", "eval_skill_native"}:
            source = self._eval_source(params)
            metadata = write_spool(self.runtime.virtuoso_spool, "source", source)
            request["source_path"] = metadata["path"]
        elif method == "load_skill_file":
            request["source_path"] = str(self._materialize_skill_file(params))
        elif method == SCHEMATIC_SNAPSHOT_METHOD:
            source_path, snapshot_path = self._snapshot_source(params)
            request["source_path"] = str(source_path)
        elif method == "capture_circuit_template":
            source_path, snapshot_path = self._template_source(params)
            request["source_path"] = str(source_path)
        elif method == CONFIG_BINDING_METHOD:
            source_path = self._config_binding_source(params)
            request["source_path"] = str(source_path)
        elif params:
            raise RequestFailure("invalid_params", "get_context takes no arguments")

        try:
            ok, detail = self.transport.request(request, timeout=self.timeout)
        except TimeoutError as exc:
            self._execution_status_unknown.set()
            raise RequestFailure("timeout", str(exc)) from exc
        except (OSError, ProtocolError, TransportClosed) as exc:
            self._execution_status_unknown.set()
            raise RequestFailure(
                "bridge_closed",
                f"{exc}; execution status is unknown and the request was not retried",
            ) from exc
        source_path = request.get("source_path")
        if isinstance(source_path, str):
            Path(source_path).unlink(missing_ok=True)
        try:
            payload, raw_result = self._result_payload(result_name, wait_for_visibility=ok)
        except RequestFailure as exc:
            if snapshot_path is not None:
                snapshot_path.unlink(missing_ok=True)
            if not ok:
                # A SKILL/context/write failure may legitimately have no result
                # file. Preserve the bridge diagnostic instead of masking it
                # with the subsequent ENOENT from the spool reader.
                raise RequestFailure(
                    "skill_error", str(detail.get("message", "SKILL execution failed"))
                ) from exc
            raise
        except BaseException:
            if snapshot_path is not None:
                snapshot_path.unlink(missing_ok=True)
            raise
        if not ok:
            result_path.unlink(missing_ok=True)
            if snapshot_path is not None:
                snapshot_path.unlink(missing_ok=True)
            message = detail.get("message", "SKILL execution failed")
            raise RequestFailure("skill_error", str(message), payload)
        if payload.get("ok") is not True:
            result_path.unlink(missing_ok=True)
            if snapshot_path is not None:
                snapshot_path.unlink(missing_ok=True)
            raise RequestFailure("skill_error", str(payload.get("error", "SKILL failed")), payload)
        if method == SCHEMATIC_SNAPSHOT_METHOD:
            result_path.unlink(missing_ok=True)
            assert snapshot_path is not None
            try:
                return self._materialize_snapshot_artifact(payload, snapshot_path)
            except BaseException:
                try:
                    snapshot_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        if method == "capture_circuit_template":
            result_path.unlink(missing_ok=True)
            assert snapshot_path is not None
            try:
                return self._materialize_snapshot_artifact(
                    payload, snapshot_path, artifact_prefix="template-data-"
                )
            except BaseException:
                snapshot_path.unlink(missing_ok=True)
                raise
        if method == CONFIG_BINDING_METHOD:
            result_path.unlink(missing_ok=True)
            return self._bounded_result(payload, result_name, raw_result)
        return self._bounded_result(payload, result_name, raw_result)

    def _dispatch_live(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return dispatch_live(self.live_session, method, params)

    def _eval_source(self, params: dict[str, Any]) -> bytes:
        return eval_source(params, self.runtime.spool)

    def _materialize_skill_file(self, params: dict[str, Any]) -> Path:
        return materialize_skill_file(
            params, self.workspace, self.runtime.virtuoso_spool, write_spool=write_spool
        )

    def _snapshot_source(self, params: dict[str, Any]) -> tuple[Path, Path]:
        return snapshot_source(params, self.runtime.virtuoso_spool, write_spool=write_spool)

    def _template_source(self, params: dict[str, Any]) -> tuple[Path, Path]:
        return template_source(params, self.runtime.virtuoso_spool, write_spool=write_spool)

    def _config_binding_source(self, params: dict[str, Any]) -> Path:
        return config_binding_source(
            params, self.runtime.virtuoso_spool, write_spool=write_spool
        )

    def _materialize_snapshot_artifact(
        self, payload: dict[str, Any], expected_path: Path, *,
        artifact_prefix: str = "snapshot-data-",
    ) -> dict[str, Any]:
        return materialize_snapshot_artifact(
            payload, expected_path, self.runtime.virtuoso_spool, self.runtime.spool,
            artifact_prefix=artifact_prefix, max_bytes=SCHEMATIC_SNAPSHOT_MAX_BYTES,
        )

    def _result_payload(
        self, name: str, *, wait_for_visibility: bool = False
    ) -> tuple[dict[str, Any], bytes]:
        return result_payload(
            self.runtime.virtuoso_spool, name, timeout=self.timeout,
            visibility_timeout=RESULT_VISIBILITY_TIMEOUT, wait_for_visibility=wait_for_visibility,
        )

    def _bounded_result(
        self, payload: dict[str, Any], name: str, raw: bytes
    ) -> dict[str, Any]:
        return bounded_result(
            payload, name, raw, self.runtime.virtuoso_spool, self.runtime.spool,
            inline_bytes=INLINE_RESULT_BYTES, write_spool=write_spool,
        )


def run_session(
    workspace: Path,
    terminal: str | None,
    codex: str | None,
    timeout: float,
    *,
    agent: str = "codex",
    claude: str | None = None,
    shared_spool: bool = False,
    profile: str = INTERACTIVE_PROFILE,
) -> int:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("request timeout must be a positive finite number")
    if agent not in {"codex", "claude"}:
        raise ValueError(f"unsupported AI agent: {agent}")
    validate_profile(profile)
    workspace = workspace.expanduser().absolute()
    if not workspace.is_dir():
        raise NotADirectoryError(workspace)
    temp_root = ai_directory(create=True)
    python_root, install_root = _session_roots()
    terminal_exe = resolve_executable(
        terminal or environment_setting(os.environ, "SICO_AI_TERMINAL"),
        [install_root / "bin" / "sico-ai-terminal"],
    )
    explicit_agent = codex if agent == "codex" else claude
    session_install_roots = _session_install_roots(install_root, terminal_exe)
    agent_exe = resolve_agent_executable(
        agent,
        explicit_agent,
        install_roots=session_install_roots,
    )
    runtime: RuntimePaths | None = None
    transport: VirtuosoTransport | None = None
    server: UnixRequestServer | None = None
    server_thread: threading.Thread | None = None
    dispatcher: RequestDispatcher | None = None
    live_session: LiveModelSession | None = None
    live_cancel = threading.Event()
    process: subprocess.Popen[bytes] | None = None
    codex_adapter: CodexCliAdapter | None = None
    codex_wrapper = None
    control: _TerminalControl | None = None
    previous: Any = None
    clean_runtime = True
    stop_requested = threading.Event()

    def stop_handler(_signum: int, _frame: object) -> None:
        stop_requested.set()

    previous = signal.signal(signal.SIGTERM, stop_handler)
    try:
        runtime = RuntimePaths.create(
            spool_base=temp_root if shared_spool else None,
            runtime_base=temp_root,
        )
        token = new_token()
        transport = VirtuosoTransport()
        transport.start()
        capability = SimpleNamespace(runtime=runtime, path=runtime.socket, token=token)

        def live_runner(config: Mapping[str, Any], active: Mapping[str, Any]) -> Mapping[str, Any]:
            return run_live_recipe(
                config, active, run_aivw_recipe=run_aivw_recipe, capability=capability,
                workspace=workspace, timeout=timeout,
                cancel_event=live_session.cancel_event if live_session is not None else live_cancel,
                snapshot_binding=_snapshot_binding_from_aivw,
            )

        live_session = LiveModelSession(
            runner=live_runner,
            event_sink=lambda payload: _send_transport_event(transport, payload),
            cancel_event=live_cancel,
        )
        dispatcher = RequestDispatcher(transport, runtime, workspace, timeout, live_session)
        server = UnixRequestServer(runtime.socket, token, dispatcher)
        server_thread = threading.Thread(
            target=server.serve_forever,
            name="cad-ai-unix",
            daemon=True,
        )
        server_thread.start()
        codex_home: Path | None = None
        if agent == "codex":
            codex_home = private_codex_home()
            install_packaged_codex_skills(codex_home, install_root)
            # Resolve the fixed production interpreter only after the session
            # resources and agent-specific setup are ready.  This preserves
            # deterministic cleanup and keeps path/runtime preflight errors
            # distinguishable from interpreter configuration errors.
            child_python = production_python(require_environment=True)
            codex_adapter = CodexCliAdapter(
                agent_exe,
                profile,
                run_id=os.environ.get("AIVW_RUN_ID", "") or "aivw-" + runtime.root.name,
                source_generation=os.environ.get("AIVW_SOURCE_GENERATION") or None,
                provenance_path=os.environ.get("AIVW_PROVENANCE_PATH") or None,
            )
        else:
            child_python = production_python(require_environment=True)
            mcp_environment = dict(os.environ, SICO_AI_WORKSPACE=str(workspace))
            export_environment(mcp_environment, temp_root.parent)
            config = build_claude_mcp_config(
                child_python,
                python_root / "sico-ai",
                runtime,
                token,
                timeout,
                environment=mcp_environment,
                profile=profile,
            )
            config_path = Path(str(write_spool(runtime.spool, "claude-mcp", config)["path"]))
            command = build_claude_command(agent_exe, config_path, profile=profile)
        control = _TerminalControl.create()
        terminal_environment, input_warnings = prepare_terminal_environment(
            runtime, token, workspace, codex_home, agent, temp_root=temp_root.parent, profile=profile
        )
        terminal_environment["SICO_AI_PROFILE"] = profile
        prepare_agent_runtime_environment(agent, agent_exe, terminal_environment)
        if agent == "codex":
            codex_wrapper = prepare_codex_gateway(terminal_environment)
            command = build_codex_command(
                agent_exe, child_python, workspace, python_root, timeout,
                environment=terminal_environment, profile=profile,
            )
        terminal_command = [terminal_exe, "--working-directory", str(workspace), "--", *command]
        publish_environment(terminal_environment, "SICO_PYTHON", child_python)
        python_bin = str(Path(child_python).parent)
        terminal_environment["PATH"] = os.pathsep.join(
            [python_bin, *filter(None, terminal_environment.get("PATH", "").split(os.pathsep))]
        )
        for warning in input_warnings:
            print(f"sico-ai: warning: {warning}", file=sys.stderr, flush=True)
        terminal_environment.pop("CAD_AI_CONTROL_FD", None)
        terminal_environment[TERMINAL_CONTROL_FD_ENV] = str(control.read_fd)
        try:
            process = subprocess.Popen(
                terminal_command,
                cwd=workspace,
                env=terminal_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                pass_fds=(control.read_fd,),
            )
        finally:
            control.close_reader()
        _send_transport_event(
            transport,
            {
                "event": "session.ready",
                "workspace": str(workspace),
                "agent": agent,
                "control_protocol": "control.v2",
            },
        )
        return_code, owner_shutdown = _wait_for_terminal(
            process,
            transport,
            control,
            stop_requested,
            live_event_handler=live_session.handle,
        )
        if owner_shutdown:
            return_code = _shutdown_terminal(process, control)
        assert return_code is not None
        if codex_adapter:
            codex_adapter.finish(
                exit_code=return_code,
                status="completed" if return_code == 0 else "failed",
            )
        _send_transport_event(transport, {"event": "session.exit", "status": return_code})
        return return_code
    finally:
        try:
            if process is not None and process.poll() is None:
                if control is not None:
                    _shutdown_terminal(process, control)
                else:
                    process.terminate()
            if control is not None:
                control.close()
            if live_session is not None:
                live_session.close()
            if transport is not None:
                transport.close()
            if server is not None:
                server.quiesce()
            if server is not None and dispatcher is not None:
                idle = server.wait_idle(TERMINAL_SHUTDOWN_TIMEOUT)
                clean_runtime = clean_runtime and idle and dispatcher.runtime_cleanup_safe
            if server is not None:
                server.shutdown()
                server.server_close()
            if server_thread is not None:
                server_thread.join(timeout=5)
            if clean_runtime and runtime is not None:
                runtime.cleanup()
        finally:
            try:
                if codex_wrapper is not None:
                    codex_wrapper.close()
            finally:
                if previous is not None:
                    signal.signal(signal.SIGTERM, previous)


__all__ = [
    "DEFAULT_REQUEST_TIMEOUT",
    "RequestDispatcher",
    "build_codex_command",
    "private_codex_home",
    "run_session",
    "sanitized_terminal_environment",
]
