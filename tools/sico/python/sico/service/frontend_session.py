"""Identity-only session tokens and immutable frontend configuration."""

from dataclasses import dataclass
from typing import Optional

from ..core.contracts import BoundContext
from .published import freeze


@dataclass(frozen=True)
class SessionToken:
    session_id: str
    # None denotes closed history on the wire; SessionTokens rejects it for live commands.
    runtime_id: Optional[str]


@dataclass(frozen=True)
class SessionView:
    session_id: str
    runtime_id: str
    context: BoundContext
    label: str
    base: str
    model: str
    resources: bool


@dataclass(frozen=True)
class FrontendSession:
    api: object
    session: SessionToken


def publish_session(controller):
    """Called by backend construction/publication, never by a Qt query."""
    context = controller.current
    published = BoundContext(context.instance_id, context.generation, context.target_id,
                             context.snapshot)
    object.__setattr__(published, "snapshot", freeze(published.snapshot))
    controller.frontend_view = SessionView(
        controller.session_id, controller.runtime_id, published,
        controller.label, controller.base, controller.model,
        hasattr(controller.loop, "resources"),
    )
