"""Streaming DSPF parsing, indexing, and analysis APIs."""

from .analysis import NetSummary, ResistancePath
from .indexer import (
    IndexCancelled,
    IndexLocked,
    IndexProgress,
    IndexResult,
    build_index,
    default_cache_dir,
    index_path_for,
)
from .highlight import resistance_highlight_regions
from .integrity import ResistanceComponent, ResistanceIntegrity, ShortCandidate
from .network import (
    RcNetwork,
    RcNetworkCapacitor,
    RcNetworkNode,
    RcNetworkResistor,
)
from .repository import CapacitorSummary, DspfRepository, ResistorSummary
from .values import parse_spice_number

__all__ = [
    "DspfRepository",
    "CapacitorSummary",
    "ResistorSummary",
    "IndexCancelled",
    "IndexLocked",
    "IndexProgress",
    "IndexResult",
    "NetSummary",
    "RcNetwork",
    "RcNetworkCapacitor",
    "RcNetworkNode",
    "RcNetworkResistor",
    "resistance_highlight_regions",
    "ResistanceComponent",
    "ResistanceIntegrity",
    "ResistancePath",
    "ShortCandidate",
    "build_index",
    "default_cache_dir",
    "index_path_for",
    "parse_spice_number",
]
