"""Session-owned native process configuration, without touching the terminal runtime."""

from __future__ import annotations

import json
from pathlib import Path

from sicoenv import RETIRED_NAMES, publish as publish_environment

from cadenv import EDA_TEMP_MARKERS, VIRTUOSO_MARKERS, preserve_eda_temp_environment

from cadai import process_monitor
from cadai.env_names import name as env_name
from cadai.launch import prepare_agent_runtime_environment
from sico.interpreter import native_entry

from ..interpreter import cad_python
from ..installation import current, python_path
from ..storage.journal import private_dir
from ..storage.roots import export_state_environment
from .rpc import AppServer
from . import MODEL_PROVIDER
from .runtime_scratch import RuntimeScratch
from cadai.responses_wrapper import ResponsesWrapper

_MCP_ENV_VARS = (
    "SICO_MCP_PORT",
    "SICO_MCP_TOKEN",
    "SICO_MCP_TIMEOUT",
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "PYTHONNOUSERSITE",
    "PYTHONDONTWRITEBYTECODE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SQLITE_TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "SICO_TEMP_DIR",
    # Legacy projects still use the vendor/EDA temporary-root contract until
    # their state is explicitly activated into .sico.
    "CAD_TEMP_DIR",
    "SICO_PYTHON",
    *EDA_TEMP_MARKERS,
)

# Keep this aligned with the launcher's finite capture list, not a prefix match.
_CAPTURED_VIRTUOSO_ENV = VIRTUOSO_MARKERS


def mcp_environment_vars(environment):
    """Preserve captured EDA values without exporting model credentials to MCP."""
    inherited = [key for key in _CAPTURED_VIRTUOSO_ENV if key in environment]
    return [*_MCP_ENV_VARS, *inherited]


