"""Select the Assistant MCP process entry for source and native installations."""

from pathlib import Path
from sicoentry import compiled_module, native_entry


def mcp_command(python, source_entry, timeout, *, environment=None):
    arguments = ["mcp", "--request-timeout", f"{timeout + 5:g}"]
    if not compiled_module(__file__):
        return [python, "-s", str(source_entry), *arguments]
    entry = native_entry("aiassistant", environment=environment,
                         anchor=Path(__file__).resolve().parents[4])
    return [str(entry), *arguments]
