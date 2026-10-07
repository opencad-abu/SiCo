"""One collision-repair entry point for verified target template graphs."""

from .template_route_chains import chain_paths
from .template_route_components import component_paths
from .template_route_detour import detour_paths
from .template_route_tracks import track_paths


def repair_paths(edges, members, layout, placed, pins, reserved, diagnostics):
    """Prefer fixed vertices, free tracks, fixed junctions, then conflict scopes."""
    return (detour_paths(edges, members, layout, placed, pins, reserved, diagnostics)
            or track_paths(edges, members, layout, placed, pins, reserved, diagnostics)
            or chain_paths(edges, members, layout, placed, pins, reserved, diagnostics)
            or component_paths(edges, members, layout, placed, pins, reserved, diagnostics))
