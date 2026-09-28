"""Legacy request/workspace IO imports; the domain owners define every value.

Remove the facade and backend diagnostic alias after supported callers migrate
to workspace_model, request/workspace input/output and request_data."""

from __future__ import annotations

from .workspace_model import (
    WorkspaceCellPresentation,
    WorkspaceConfig,
    WorkspaceProcess,
)
from .request_input import (
    load_request,
)
from .workspace_input import (
    load_workspace,
)
from .request_data import (
    request_to_dict,
    canonical_request_json,
    canonical_request_digest,
)
from .request_output import (
    render_request_toml,
    save_request,
)
from .workspace_output import (
    render_workspace_toml,
    save_workspace,
)
from .toml_backend import _TOML_BACKEND as _TOML_BACKEND


__all__ = ['WorkspaceCellPresentation', 'WorkspaceConfig', 'WorkspaceProcess', 'load_request', 'load_workspace', 'request_to_dict', 'canonical_request_json', 'canonical_request_digest', 'render_request_toml', 'render_workspace_toml', 'save_request', 'save_workspace']
