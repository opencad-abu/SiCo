"""Host-side launcher and validator for read-only site Python probes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Mapping, Optional

from .envelope import Envelope
from .errors import HubError
from .registry import default_registry

SITE_CONTRACT = "sico.site.capability.v1"
SITE_ACTION = "probe"
SITE_SCHEMA = "site.capability.v1"
PROBE_MARKER = "SICO_DATAHUB_PROBE "
_SECRET_MARKERS = (
    "API_KEY",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)


def default_probe_path() -> Path:
    return Path(__file__).resolve().parents[1] / "sico_datahub_site_probe.py"


def default_wire_path() -> Path:
    return Path(__file__).resolve().parents[1] / "sico_datahub_wire.py"


def safe_site_environment(source: Optional[Mapping[str, str]] = None) -> dict:
    """Keep the site tool environment while removing credential-bearing keys."""
    values = dict(os.environ if source is None else source)
    return {
        key: value for key, value in values.items()
        if not any(marker in key.upper() for marker in _SECRET_MARKERS)
    }


def _probe_line(output):
    matches = [
        line[len(PROBE_MARKER):]
        for line in output.splitlines()
        if line.startswith(PROBE_MARKER)
    ]
    return matches[-1] if matches else None


def probe_site(
    executable,
    *,
    wire_path=None,
    probe_path=None,
    load_mode="import",
    scope="site",
    timeout=15.0,
    isolate=True,
    env=None,
) -> Envelope:
    """Run one bounded site probe and return its validated response envelope."""
    executable = Path(executable)
    wire_path = Path(wire_path) if wire_path is not None else default_wire_path()
    probe_path = Path(probe_path) if probe_path is not None else default_probe_path()
    if not executable.is_file():
        raise HubError("capability_not_available", "Site Python executable is unavailable")
    if not wire_path.is_file() or not probe_path.is_file():
        raise HubError("internal", "DataHub site probe files are unavailable")
    attempts = [True, False] if isolate else [False]
    completed = None
    raw = None
    for isolated_attempt in attempts:
        command = [str(executable)]
        if isolated_attempt:
            command.append("-I")
        command.extend([
            str(probe_path),
            "--wire", str(wire_path),
            "--load-mode", load_mode,
            "--scope", scope,
        ])
        try:
            completed = subprocess.run(
                command,
                env=safe_site_environment(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HubError("timeout", "Site Python probe timed out") from exc
        except OSError as exc:
            raise HubError(
                "capability_not_available",
                "Site Python probe could not start",
            ) from exc
        raw = _probe_line(completed.stdout + "\n" + completed.stderr)
        if raw is not None:
            break
    if raw is None:
        raise HubError(
            "read_failed",
            f"Site Python probe returned no envelope (exit {completed.returncode})",
        )
    try:
        envelope = Envelope.from_dict(json.loads(raw))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HubError("invalid_arguments", "Site Python returned an invalid envelope") from exc
    contract, spec = default_registry().action(
        SITE_CONTRACT, envelope.version, envelope.action
    )
    if contract.name != envelope.contract or spec.response_schema != SITE_SCHEMA:
        raise HubError("unsupported_contract", "Site Python contract response is unknown")
    if envelope.status == "ok" and envelope.payload.get("schema") != SITE_SCHEMA:
        raise HubError("invalid_arguments", "Site Python capability schema mismatch")
    return envelope


def probe_capabilities(executable, **kwargs) -> dict:
    envelope = probe_site(executable, **kwargs)
    if envelope.status != "ok":
        error = envelope.error
        raise HubError(
            error.code if error is not None else "internal",
            error.message if error is not None else "Site Python probe failed",
            error.details if error is not None else None,
        )
    return dict(envelope.payload)


__all__ = [
    "PROBE_MARKER",
    "SITE_ACTION",
    "SITE_CONTRACT",
    "SITE_SCHEMA",
    "default_probe_path",
    "default_wire_path",
    "probe_capabilities",
    "probe_site",
    "safe_site_environment",
]
