"""Read-only Virtuoso inspection through the bounded inspection contract."""

from .inspection import build_read_only_skill
from .skill_result import call_skill, decode_skill_result


def dispatch_inspection(name, arguments, *, client):
    code = build_read_only_skill(name, arguments)
    if name == "inspect_config_binding":
        ok, detail = client.call(name, arguments)
        return decode_skill_result(detail, transport_ok=ok)
    return call_skill(client, code)
