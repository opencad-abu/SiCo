"""Read-only Virtuoso inspection through the bounded inspection contract."""

from cadai.shared.skill_result import call_skill
from cadai.workflows.schematic_read.inspection import build_read_only_skill


def dispatch_inspection(name, arguments, *, client):
    code = build_read_only_skill(name, arguments)
    return call_skill(client, code)
