"""Finite, argument-free SKILL operations; never serialize model text as SKILL."""

from cadai.entry_context import NAMES as SHARED_READ_METHODS

from .project import PROJECT_READ_METHODS

READ_METHODS = (
    frozenset({"get_context", "get_project_context", "read_ade_setup", "read_ade_history"})
    | SHARED_READ_METHODS
    | PROJECT_READ_METHODS
)

# Original Assistant operations use the same captured Virtuoso source as the
# shared adapters.  The payload is a bounded, preflighted SKILL expression (or
# an exact .il/.ils path for load_skill_file); it is transported separately
# from circuit_call so the finite circuit wire cannot be widened accidentally.
ASSISTANT_METHOD = "assistant_call"


class QueryUnavailable(RuntimeError):
    """A supported read could not complete; the connection and target remain usable."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
