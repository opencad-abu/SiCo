"""Compatibility facade for structural connectivity owners.

New code should import the model, lexical, Verilog, SI, or comparison owner
that matches its responsibility. This facade remains for the public API and
legacy consumers. Remove it in the next incompatible API release after all
supported consumers migrate to the corresponding owners. Cadence SI remains
the structural authority; no implementation here invents or repairs hierarchy.
"""

from .connectivity_model import (
    ConnectivityParseError,
    Finding,
    Instance,
    Module,
    Port,
    Structure,
)
from .connectivity_lex import split_tokens, unescape_identifier
from .connectivity_verilog import parse_verilog
from .connectivity_si import (
    parse_globalmap,
    parse_globalmap_models,
    parse_inherited_connections,
    parse_si_map,
    parse_si_netlists,
)
from .connectivity_compare import compare_structures
from .connectivity_normalize import canonicalize_module

__all__ = [
    "ConnectivityParseError",
    "Finding",
    "Instance",
    "Module",
    "Port",
    "Structure",
    "canonicalize_module",
    "compare_structures",
    "parse_globalmap",
    "parse_globalmap_models",
    "parse_inherited_connections",
    "parse_si_map",
    "parse_si_netlists",
    "parse_verilog",
    "split_tokens",
    "unescape_identifier",
]
