"""Legacy public defaults imports referencing explicit provider/report owners.

Remove after supported callers migrate to the corresponding defaults modules.
PdkDefaultsRequest/Result and read_defaults_report remain same-object aliases;
remove the old spellings once the workflow and external consumers migrate."""

from __future__ import annotations

from .defaults_values import (
    DEFAULTS_SCHEMA_VERSION,
    DEFAULTS_OUTPUT_ENV,
    DEFAULTS_PROJECT_ENV,
    DEFAULTS_RESULTS_ENV,
    DEFAULTS_PROVIDERS,
)
from .defaults_request import (
    MaeSetup,
    DefaultsProbeRequest,
    PdkDefaultsRequest,
)
from .defaults_probe import (
    render_defaults_probe_script,
)
from .defaults_snapshot import (
    normalize_model_files,
    normalize_snapshot,
)
from .defaults_result import (
    DefaultsReport,
    PdkDefaultsResult,
    SourceDefaults,
)
from .defaults_effective import (
    source_defaults_from_report,
    defaults_report_to_source_defaults,
)
from .defaults_report import (
    parse_defaults_report,
    read_defaults_report,
    defaults_report_to_json,
)


__all__ = ['DEFAULTS_SCHEMA_VERSION', 'DEFAULTS_OUTPUT_ENV', 'DEFAULTS_PROJECT_ENV', 'DEFAULTS_RESULTS_ENV', 'DEFAULTS_PROVIDERS', 'MaeSetup', 'render_defaults_probe_script', 'normalize_model_files', 'normalize_snapshot', 'DefaultsProbeRequest', 'PdkDefaultsRequest', 'DefaultsReport', 'PdkDefaultsResult', 'SourceDefaults', 'source_defaults_from_report', 'defaults_report_to_source_defaults', 'parse_defaults_report', 'read_defaults_report', 'defaults_report_to_json']
