"""Published MTS imports must preserve type identity across request boundaries."""

from mtsnetlistor import config, defaults, model
from mtsnetlistor import defaults_effective, defaults_report, defaults_request, defaults_result
from mtsnetlistor import model_design, model_entries, model_options, model_request
from mtsnetlistor import request_data, request_input, request_output
from mtsnetlistor import workspace_input, workspace_model, workspace_output


def test_config_exports_preserve_workspace_and_io_identity():
    for owner, names in (
        (workspace_model, ('WorkspaceConfig', 'WorkspaceProcess', 'WorkspaceCellPresentation')),
        (workspace_input, ('load_workspace',)),
        (workspace_output, ('render_workspace_toml', 'save_workspace')),
        (request_input, ('load_request',)),
        (request_output, ('render_request_toml', 'save_request')),
        (request_data, ('request_to_dict', 'canonical_request_json', 'canonical_request_digest')),
    ):
        for name in names:
            assert getattr(config, name) is getattr(owner, name)


def test_model_exports_preserve_dataclass_identity():
    for owner, names in (
        (model_design, ('SourceDesign', 'TargetSelection')),
        (model_entries, ('ModelEntry', 'CornerProfile', 'CornerExport')),
        (model_options, ('ProcessOptions', 'SimulatorOption')),
        (model_request, ('CellNetlistSpec', 'NetlistRequest')),
    ):
        for name in names:
            assert getattr(model, name) is getattr(owner, name)


def test_defaults_exports_preserve_request_result_and_alias_identity():
    for owner, names in (
        (defaults_request, ('MaeSetup', 'DefaultsProbeRequest', 'PdkDefaultsRequest')),
        (defaults_result, ('DefaultsReport', 'PdkDefaultsResult', 'SourceDefaults')),
        (defaults_report, ('parse_defaults_report', 'read_defaults_report')),
        (defaults_effective, ('source_defaults_from_report',)),
    ):
        for name in names:
            assert getattr(defaults, name) is getattr(owner, name)
    assert defaults.PdkDefaultsRequest is defaults.DefaultsProbeRequest
    assert defaults.PdkDefaultsResult is defaults.DefaultsReport
    assert defaults.read_defaults_report is defaults.parse_defaults_report
