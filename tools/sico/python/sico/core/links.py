"""Session-bound object links shared by services and presentation."""

from urllib.parse import urlsplit

from .contracts import identifier

# Links use the product name. The legacy scheme is the old product name
# (Copilot Studio), recorded in journals and in tool results the model saw,
# so it keeps resolving.
LINK_SCHEME = "copilot"
LINK_SCHEMES = frozenset({LINK_SCHEME, "studio"})


def object_link(kind, session, object_id):
    if kind not in {"data", "report", "audit"}:
        raise ValueError("Unsupported workbench object")
    return f"{LINK_SCHEME}://{kind}/{identifier(session)}/{identifier(object_id)}"


def parse_link(value, session):
    url = urlsplit(value)
    parts = url.path.split("/")
    if (
        url.scheme not in LINK_SCHEMES
        or url.netloc not in {"data", "report", "audit"}
        or url.query
        or url.fragment
        or len(parts) != 3
        or parts[1] != session
    ):
        raise ValueError("Link does not belong to this session")
    return url.netloc, identifier(parts[2])
