"""Small shared JSON-schema helpers for bounded circuit tools."""
def string_schema(maximum=256, **extra):
    return {"type": "string", "minLength": 1, "maxLength": maximum, **extra}


def tool(name, description, properties, required=(), mutating=False):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties,
                            "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": not mutating, "destructiveHint": False,
                            "idempotentHint": not mutating, "openWorldHint": False}}


PRESENTATION = {"type": "string", "enum": ["foreground", "background"],
                "description": "Use the user's explicit choice at task start; retain it for this task."}
