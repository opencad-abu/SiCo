"""Compatibility exports for manifest.

New code uses the named owner modules. Remove this facade after all external
callers migrate; exports are the original objects, without alternate state.
"""

from .manifest_records import ResolvedArtifact as ResolvedArtifact, ResolvedArtifactLocator as ResolvedArtifactLocator
from .manifest_artifacts import artifact_index as artifact_index
from .manifest_locators import artifact_locator_index as artifact_locator_index
from .manifest_publication import publish_split_manifests as publish_split_manifests
from .manifest_verification import verify_split_manifests as verify_split_manifests
from .manifest_resolution import resolve_indexed_artifact as resolve_indexed_artifact, resolve_indexed_locator as resolve_indexed_locator

__all__ = ['ResolvedArtifact', 'ResolvedArtifactLocator', 'artifact_index', 'artifact_locator_index', 'publish_split_manifests', 'verify_split_manifests', 'resolve_indexed_artifact', 'resolve_indexed_locator']
