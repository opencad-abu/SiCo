"""MCP protocol orchestration and ownership of per-server resources.

Compatibility facade: TOOLS, _TOOLS_BY_NAME, SERVER_INSTRUCTIONS and SocketClient
remain aliases for existing launch/agent adapters. Remove each alias only after
those consumers migrate to its owning module; never maintain parallel data.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .agent_profile import INTERACTIVE_PROFILE, validate_profile
from .candidate_ledger import CandidateLedger, CandidateLedgerError
from .entry_context import call_context, startup_context
from .mcp_bindings import create_bindings
from .mcp_call_errors import (
    invalid_arguments,
    invalid_call_shape,
    normalize_tool_result,
    rejected_tool_call,
    unavailable_tool,
    unknown_tool,
)
from .mcp_definitions import TOOLS, resolve_tool_name
from .mcp_definitions import TOOLS_BY_NAME as _TOOLS_BY_NAME
from .mcp_environment import run_mcp_from_environment
from .mcp_instructions import SERVER_INSTRUCTIONS, VERIFICATION_SERVER_INSTRUCTIONS
from .mcp_protocol import error as mcp_error
from .mcp_protocol import handle_message, serve, tool_result
from .mcp_protocol import result as mcp_result
from .mcp_registry import ToolArgumentError, wire_row
from .mcp_transport import SocketClient
from .pdk_binding import PdkBindings
from .pdk_schema import PdkUnavailable
from .pdk_session import PdkSession
from .protocol import MAX_LINE_BYTES
from .schematic_snapshot import SchematicSnapshotStore, SnapshotError
from .skill_reference import SkillReference


class McpServer:
    def __init__(
        self,
        client: SocketClient,
        reader=None,
        writer=None,
        skill_reference: SkillReference | None = None,
        workspace: Path | None = None,
        snapshot_store: SchematicSnapshotStore | None = None,
        profile: str = INTERACTIVE_PROFILE,
    ):
        validate_profile(profile)
        self.client = client
        self.reader = reader or sys.stdin.buffer
        self.writer = writer or sys.stdout.buffer
        self.skill_reference = skill_reference or SkillReference(workspace=workspace)
        self.workspace = workspace
        # Keep construction side-effect free.  A few embedders use a minimal
        # client object for non-snapshot tools; create the index only when the
        # first snapshot operation is requested.
        self.snapshot_store = snapshot_store
        self.profile = profile
        self._candidate_ledger: CandidateLedger | None = None
        self._pdk_session: PdkSession | None = None
        self._pdk_bindings = None
        self._tools = create_bindings(
            client=client, workspace=workspace, skill_reference=self.skill_reference,
            get_pdk_session=self.pdk_session, get_pdk_bindings=self.pdk_bindings,
            get_snapshot_store=self._snapshot_store,
            get_candidate_ledger=self._candidate_store,
        )

    def pdk_session(self):
        if self._pdk_session is None:
            entry = getattr(self.client, "pdk_entry_context", None)
            self._pdk_session = PdkSession(
                self.client, workspace=self.workspace,
                entry=entry or (lambda: call_context("get_entry_context", {}, self.client)[1]),
            )
        return self._pdk_session

    def pdk_bindings(self, *, create=True):
        if not create:
            return self._pdk_bindings
        self.pdk_session()
        if self._pdk_bindings is None:
            self._pdk_bindings = PdkBindings(self._pdk_session, self.client)
        return self._pdk_bindings

    def pdk_selection_guard(self, name):
        preparation = getattr(self._pdk_session, "preparation", None)
        return preparation.guard(name) if preparation is not None else None

    def run(self) -> int:
        try:
            serve(self.reader, self.writer, self.handle, max_line_bytes=MAX_LINE_BYTES)
        finally:
            self.close()
        return 0

    def close(self) -> None:
        """Release in dependency order, attempting every close even after failure."""
        failure = None
        for resource in (self._pdk_session, self.snapshot_store, self.skill_reference, self.client):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    if failure is None:
                        failure = exc
        if failure is not None:
            raise failure

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        return handle_message(
            message,
            instructions=self._instructions,
            list_tools=self._list_tools,
            call_tool=self._tool_call,
        )

    def _instructions(self) -> str:
        if self.profile == "verification":
            return VERIFICATION_SERVER_INSTRUCTIONS
        return SERVER_INSTRUCTIONS + startup_context(self.client)

    def _list_tools(self) -> list[dict[str, Any]]:
        return [wire_row(tool.schema) for tool in self._tools.values()
                if tool.allows(self.profile)]

    def _tool_call(self, request_id: Any, params: Any) -> dict[str, Any]:
        from .skill_diagnostics import SkillDiagnostics

        with SkillDiagnostics() as diagnostics:
            return self._tool_call_with_diagnostics(request_id, params, diagnostics)

    def _tool_call_with_diagnostics(self, request_id, params, diagnostics):
        if not isinstance(params, dict):
            message, data = invalid_call_shape(
                "tools/call params must be an object",
                expected='{"name": string, "arguments": object}',
                received=params,
            )
            return self._error(request_id, -32602, message, data)
        requested_name = params.get("name")
        if not isinstance(requested_name, str) or not requested_name:
            message, data = invalid_call_shape(
                "tools/call requires params.name",
                expected="non-empty tool name string",
                received=requested_name,
            )
            return self._error(request_id, -32602, message, data)
        available = [name for name, entry in self._tools.items() if entry.allows(self.profile)]
        name = resolve_tool_name(requested_name)
        arguments = params.get("arguments", {})
        tool = self._tools.get(name) if name is not None else None
        if tool is None:
            message, data = unknown_tool(requested_name, available)
            return self._error(request_id, -32602, message, data)
        if not tool.allows(self.profile):
            message, data = unavailable_tool(requested_name, self.profile, available)
            return self._error(request_id, -32602, message, data)
        if not isinstance(arguments, dict):
            message, data = invalid_call_shape(
                f"Tool {name!r} requires params.arguments",
                expected="JSON object",
                received=arguments,
                tool_name=name,
                schema=tool.schema["inputSchema"],
            )
            return self._error(request_id, -32602, message, data)
        rejected = invalid_arguments(name, tool.schema["inputSchema"], arguments)
        if rejected is not None:
            message, data = rejected
            return self._error(request_id, -32602, message, data)
        try:
            selection = (None if name == "execute_circuit_operation"
                         and arguments.get("operation") == "apply_cdf_update"
                         else self.pdk_selection_guard(name))
        except PdkUnavailable as exc:
            selection = {"ok": False, "code": exc.code, "message": str(exc)}
            if isinstance(getattr(exc, "data", None), dict):
                selection.update(exc.data)
        if selection is not None:
            selection = diagnostics.attach(selection)
            ok = selection.get("ok") is not False
            if not ok:
                selection = normalize_tool_result(
                    selection, name, tool.schema["inputSchema"]
                )
            return tool_result(
                request_id,
                ok,
                selection,
                compact=tool.compact,
                tool_name=name,
                input_schema=tool.schema["inputSchema"],
            )
        try:
            ok, detail = tool.invoke(arguments, profile=self.profile)
        except ToolArgumentError as exc:
            message, data = rejected_tool_call(
                name, str(exc), tool.schema["inputSchema"]
            )
            response = self._error(request_id, -32602, message, data)
            output = diagnostics.attach({})
            if output.get("output") or output.get("output_truncated"):
                response["error"]["data"] = {**data, **output}
            return response
        detail = diagnostics.attach(detail)
        if not ok:
            detail = normalize_tool_result(detail, name, tool.schema["inputSchema"])
        return tool_result(
            request_id,
            ok,
            detail,
            compact=tool.compact,
            tool_name=name,
            input_schema=tool.schema["inputSchema"],
        )

    def _snapshot_store(self) -> SchematicSnapshotStore:
        if self.snapshot_store is None:
            spool = getattr(getattr(self.client, "runtime", None), "spool", None)
            if not isinstance(spool, Path):
                raise SnapshotError("schematic snapshot storage is unavailable")
            self.snapshot_store = SchematicSnapshotStore(spool)
        return self.snapshot_store

    def _candidate_store(self) -> CandidateLedger:
        # Interactive product sessions expose every registered capability.
        # Candidate staging stays bounded to the private session spool and has
        # no OA publication path; the verification profile only hides unrelated
        # tools from that specialized agent.
        if self.profile not in {INTERACTIVE_PROFILE, "verification"}:
            raise CandidateLedgerError("candidate tools require an interactive session")
        if self._candidate_ledger is None:
            runtime = getattr(self.client, "runtime", None)
            spool = getattr(runtime, "spool", None)
            if not isinstance(spool, Path):
                raise CandidateLedgerError("candidate ledger storage is unavailable")
            self._candidate_ledger = CandidateLedger(spool)
        return self._candidate_ledger

    _result = staticmethod(mcp_result)
    _error = staticmethod(mcp_error)


__all__ = [
    "McpServer",
    "SERVER_INSTRUCTIONS",
    "SocketClient",
    "TOOLS",
    "_TOOLS_BY_NAME",
    "run_mcp_from_environment",
]
