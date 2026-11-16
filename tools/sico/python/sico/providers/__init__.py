"""Providers are injected; package import opens no network connections.

The Python (HTTP) provider stack has been retired. Only Codex providers remain;
see sico.providers.config for the Codex factory path.
"""

from __future__ import annotations

__all__: list[str] = []
