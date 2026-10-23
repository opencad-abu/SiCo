"""Authoritative identity of the built-in SiCo MCP servers."""

from __future__ import annotations

# Claude Code and Codex render MCP tools as "mcp__<server id>__<tool>". The
# client fixes the "mcp__" wrapper and the "__" separators; the server id
# below is the only part this project chooses.
SERVER_ID = "sico_ic"
PLATFORM_IDS = ("sico_ic", "sico_ae", "sico_cc")
KNOWLEDGE_ID = "sico_knowledge"
RESERVED_IDS = (*PLATFORM_IDS, KNOWLEDGE_ID)
# Platform servers this build always provides for a session; additional
# platforms appear once their integration ships and is configured.
BUILTIN_IDS = (SERVER_ID,)


def qualified_name(tool: str, server_id: str = SERVER_ID) -> str:
    """Return the flat client-visible name of one built-in MCP tool."""
    return f"mcp__{server_id}__{tool}"
