"""Versioned, canonical design context used by AIVW adapters."""

from .schema import (
    DesignIR,
    DesignIRValidationError,
    build_design_ir,
)
from .normalize import normalize_design_ir
from .fingerprint import design_ir_digest
from .diff import diff_design_ir
from .assembler import (
    DesignIRAssemblyError,
    assemble_design_ir_from_snapshot,
    build_snapshot_context,
)
from .config_binding import (
    ConfigBinding,
    ConfigBindingValidationError,
    bind_config_to_target,
    config_binding_artifact_locator,
    load_config_binding_artifact,
    validate_config_binding,
)
from .official_ams import (
    OfficialAMSBindingError,
    build_config_binding_from_official_structure,
)

__all__ = [
    "DesignIR",
    "DesignIRValidationError",
    "build_design_ir",
    "normalize_design_ir",
    "design_ir_digest",
    "diff_design_ir",
    "DesignIRAssemblyError",
    "assemble_design_ir_from_snapshot",
    "build_snapshot_context",
    "ConfigBinding",
    "ConfigBindingValidationError",
    "bind_config_to_target",
    "config_binding_artifact_locator",
    "load_config_binding_artifact",
    "validate_config_binding",
    "OfficialAMSBindingError",
    "build_config_binding_from_official_structure",
]
