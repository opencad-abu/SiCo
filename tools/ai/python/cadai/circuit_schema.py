"""Small shared JSON-schema helpers for bounded circuit tools."""
def string_schema(maximum=256, **extra):
    return {"type": "string", "minLength": 1, "maxLength": maximum, **extra}


def tool(name, description, properties, required=(), mutating=False, destructive=False,
         help_text=None, defs=None):
    """One tool definition; ``help_text`` is on-demand detail served by mcp_help.

    It is never part of an advertised MCP row (see ``mcp_registry.wire_row``).
    ``defs`` declares the local ``$defs`` section a factored schema references;
    ``schema_refs.expand`` is the one authoritative expansion.
    """
    schema = {"type": "object", "properties": properties,
              "required": list(required), "additionalProperties": False}
    if defs:
        schema["$defs"] = defs
    row = {"name": name, "description": description, "inputSchema": schema,
           "annotations": {"readOnlyHint": not mutating, "destructiveHint": bool(destructive),
                           "idempotentHint": not mutating, "openWorldHint": False}}
    if help_text:
        row["help"] = help_text
    return row


PRESENTATION = {"type": "string", "enum": ["foreground", "background"],
                "description": (
                    "Use the user's explicit choice at task start; retain it for this task."
                )}
