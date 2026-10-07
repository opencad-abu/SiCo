"""Installed declarative adaptation rule identities; no model-supplied execution.

The digest table is generated and reviewed at build time.  Runtime code must be
usable from the compiled distribution, where the development ``.py`` files are
not present, so it deliberately does not inspect its own source tree.
"""

from .template_schema import TemplateError

RULE_VERSION = "1"
# Build-time pinned identities for the reviewed v2 rule handlers.  Changing a
# handler requires a new version and a new digest; old template records remain
# immutable and therefore cannot silently acquire new behavior.
RULE_DIGESTS = {
    "rename": "71e97c6a30f35ba5b1109955bb0a7dee198220349688145c31e064b739dd64f7",
    "add_boundary_group": "61efc35ba7b2e26cfa8b123163199003e1dac8fa492d478ec51b2ab28af9eb08",
    "omit_optional_group": "5e18f7223f0b100c153723db0986b1b82ef03351dea4e205162133b6ad1e37f5",
}


def verify_installed_rule_digests():
    """Build-time/test-time guard for the source-free runtime digest table."""
    import hashlib
    import json
    from pathlib import Path

    root = Path(__file__).parent
    handlers = {
        "rename": ["template_embedding.py"],
        "add_boundary_group": ["template_embedding.py", "template_rule_boundary.py"],
        "omit_optional_group": ["template_embedding.py", "template_rule_omit.py"],
    }
    actual = {
        name: hashlib.sha256(
            json.dumps(
                [(root / filename).read_text() for filename in files],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        for name, files in handlers.items()
    }
    if actual != RULE_DIGESTS:
        raise TemplateError("installed adaptation rule digest table is stale")
    return True


def rule_ref(name):
    if name not in RULE_DIGESTS:
        raise TemplateError("needs_adaptation: adaptation rule is not installed")
    return {"id": name, "version": RULE_VERSION, "implementation_digest": RULE_DIGESTS[name]}


def authorize(name, requested, declared):
    reference = rule_ref(name)
    if reference not in requested or reference not in declared:
        raise TemplateError("needs_adaptation: exact installed rule must be allowed: " + name)
    return reference
