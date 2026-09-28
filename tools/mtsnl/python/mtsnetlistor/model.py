"""Legacy request-value imports; all objects are owned by the domain modules.

Remove this facade in the next incompatible API version after supported CLI,
GUI and worker callers migrate to model_design/entries/options/request."""

from __future__ import annotations

from .model_validation import (
    OA_NAME_RE,
    SIMULATORS,
    MTS_DESIGN_CIRCUIT_SECTION,
    validate_oa_name,
)
from .model_options import (
    HSPICED_UNQUALIFIED_PROCESS_OPTIONS,
    HSPICED_UNQUALIFIED_SIMULATOR_OPTIONS,
    SimulatorOption,
    ProcessOptions,
)
from .model_entries import (
    ModelEntry,
    CornerProfile,
    CornerExport,
)
from .model_design import (
    SourceDesign,
    TargetSelection,
)
from .model_request import (
    CellNetlistSpec,
    NetlistRequest,
)


__all__ = ['OA_NAME_RE', 'SIMULATORS', 'MTS_DESIGN_CIRCUIT_SECTION', 'HSPICED_UNQUALIFIED_PROCESS_OPTIONS', 'HSPICED_UNQUALIFIED_SIMULATOR_OPTIONS', 'validate_oa_name', 'ModelEntry', 'CornerProfile', 'CornerExport', 'SimulatorOption', 'ProcessOptions', 'SourceDesign', 'CellNetlistSpec', 'TargetSelection', 'NetlistRequest']
