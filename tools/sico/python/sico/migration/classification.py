"""Versioned, path-specific ownership rules for legacy project state."""

import re
from pathlib import PurePosixPath

from .native_paths import disposition as native_disposition


CONTAINERS = {
    "ai", "ai/agent", "ai/agent/service", "ai/agent/sessions",
    "ai/agent/attachments", "ai/agent/background", "ai/agent/background/records",
    "ai/agent/background/services", "ai/circuit_operations", "ai/measurement-results",
    "rce", "ai/reference", "ai/agent/bridges",
}
PRESERVE = {"worktrees", ".sico-evidence", "build-evidence", "acceptance-evidence"}
CACHES = {"cache", "lsf", "rce/dspf"}
RUNTIME = {"runtime", "ai/agent/runtime", "ai/agent/background/workers"}
REVIEW = {
    "ai/terminal-logs": "terminal_history_adapter_required",
}
ID = r"[a-zA-Z0-9_-]{1,96}"
HEX = r"[a-f0-9]{32}"
TEMPLATES = r"ai/(?:circuit_templates|reference/circuit-templates)"
TEMPLATE = r"tpl_[a-f0-9]{64}"
BRIDGE = r"ai/agent/bridges/[a-f0-9]{64}"


def router_disposition(relative, directory):
    if directory:
        if re.fullmatch(BRIDGE + r"\.state(?:/archive)?", relative):
            return "container", "router_directory"
        return None
    patterns = (
        (r"\.json", "identity", "router_descriptor"),
        (r"(?:\.lock|\.state/(?:writer|maintenance)\.lock)", "lock", "router_lock"),
        (r"(?:\.retired\.json|\.(?:bindings|router|relay|skill)\.jsonl|"
         r"\.state/requests\.jsonl|\.state/archive/(?:manifest\.json|"
         r"(?:requests|bindings|router|relay|skill)\.jsonl\.gz))", "retain", "router_evidence"),
    )
    for suffix, action, schema in patterns:
        if re.fullmatch(BRIDGE + suffix, relative):
            return action, schema
    return None


def template_disposition(relative, directory):
    if directory:
        if re.fullmatch(TEMPLATES + r"(?:/(?:catalogs|captures|previews))?", relative):
            return "container", "template_directory"
        if re.fullmatch(TEMPLATES + "/previews/" + TEMPLATE + "-preview4", relative):
            return "container", "template_preview_directory"
    else:
        patterns = (
            (r"/(?:catalog\.sqlite3|catalogs/" + TEMPLATE + r"\.sqlite3)", "template_catalog"),
            (r"/captures/[a-f0-9]{64}\.jsonl", "template_capture"),
            ("/previews/" + TEMPLATE + r"-preview4/(?:index\.html|symbol-plan\.json|"
             r"(?:schematic|symbol|layout|symbol-proposed)\.svg)", "template_preview"),
        )
        for suffix, schema in patterns:
            if re.fullmatch(TEMPLATES + suffix, relative):
                return "retain", schema
    return None


def classify(relative, directory):
    """Return disposition and schema; containers alone permit recursive traversal."""
    path = PurePosixPath(relative)
    native = native_disposition(relative, directory)
    if native is not None:
        return native
    router = router_disposition(relative, directory)
    if router is not None:
        return router
    template = template_disposition(relative, directory)
    if template is not None:
        return template
    if directory:
        if relative in PRESERVE:
            return "preserve", "private_development_state"
        if relative in CACHES:
            return "rebuild", "cache"
        if relative in RUNTIME or re.fullmatch(r"\.cad-ai-runtime-\d+|ai/cad-ai-[\w-]+", relative):
            return "runtime", "runtime_container"
        if relative in REVIEW:
            return "review", REVIEW[relative]
        if (relative in CONTAINERS or re.fullmatch(
                r"ai/agent/(sessions|attachments)/" + ID, relative)
                or re.fullmatch(r"ai/agent/sessions/" + ID + "/inbox", relative)):
            return "container", "directory"
        return "unknown", "unregistered_directory"
    fixed = {
        "ai/agent-provider.json": ("retain", "provider"),
        "ai/agent-resources.json": ("retain", "resources"),
        "ai/agent/service/project.json": ("identity", "project"),
        "ai/agent/service/discovery.json": ("runtime", "discovery"),
        "ai/agent/service/owner.lock": ("lock", "project_lock"),
        "ai/circuit_operations/.journal.lock": ("lock", "operations_lock"),
        "ai/agent/workspace.ini": ("retain", "workspace_settings"),
    }
    if relative in fixed:
        return fixed[relative]
    patterns = (
        (r"ai/agent/sessions/" + ID + r"/session\.snapshot\.json", "retain", "snapshot"),
        (r"ai/agent/sessions/" + ID + r"/end-release\.json", "retain", "session_release"),
        (r"ai/agent/sessions/" + ID + r"/events\.jsonl", "retain", "journal"),
        (r"ai/agent/sessions/" + ID + r"/events\.idx", "rebuild", "journal_index"),
        (r"ai/agent/sessions/" + ID + r"/writer\.lock", "lock", "journal_lock"),
        (r"ai/agent/sessions/" + ID + r"/inbox/" + ID + r"\.json", "retain", "inbox"),
        (r"ai/agent/attachments/" + ID + "/" + HEX +
         r"\.(json|txt|png|jpg|gif|webp|wav|bin)", "retain", "attachment"),
        (r"ai/agent/background/services/" + HEX, "lock", "background_lease"),
        (r"ai/agent/background/records/" + ID + r"\.json", "retain", "background"),
        (r"ai/circuit_operations/[a-f0-9]{64}\.json", "retain", "operation"),
        (r"ai/measurement-results/[a-z_]+_[a-f0-9]{64}\.json", "retain", "measurement"),
    )
    for pattern, disposition, schema in patterns:
        if re.fullmatch(pattern, relative):
            return disposition, schema
    # Unknown locks still participate in writer detection, but never imply ownership.
    if path.suffix == ".lock" or path.name == ".journal.lock":
        return "lock", "unregistered_lock"
    return "unknown", "unregistered_file"
