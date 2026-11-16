"""Confirmed edit permission does not imply qualified CDF constraints."""

from copy import deepcopy

from cadai.pdk_standard.query import (
    Queries,
    cdf_missing,
    content_rows,
    package_summary,
    symbol_missing,
)
from pdk_standard_fixture import package


def test_allowed_cdf_with_unknown_execution_is_incomplete(tmp_path):
    current, _, _, _ = package(tmp_path)
    name = 'cell0/cdf.json'
    cdf = current.document(name)
    cdf['parameters']['w'].update(write='allow', requirement='explicit', unit='m')
    original = current.document
    current.document = lambda target: deepcopy(cdf) if target == name else original(target)
    rows, status = content_rows(current, original('device.json')['items'],
                                {'section': 'cdf', 'device': 'cell0'})
    assert rows and status == 'incomplete'
    assert ('/execution', 'confirmation_required') in list(cdf_missing(cdf))
    cdf['execution'] = {'mode': 'cdf', 'order': ['w']}
    assert list(cdf_missing(cdf)) == [('/parameters/w/domain', 'missing_required')]
    cdf['parameters']['w']['domain'] = {'kind': 'interval', 'min': 0.1, 'max': 1.0}
    assert list(cdf_missing(cdf)) == []
    assert content_rows(current, original('device.json')['items'],
                        {'section': 'cdf', 'device': 'cell0'})[1] == 'ok'


def test_symbol_detail_reports_unconfirmed_geometry_as_incomplete(tmp_path):
    current, _, _, _ = package(tmp_path)
    rows, status = content_rows(current, current.document('device.json')['items'],
                                {'section': 'symbol', 'device': 'cell0'})
    assert rows and status == 'incomplete'
    assert ('/bbox', 'missing_required') in list(symbol_missing(current.document('cell0/symbol.json')))


def test_cdf_query_exposes_the_confirmed_write_and_execution_order(tmp_path):
    """门槛要求的可写集合与执行顺序必须能从设计侧读到，否则绑定只能靠猜。"""
    current, _, _, _ = package(tmp_path)
    name = 'cell0/cdf.json'
    cdf = current.document(name)
    cdf['parameters']['w'].update(write='allow', requirement='explicit', unit='m',
                                  domain={'kind': 'interval', 'min': 0.1, 'max': 1.0})
    cdf['execution'] = {'mode': 'cdf', 'order': ['w'],
                        'implementation_ref': 'builtin:cdf-callbacks:v1'}
    original = current.document
    current.document = lambda target: deepcopy(cdf) if target == name else original(target)
    rows, status = content_rows(current, original('device.json')['items'],
                                {'section': 'cdf', 'device': 'cell0'})
    assert status == 'ok'
    metadata = next(row for row in rows if 'metadata' in row)
    assert metadata['metadata']['execution']['order'] == ['w']
    writable = [row for row in rows if row.get('section') == 'parameters']
    assert [row['id'] for row in writable] == ['w']
    assert writable[0]['write'] == 'allow'


def test_symbol_review_candidates_stay_out_of_standard_projection(tmp_path):
    from cadai.pdk_standard.geometry import review_candidates, project

    current, bridge, observed, _ = package(tmp_path)
    value = deepcopy(observed)
    value['geometry'] = {
        'items': [{
            'terminal': 'A', 'pin_index': 0, 'figure_index': 0,
            'bbox': [[0.0, -0.05], [0.1, 0.05]], 'shape_type': 'rect',
            'anchors': {'candidates': [{'point': [0.1, 0.0], 'method': 'figure_bbox_center'}]},
        }],
        'selection_bbox': None,
        'selection_figures': [{'bBox': {'status': 'known', 'type': 'list', 'value': [
            {'status': 'known', 'type': 'list', 'value': [
                {'status': 'known', 'type': 'float', 'value': 0.0},
                {'status': 'known', 'type': 'float', 'value': -0.1}]},
            {'status': 'known', 'type': 'list', 'value': [
                {'status': 'known', 'type': 'float', 'value': 0.1},
                {'status': 'known', 'type': 'float', 'value': 0.1}]},
        ]}}],
        'parameterized_master': False,
    }
    candidate = review_candidates(value)
    assert candidate['selection_bbox']['value'] == [[0.0, -0.1], [0.1, 0.1]]
    assert candidate['bbox']['value'] is None
    assert candidate['anchors'][0]['escape_candidate'] is None
    assert candidate['bbox']['requires_confirmation'] is True
    assert project(value, 'cell0_symbol')['bbox']['state'] == 'unknown'


