"""Select the current authenticated Virtuoso entry for conversation continuation."""

from ..transport.host_discovery import candidates, connect_descriptor, descriptor_at


def current_host(owner, preferred=None):
    """Prefer this window's host, then the newest reachable host in this project."""
    source = owner.controllers.get(preferred)
    if source is not None:
        descriptor = dict(owner.dependencies_for(preferred).bridge_descriptor)
        if descriptor:
            try:
                return connect_descriptor(owner.project, descriptor)
            except (OSError, EOFError, ValueError, RuntimeError, KeyError, TypeError):
                pass
    for path in candidates(owner.project):
        try:
            return connect_descriptor(owner.project, descriptor_at(owner.project, path))
        except (OSError, EOFError, ValueError, RuntimeError, KeyError, TypeError):
            continue
    return None
