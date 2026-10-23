"""Resolve observed inherited-connection net expressions from captured defaults.

A Cadence net expression ``[@<parameter>:<scope>:<default>]`` on a terminal or a
signal declares an inherited connection: the enclosing design may redirect it and
the source falls back to ``<default>``.  For a self-contained template the
declared default must be the connection the direct-level graph already contains.
When every captured expression parses and agrees with the captured nets, the
expressions are *resolved* and no longer hide a missing connection; anything
uncaptured, unparsable or disagreeing stays unresolved.
"""

import re

from .template_schema import TemplateError

PARAMETER_PRIMARY = "terminal_expressions"
PARAMETER_TEXTS = "terminal_expression_texts"
SIGNAL_PRIMARY = "signal_expressions"
SIGNAL_TEXTS = "signal_expression_texts"
TEXT_FIELDS = (PARAMETER_TEXTS, SIGNAL_TEXTS)
EXPRESSION = re.compile(
    r"^\[@(?P<parameter>[^:\[\]]+):(?P<scope>[^:\[\]]+):(?P<default>[^\[\]]+)\]$"
)
RESOLVED = "resolved"


def parse(text):
    """Return the parameter, scope and default of one net expression, or None."""
    if not isinstance(text, str):
        return None
    match = EXPRESSION.fullmatch(text.strip())
    return match.groupdict() if match else None


def texts(view):
    """Both captured expression-text maps of one observation view."""
    return (
        view.get(PARAMETER_PRIMARY) or [],
        view.get(PARAMETER_TEXTS) or {},
        view.get(SIGNAL_PRIMARY) or [],
        view.get(SIGNAL_TEXTS) or {},
    )


def view_resolved(view, terminal_nets=None, signal_names=None):
    """``(True, None)`` when every expression of one view resolves; else a reason."""
    for primary, text_field, names, known in (
        (PARAMETER_PRIMARY, PARAMETER_TEXTS, view.get(PARAMETER_PRIMARY) or [],
         terminal_nets),
        (SIGNAL_PRIMARY, SIGNAL_TEXTS, view.get(SIGNAL_PRIMARY) or [], signal_names),
    ):
        captured = view.get(text_field) or {}
        for name in names:
            parsed = parse(captured.get(name))
            if parsed is None:
                return False, f"{name} net expression was not captured or is unsupported"
            if known is None:
                continue
            declared = parsed["default"]
            if primary == SIGNAL_PRIMARY:
                if name != declared:
                    return False, f"signal {name} declares default {declared}"
            elif known.get(name) != declared:
                return False, f"{name} connects to {known.get(name)}, not {declared}"
    return True, None


def resolved(views, terminal_nets=None, signal_names=None, source_target=None):
    """Whether every view's expressions resolve against the captured source facts."""
    for view in views:
        source = view.get("source_view") == "source"
        if source_target is not None and source:
            if tuple(view.get("target") or ()) != tuple(source_target):
                raise TemplateError("source observation does not name the captured source view")
        ok, reason = view_resolved(
            view,
            terminal_nets if source else None,
            signal_names if source else None,
        )
        if not ok:
            return False, reason
    return True, None


def record_discharged(record, views):
    """Whether the record's own captured nets verify every observed expression.

    The normalized record addresses nets by ID, so the check maps each port to its
    source net name before comparing it with the declared expression default.
    """
    topology = record.get("topology") or {}
    names = {row.get("id"): row.get("source_name") for row in topology.get("nets", [])}
    terminals = {
        row.get("name"): names.get(row.get("net")) for row in topology.get("ports", [])
    }
    signals = {row.get("source_name") for row in topology.get("nets", [])}
    source = record.get("source") or {}
    target = (source.get("lib"), source.get("cell"), source.get("view"))
    return resolved(views, terminals, signals, target)[0]


def has_expressions(views):
    return any(
        view.get(PARAMETER_PRIMARY) or view.get(SIGNAL_PRIMARY) for view in views
    )