class CodexRuntime:
    def __init__(self, settings, journal, bridge, *, memory_settings=None):
        self.wrapper, self.rpc, self.gateway = None, None, None
        self.process_record = None
        self.home = journal.directory / "codex"
        private_dir(self.home)
        self.scratch = RuntimeScratch(self.home)
        temporary = self.scratch.path
        environment = dict(settings.environment)
        stripped_prefixes = (
            "CODEX_", "OPENAI_", "ANTHROPIC_", "CLAUDE_", "SICO_", "CAD_AGENT_",
            "CAD_CODEX_", "CAD_CLAUDE_", "CAD_COPILOT_", "CAD_STUDIO_",
        )
        for key in list(environment):
            if key.startswith(stripped_prefixes) and key not in EDA_TEMP_MARKERS:
                environment.pop(key)
        for name in ("SICO_MCP_PORT", "SICO_MCP_TOKEN", "SICO_MCP_TIMEOUT"):
            for old in RETIRED_NAMES[name]:
                environment.pop(old, None)
        environment.pop(settings.options.get("api_key_env", env_name("API_KEY")), None)
        python = cad_python(settings.environment)
        package_root = str(current().tool("sico") / "python")
        environment.update(
            CODEX_HOME=str(self.home),
            SICO_MCP_PORT=str(bridge.port),
            SICO_MCP_TOKEN=bridge.token,
            SICO_MCP_TIMEOUT=str(bridge.timeout),
            PYTHONPATH=python_path(),
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
        )
        publish_environment(environment, "SICO_PYTHON", python)
        # The journal root is <launch>/.sico/ai/agent or legacy <launch>/.cad/ai/agent. Keep the state
        # identity at the project root and preserve the legacy EDA contract
        # only for projects that have not completed activation.
        state = journal.root.parents[1]
        export_state_environment(state, environment)
        preserve_eda_temp_environment(environment)
        for key in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            environment[key] = str(temporary)
        base_url = settings.endpoint.rsplit("/responses", 1)[0]
        api_key = settings.api_key
        try:
            executable = settings.executable()
            prepare_agent_runtime_environment("codex", executable, environment)
            endpoint = settings.endpoint
            if settings.environment.get("SICO_CODEX_LSF_EXE", "").strip():
                from .lsf_gateway import LsfGateway

                self.gateway = LsfGateway(settings, state.parent, self.home / "lsf-gateway")
                base_url, api_key = self.gateway.base_url, self.gateway.token
                endpoint = base_url + "/responses"
            if settings.tool_format == "flat":
                self.wrapper = ResponsesWrapper(
                    endpoint, api_key, timeout=settings.timeout,
                    idle_timeout=settings.idle_timeout,
                )
                base_url, api_key = self.wrapper.base_url, self.wrapper.token
            environment.pop("CAD_COPILOT_MODEL_KEY", None)
            environment["SICO_MODEL_KEY"] = api_key
            self.cwd = str(state.parent)
            options = {
                "features.goals": True,
                "model": settings.model,
                "model_provider": MODEL_PROVIDER,
                f"model_providers.{MODEL_PROVIDER}.name": "SiCo gateway",
                f"model_providers.{MODEL_PROVIDER}.base_url": base_url,
                f"model_providers.{MODEL_PROVIDER}.env_key": "SICO_MODEL_KEY",
                f"model_providers.{MODEL_PROVIDER}.wire_api": "responses",
                f"model_providers.{MODEL_PROVIDER}.supports_websockets": False,
                f"model_providers.{MODEL_PROVIDER}.request_max_retries": 0,
                f"model_providers.{MODEL_PROVIDER}.stream_max_retries": 0,
                f"model_providers.{MODEL_PROVIDER}.stream_idle_timeout_ms": int(settings.idle_timeout * 1000),
                # No model work starts until the service configures the thread.
                "approval_policy": "never",
                "approvals_reviewer": "user",
                "sandbox_mode": "workspace-write",
                "web_search": "live",
                "analytics.enabled": False,
                "features.shell_tool": True,
                "features.shell_snapshot": True,
                "features.enable_request_compression": False,
                "features.unbounded_connection_retries": False,
                "features.plugins": True,
                "features.apps": True,
                # Native collaboration is used for long read-only preparation
                # jobs and supplies the parent/child lifecycle shown in Copilot.
                "features.multi_agent": True,
                "features.multi_agent_v2": True,
                "features.default_mode_request_user_input": True,
                "features.image_generation": True,
                "features.browser_use": True,
                "features.computer_use": True,
                "features.view_image": True,
                "features.unified_exec": True,
                "features.unified_exec_tty": True,
                "features.sleep_tool": True,
                "features.auth_elicitation": True,
                "features.tool_call_mcp_elicitation": True,
                "features.skill_mcp_dependency_install": True,
                "mcp_servers.virtuoso.command": str(native_entry("sico-mcp") or python),
                "mcp_servers.virtuoso.args": (
                    [] if native_entry("sico-mcp") else ["-s", "-m", "sico.codex.mcp"]
                ),
                "mcp_servers.virtuoso.cwd": package_root,
                "mcp_servers.virtuoso.env_vars": mcp_environment_vars(environment),
                "mcp_servers.virtuoso.required": True,
                # Preserve the host's existing tool authorization while the
                # thread allows MCP form/URL requests. External roots keep
                # their own declared per-tool approval policy.
                "mcp_servers.virtuoso.default_tools_approval_mode": "approve",
                "mcp_servers.virtuoso.startup_timeout_sec": 10,
                "mcp_servers.virtuoso.tool_timeout_sec": bridge.timeout,
            }
            from .thread_memory import runtime_options

            options.update(runtime_options(journal, memory_settings, model=settings.model))
            if settings.options.get("linux_sandbox") == "legacy-landlock":
                options["features.use_legacy_landlock"] = True
            command = [executable, "app-server", "--listen", "stdio://"]
            for key, value in options.items():
                command += ["-c", key + "=" + json.dumps(value, ensure_ascii=False)]
            self.rpc = AppServer(command, environment, self.cwd)
            self.process_record = process_monitor.track(
                self.rpc.child, [executable, "app-server"], self.cwd,
                session_id=str(journal.directory), hidden=True,
            )
        except BaseException:
            self.close()
            raise

    def close(self):
        errors = []
        for name in ("rpc", "wrapper", "gateway"):
            resource = getattr(self, name)
            if resource is None:
                continue
            try:
                resource.close()
                if name == "rpc":
                    process_monitor.finish(self.process_record, resource.child.returncode)
                setattr(self, name, None)
            except (OSError, RuntimeError) as exc:
                errors.append(exc)
        self.scratch.close()
        if errors:
            raise errors[0]
