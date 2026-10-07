"""Three template library tiers; local writes stay separate from shared references."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sicostate import project_directory

from .env_names import CIRCUIT_TEMPLATES_ENV, CIRCUIT_TEMPLATES_LEGACY, resolved_name
from .optional_resources import resource_path

PROJECT_ENV = CIRCUIT_TEMPLATES_ENV
LEGACY_ENVS = CIRCUIT_TEMPLATES_LEGACY
TIER_ORDER = {"private": 0, "project": 1, "builtin": 2}


def normalize_root(value, variable=PROJECT_ENV, *, workspace=None):
    return resource_path(value, variable, workspace=workspace)


def builtin_root():
    return Path(__file__).resolve().parents[2] / "reference/circuit_templates"


def template_root(workspace=None, *, create=False):
    """Writable private library, anchored to launch workspace, never the MCP package cwd."""
    return project_directory(
        Path.cwd() if workspace is None else workspace, "ai/circuit_templates", create=create
    )


@dataclass(frozen=True)
class TemplateLocation:
    tier: str
    path: Path
    legacy: bool = False

    def describe(self):
        return {"tier": self.tier, "root": str(self.path), "legacy": self.legacy}


def template_locations(workspace=None, environment=None):
    env = os.environ if environment is None else environment
    private = template_root(workspace)
    locations = [TemplateLocation("private", private)]
    legacy_private = private.parent / "reference/circuit-templates"
    # Compatibility source in the private tier, never a second write destination.
    if legacy_private.exists():
        locations.append(TemplateLocation("private", legacy_private.resolve(), True))
    variable = resolved_name(env, PROJECT_ENV, LEGACY_ENVS)
    configured = str(env.get(variable, "")).strip()
    if configured:
        locations.append(
            TemplateLocation(
                "project", normalize_root(configured, variable, workspace=workspace),
                variable in LEGACY_ENVS
            )
        )
    locations.append(TemplateLocation("builtin", builtin_root().resolve()))
    return locations