def test_blocker_query_separates_publication_from_full_library(tmp_path):
    current, _, _, _ = package(tmp_path)
    response = Queries().read(
        current, {"library": "synthetic", "section": "blockers"})
    report = response["items"][0]
    assert response["design_ready"] is False
    assert response["next_action"] == "confirm_device_usage"
    assert report["publication_blockers"][0]["code"] == "no_usable_device"
    assert report["next_action"] == "confirm_device_usage"


def test_model_section_exposes_the_card_inventory():
    class Package:
        def available(self, name):
            return name == "model.json"

        def document(self, name):
            return {
                "source": "capture", "depends_on": [],
                "configs": {"spectre.tt": {"simulator": "spectre"}},
                "corners": {"spectre.tt": {"name": "TT", "configs": ["spectre.tt"]}},
                "cards": {"card_x": {
                    "name": "n18", "kind": "model", "simulator": "spectre",
                    "file_ref": "file_model", "lines": [39],
                    "configs": ["spectre.tt"], "sections": ["tt"],
                    "element_type": "bsim4"}},
                "evidence": {},
            }

    rows, status = content_rows(Package(), {}, {"section": "model"})
    assert status == "ok"
    card = next(row for row in rows if row.get("section") == "cards")
    assert card["id"] == "card_x" and card["name"] == "n18"
    assert card["configs"] == ["spectre.tt"] and card["sections"] == ["tt"]


def test_package_summary_does_not_embed_acceptance_receipts():
    manifest = {
        "format": "sico.pdk.package", "schema_version": "1.1.0",
        "package_id": "synthetic", "pdk_version": "v1", "options": {},
        "libraries": {"synthetic": {"name": "synthetic"}},
        "dependencies": {}, "files": {}, "revision": "r", "created_at": "t",
        "publication": {
            "profile": "circuit-simulation-v1", "at": "t",
            "content_digest": "sha256:" + "a" * 64, "based_on": "sha256:" + "b" * 64,
            "scope": {"covers": "data", "libraries": ["synthetic"], "devices": 2,
                      "simulators": ["spectre"], "corners": ["spectre.tt"],
                      "unfinished": []},
            "acceptance": {
                "verifier": "sico-device-acceptance-v2",
                "operations": ["creation", "parameter_roundtrip", "wiring", "netlist",
                               "simulation_config"],
                "evidence_ref": "/private/evidence.json",
                "evidence_digest": "sha256:" + "c" * 64,
                "receipts": {"run": {"devices": ["cell0", "cell1"],
                                     "fingerprints": {"cell0": {"x": "y" * 10000}}}},
            },
        },
    }
    summary = package_summary(manifest)
    assert summary["publication"]["acceptance"]["receipts"] == 1
    assert summary["publication"]["acceptance"]["devices"] == 2
    assert "fingerprints" not in summary["publication"]["acceptance"]


def test_capture_evidence_retains_geometry_review_candidates(tmp_path):
    from cadai.pdk_data import PdkData

    current, _, observed, _ = package(tmp_path)
    data = PdkData(tmp_path, {})
    ref = data.observation(observed)
    from cadai.pdk_data import read_json
    record = read_json(data.root.parent / 'pdk-evidence' / (ref + '.json'))
    assert record['digest'] == ref
    assert 'geometry_review' in record
    assert record['geometry_review']['warning'].startswith('Candidates do not authorize')


def test_symbol_signature_covers_body_observations(tmp_path):
    from cadai.pdk_standard.geometry import signature

    current, _, observed, _ = package(tmp_path)
    first = signature(observed)
    changed = deepcopy(observed)
    changed['geometry']['selection_figures'] = [{'bBox': [[0, 0], [1, 1]]}]
    assert signature(changed) != first
