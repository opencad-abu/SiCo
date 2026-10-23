"""Provider-specific Responses fields, maintained in one compatibility table."""

from __future__ import annotations

# A profile matches a model family without listing every deployment id.
PROFILE_MARKERS = (("doubao", "doubao"),)

UNSUPPORTED_FIELDS = {
    "doubao": frozenset({"reasoning.summary", "text.verbosity"}),
}


def profile(model):
    value = str(model or "").strip().lower()
    for marker, name in PROFILE_MARKERS:
        if marker in value:
            return name
    return None


def unsupported_fields(model):
    return UNSUPPORTED_FIELDS.get(profile(model), frozenset())


def codex_options(model):
    """Codex options that prevent unsupported fields at request construction."""
    options = {}
    if "reasoning.summary" in unsupported_fields(model):
        options["model_reasoning_summary"] = "none"
    return options


def strip(request, fields):
    """Remove configured request-envelope paths without reading item payloads."""
    for path in fields:
        _remove_path(request, path.split("."))
    return request


def _remove_path(value, parts):
    parents = []
    target = value
    for part in parts[:-1]:
        if not isinstance(target, dict) or part not in target:
            return
        parents.append((target, part))
        target = target[part]
    if isinstance(target, dict):
        target.pop(parts[-1], None)
    while parents:
        parent, key = parents.pop()
        if parent[key]:
            break
        parent.pop(key, None)
