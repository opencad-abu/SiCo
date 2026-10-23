"""Session-owned native process configuration, without touching the terminal runtime."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from sicoenv import RETIRED_NAMES, publish as publish_environment

from cadenv import EDA_TEMP_MARKERS, VIRTUOSO_MARKERS, preserve_eda_temp_environment

from cadai import process_monitor
from cadai.env_names import name as env_name
from cadai.launch import prepare_agent_runtime_environment
from cadai.mcp_server import SERVER_ID
from cadai.optional_resources import DATA_SOURCE_ENV, apply_data_sources
from sico.interpreter import native_entry

from ..diagnostics import DiagnosticLog, sensitive_values
from ..interpreter import cad_python
from ..installation import current, python_path
from ..storage.journal import private_dir
from ..storage.native_home import selected_native_home
from ..storage.roots import export_state_environment
from .model_catalog import prepare as prepare_model_catalog
from .rpc import AppServer
from . import MODEL_PROVIDER
from .runtime_scratch import RuntimeScratch
from cadai.responses_compat import codex_options, unsupported_fields
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
    "SICO_PDK_WORKFLOW",
    *DATA_SOURCE_ENV.values(),
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
    def __init__(self, settings, journal, bridge, *, memory_settings=None,
                 enabled_tools=None, external_resources=None, terminal_route=False,
                 cancelled=None, auxiliary=False):
        from sicoruntime import validate as validate_runtime

        validate_runtime(settings.environment)
        self.wrapper, self.rpc, self.gateway, self.knowledge = None, None, None, None
        self.diagnostic = None
        self.process_record = None
        self.auxiliary_home = None
        if auxiliary:
            self.auxiliary_home = Path(tempfile.mkdtemp(prefix="recap-", dir=journal.directory))
            self.home, self.home_scope = self.auxiliary_home, "recap"
        else:
            self.home, self.home_scope = selected_native_home(
                journal.root, journal.session_id)
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
        if "SICO_PDK_WORKFLOW" in settings.environment:
            environment["SICO_PDK_WORKFLOW"] = settings.environment["SICO_PDK_WORKFLOW"]
        for name in ("SICO_MCP_PORT", "SICO_MCP_TOKEN", "SICO_MCP_TIMEOUT"):
            for old in RETIRED_NAMES[name]:
                environment.pop(old, None)
        environment.pop(settings.options.get("api_key_env", env_name("API_KEY")), None)
        self.external_mcp = None
        python = cad_python(settings.environment)
        package_root = str(current().tool("sico") / "python")
        environment.update(
            CODEX_HOME=str(self.home),
            PYTHONPATH=python_path(),
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
        )
        if bridge is not None:
            environment.update(SICO_MCP_PORT=str(bridge.port), SICO_MCP_TOKEN=bridge.token,
                               SICO_MCP_TIMEOUT=str(bridge.timeout))
        publish_environment(environment, "SICO_PYTHON", python)
        # The journal root is <launch>/.sico/ai/agent or legacy <launch>/.cad/ai/agent. Keep the state
        # identity at the project root and preserve the legacy EDA contract
        # only for projects that have not completed activation.
        state = journal.root.parents[1]
        apply_data_sources(environment, state.parent, source=settings.environment)
        export_state_environment(state, environment)
        preserve_eda_temp_environment(environment)
        for key in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            environment[key] = str(temporary)
        base_url = settings.base_url
        api_key = settings.api_key
        self.diagnostic = DiagnosticLog(
            journal.directory / "diagnostics.jsonl",
            component="codex",
            secrets_=sensitive_values(environment),
        )
        try:
            if external_resources is not None and not auxiliary:
                from .external_mcp import ExternalMcp

                self.external_mcp = ExternalMcp(external_resources, settings)
                for reference in self.external_mcp.references:
                    environment.pop(reference, None)
                environment.update(self.external_mcp.environment)
                for source in external_resources.get("knowledge_sources", []):
                    environment.pop(source.get("credential_ref"), None)
                if any(r["enabled"] for r in external_resources.get("knowledge_sources", [])):
                    from .knowledge_runtime import KnowledgeRuntime

                    self.knowledge = KnowledgeRuntime(
                        state.parent, external_resources, settings,
                        diagnostic=self.diagnostic,
                    )
                    for reference in self.knowledge.references:
                        environment.pop(reference, None)
                    environment.update(self.knowledge.environment)
                    self.external_mcp.config["sico_knowledge"] = self.knowledge.config
            executable = settings.executable()
            prepare_agent_runtime_environment("codex", executable, environment)
            endpoint = settings.endpoint
            if settings.environment.get("SICO_CODEX_LSF_EXE", "").strip():
                from .lsf_gateway import LsfGateway

                self.gateway = LsfGateway(
                    settings, state.parent, journal.directory / "lsf-gateway",
                    auxiliary=auxiliary,
                )
                base_url, api_key = self.gateway.base_url, self.gateway.token
                endpoint = base_url + "/responses"
            if settings.tool_format == "flat":
                self.wrapper = ResponsesWrapper(
                    endpoint, api_key, timeout=settings.timeout,
                    idle_timeout=settings.idle_timeout,
                    unsupported_fields=unsupported_fields(settings.model),
                )
                base_url, api_key = self.wrapper.base_url, self.wrapper.token
            environment.pop("CAD_COPILOT_MODEL_KEY", None)
            environment["SICO_MODEL_KEY"] = api_key
            self.cwd = str(state.parent)
            from .feature_groups import (
                configured_overrides,
                project_default_path,
                read_project_default,
                runtime_options,
            )

            overrides = configured_overrides(journal)
            if overrides is None:
                overrides = read_project_default(
                    project_default_path(Path(journal.root).parents[1]))
            features = runtime_options(overrides)
            from .terminal_route import options as terminal_route_options

            features.update(terminal_route_options(terminal_route))
            options = {
                **features,
                **codex_options(settings.model),
                "shell_environment_policy.exclude": ["SICO_RESOURCE_*", "SICO_KNOWLEDGE_*", "SICO_MODEL_KEY",
                                                       "SICO_MCP_TOKEN"],
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
            }
            if bridge is not None:
                options.update({
                    f"mcp_servers.{SERVER_ID}.command": str(native_entry("sico-mcp") or python),
                    f"mcp_servers.{SERVER_ID}.args": (
                        [] if native_entry("sico-mcp") else ["-s", "-m", "sico.codex.mcp"]
                    ),
                    f"mcp_servers.{SERVER_ID}.cwd": package_root,
                    f"mcp_servers.{SERVER_ID}.env_vars": mcp_environment_vars(environment),
                    f"mcp_servers.{SERVER_ID}.required": True,
                    # Preserve the host's existing tool authorization while the
                    # thread allows MCP form/URL requests. External roots keep
                    # their own declared per-tool approval policy.
                    f"mcp_servers.{SERVER_ID}.default_tools_approval_mode": "approve",
                    f"mcp_servers.{SERVER_ID}.startup_timeout_sec": 10,
                    f"mcp_servers.{SERVER_ID}.tool_timeout_sec": bridge.timeout,
                })
            if not auxiliary:
                catalog = prepare_model_catalog(settings, self.home, executable)
                if catalog["path"] is not None:
                    options["model_catalog_json"] = str(catalog["path"])
            if enabled_tools is not None:
                # Client-side filter from the same snapshot the bridge serves;
                # never a substitute for the dispatch-side membership check.
                options[f"mcp_servers.{SERVER_ID}.enabled_tools"] = list(enabled_tools)
            from .thread_memory import runtime_options

            options.update(runtime_options(journal, memory_settings, model=settings.model))
            if auxiliary:
                from .recap_request import isolated_options

                options.update(isolated_options())
            if settings.options.get("linux_sandbox") == "legacy-landlock":
                options["features.use_legacy_landlock"] = True
            command = [executable, "app-server", "--listen", "stdio://"]
            for key, value in options.items():
                command += ["-c", key + "=" + json.dumps(value, ensure_ascii=False)]
            self.rpc = AppServer(
                command, environment, self.cwd, cancelled=cancelled,
                diagnostic=self.diagnostic,
            )
            self.resource_environment_names = frozenset(
                k for k, v in environment.items() if v and not k.startswith("SICO_RESOURCE_")
                and not k.startswith("SICO_KNOWLEDGE_") and k not in {"SICO_MODEL_KEY", "SICO_MCP_TOKEN"})
            self.process_record = process_monitor.track(
                self.rpc.child, [executable, "app-server"], self.cwd,
                session_id=str(journal.directory), hidden=True,
            )
        except BaseException:
            self.close()
            raise

    def close(self):
        errors = []
        for name in ("knowledge", "rpc", "wrapper", "gateway"):
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
        if self.auxiliary_home is not None and not errors:
            shutil.rmtree(self.auxiliary_home)
            self.auxiliary_home = None
        if errors:
            raise errors[0]
