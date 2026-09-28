"""Bounded reads of checksummed artifacts belonging to the active session."""

from ..core.contracts import ToolResult
from ..core.tools import ReadTool


def register_artifacts(registry, journal):
    def validate(inputs):
        if not isinstance(inputs, dict) or set(inputs) - {"path", "sha256", "offset", "limit"}:
            raise ValueError("Unexpected artifact arguments")
        if not all(isinstance(inputs.get(key), str) for key in ("path", "sha256")):
            raise ValueError("path and sha256 are required")
        for key, default in (("offset", 0), ("limit", 4000)):
            if type(inputs.get(key, default)) is not int:
                raise ValueError("Artifact offsets and limits must be integers")
        if inputs.get("offset", 0) < 0:
            raise ValueError("Artifact offset must be >= 0 characters")
        if not 1 <= inputs.get("limit", 4000) <= 4000:
            raise ValueError("Artifact limit must be between 1 and 4000 characters")

    def read(inputs, context):
        try:
            return ToolResult(data=journal.read_artifact(**inputs))
        except ValueError as exc:
            return ToolResult("invalid_arguments", str(exc), {"code": "artifact_invalid"})
        except OSError:
            return ToolResult("tool_error", "Session artifact is unavailable or unreadable",
                              {"code": "artifact_unavailable"})

    registry.register(
        ReadTool(
            "read_artifact",
            "Read an archived session result using its path and checksum; offsets are characters.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "sha256": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 4000},
                },
                "required": ["path", "sha256"],
                "additionalProperties": False,
            },
            validate,
            read,
            execution_domain="agent",
        )
    )
