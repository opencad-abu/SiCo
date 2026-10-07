"""Run-local Cadence mapping and connect-rule staging."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .errors import EnvironmentError
from .workspace import sha256_file


def stage_cds_lib(
    path: Path,
    *,
    source_root: Path,
    tool_environment: Mapping[str, str],
    approved_mapping: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a run-local mapping without changing source ``cds.lib``."""

    cdshome = str(tool_environment.get("CDSHOME", ""))
    if not cdshome:
        raise EnvironmentError("qualified IC environment has no CDSHOME")
    analog = (
        Path(cdshome) / "tools" / "dfII" / "etc" / "cdslib" / "artist" / "analogLib"
    )
    basic = Path(cdshome) / "tools" / "dfII" / "etc" / "cdslib" / "basic"
    definitions = (
        approved_mapping.get("definitions", {})
        if isinstance(approved_mapping, Mapping)
        else {}
    )

    def mapped(name: str, fallback: Path) -> Path:
        value = definitions.get(name) if isinstance(definitions, Mapping) else None
        if isinstance(value, Mapping) and value.get("resolved") and value.get("path"):
            return Path(str(value["path"]))
        return fallback

    mappings = {
        "analogLib": mapped("analogLib", analog),
        "basic": mapped("basic", basic),
        "gpdk045": mapped("gpdk045", source_root / "gpdk045"),
        "amsLDO": mapped("amsLDO", source_root / "amsLDO"),
    }
    for name, candidate in mappings.items():
        resolved = candidate.resolve(strict=False)
        if not resolved.is_dir() or candidate.is_symlink():
            raise EnvironmentError(f"staged cds.lib mapping is unavailable: {name}")
    path.write_text(
        "# AIVW immutable run-local mapping; source cds.lib is unchanged\n"
        + "".join(
            f"DEFINE {name} {candidate}\n" for name, candidate in mappings.items()
        ),
        encoding="utf-8",
    )
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "mappings": {name: str(candidate) for name, candidate in mappings.items()},
        "source_policy": "run_local_only",
    }


def stage_connect_rule(
    path: Path, *, tool_environment: Mapping[str, str], policy: Mapping[str, Any]
) -> dict[str, Any]:
    """Stage one approved Cadence built-in rule into the immutable payload."""

    amshome = str(tool_environment.get("AMSHOME", ""))
    relative = str(policy.get("source_relative_to_ams_home", ""))
    expected = str(policy.get("sha256", ""))
    source = (Path(amshome) / relative).resolve(strict=False)
    if (
        not amshome
        or not source.is_file()
        or source.is_symlink()
        or sha256_file(source) != expected
    ):
        raise EnvironmentError(
            "approved LDO connect-rule source is unavailable or hash-mismatched"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(source.read_bytes())
    if sha256_file(path) != expected:
        raise EnvironmentError("staged LDO connect-rule hash mismatch")
    return {
        "name": str(policy.get("name", "")),
        "source": str(source),
        "path": str(path),
        "sha256": expected,
        "size": path.stat().st_size,
        "policy": str(policy.get("policy", "")),
    }


__all__ = ["stage_cds_lib", "stage_connect_rule"]
