"""Collected symbol facts and validation of confirmed connection geometry."""

from ..pdk_normalize import raw_value
from .constraints import numeric, unknown
from .jsonio import fail, fingerprint


def pending(reason):
    return {'state': 'unknown', 'reason': reason}


METHOD = "sico-symbol-geometry-json-v3"


def signature(value, method=METHOD):
    geometry = value['geometry']
    ports = [{k: p.get(k) for k in ('name', 'direction', 'width', 'pin_count', 'net_expression')}
             for p in value['ports']['items']]
    if method == 'sico-symbol-interface-json-v1':
        # Legacy packages retain their declared algorithm until explicitly refreshed.
        pins = [{k: f.get(k) for k in ('terminal', 'pin_index', 'figure_index', 'raw')}
                for f in geometry['items']]
        return fingerprint({'bbox': geometry.get('bbox'), 'pins': pins,
                            'parameterized': geometry.get('parameterized_master'), 'ports': ports})
    if method == 'sico-symbol-interface-json-v2':
        # Compatibility only: v2 included context-derived anchor references.
        pins = [{k: f.get(k) for k in ('terminal', 'pin_index', 'figure_index', 'raw', 'bbox',
                                       'shape_type', 'anchors')} for f in geometry['items']]
        observed = {k: geometry.get(k) for k in (
            'bbox', 'dbu_per_user_unit', 'selection_bbox', 'selection_bbox_status',
            'selection_figures', 'parameterized_master', 'hierarchical_instance_count', 'body_outline_status')}
        # v2 collectors never captured the body. A new capture adds it without changing v2 facts.
        observed['body_outline_status'] = 'not_requested'
        return fingerprint({'observed': observed, 'pins': pins,
                            'parameterized': geometry.get('parameterized_master'), 'ports': ports})
    if method != METHOD:
        fail('Unsupported symbol fingerprint method: ' + method, 'pdk_source_changed')
    # Raw coordinates and shapes only: no library paths, IDs or candidate metadata.
    pins = [{k: f.get(k) for k in ('terminal', 'pin_index', 'figure_index', 'raw', 'pin_raw')}
            for f in geometry['items']]
    observed = {k: geometry.get(k) for k in (
        'bbox', 'dbu_per_user_unit', 'selection_bbox', 'selection_figures', 'parameterized_master',
        'hierarchical_instance_count', 'mosaic_count', 'body_outline_status', 'body_shapes')}
    return fingerprint({'observed': observed, 'pins': pins, 'ports': ports})


def _box(value):
    if not isinstance(value, list) or len(value) != 2:
        return None
    if any(not isinstance(point, list) or len(point) != 2 for point in value):
        return None
    if any(not numeric(number) for point in value for number in point):
        return None
    return value if value[0][0] <= value[1][0] and value[0][1] <= value[1][1] else None


def _union(boxes):
    boxes = [box for box in boxes if box]
    if not boxes:
        return None
    return [[min(box[0][0] for box in boxes), min(box[0][1] for box in boxes)],
            [max(box[1][0] for box in boxes), max(box[1][1] for box in boxes)]]


def review_candidates(value):
    """Private review uses the same proof as publication; selection boxes are labelled separately."""
    from .symbol_facts import derive
    geometry = value['geometry']
    selection = _box(raw_value(geometry.get('selection_bbox')))
    if selection is None:
        selection = _union([_box(raw_value(row.get('bBox'))) for row in
                            geometry.get('selection_figures', [])])
    facts = derive(value)
    body = None if unknown(facts['bbox']) else facts['bbox']
    anchors = []
    for figure in geometry.get('items', []):
        for candidate in (figure.get('anchors') or {}).get('candidates') or []:
            point = candidate.get('point')
            if not isinstance(point, list) or len(point) != 2 or not all(numeric(v) for v in point):
                continue
            proven = facts['terminals'].get(figure.get('terminal'), {})
            match = next((a for a in proven if a['xy'] == point), None) if isinstance(proven, list) else None
            anchors.append({'terminal': figure.get('terminal'),
                            'id': 'pin' + str(figure.get('pin_index', 0)), 'xy': point,
                            'escape_candidate': match['escape'] if match else None,
                            'basis': candidate.get('method')})
    return {'bbox': {'value': body, 'status': 'derived' if body else 'unavailable',
                     'requires_confirmation': body is None, 'basis': 'static_body_and_pin_centers'},
            'selection_bbox': {'value': selection, 'basis': 'selection_box_not_body_outline'},
            'anchors': anchors[:256], 'anchors_total': len(anchors), 'truncated': len(anchors) > 256,
            'grid': {'status': 'unavailable',
                     'reason': 'Symbol connection grid requires source or user confirmation'},
            'warning': 'Candidates do not authorize placement or wiring'}


def project(value, dependency):
    geometry = value['geometry']
    result = {'source': 'capture', 'depends_on': [dependency], 'coordinate_system': 'master_local',
            'unit': 'schematic_uu', 'bbox': pending('Body bounds excluding dynamic labels require confirmation'),
            'grid': pending('Symbol connection grid not captured'),
            'parameterized': geometry.get('parameterized_master') if type(geometry.get('parameterized_master')) is bool
                             else pending('Geometry parameterization not captured'),
            **({'geometry_parameters': pending('Effective geometry parameters not captured')}
               if geometry.get('parameterized_master') is True else {}),
            'terminals': {p['name']: {'direction': p.get('direction') or 'unknown',
                                    'anchors': pending('Electrical anchors and escape directions require confirmation')}
                          for p in value['ports']['items']}}

    if geometry.get('body_outline_status') == 'complete':
        from .symbol_facts import METHOD as FACT_METHOD, derive
        facts = derive(value)
        result['bbox'] = facts['bbox']
        result['evidence'] = {'/bbox': 'geometry_derivation', '/x_geometry_collection': 'geometry_derivation'}
        for name, anchors in facts['terminals'].items():
            result['terminals'][name]['anchors'] = anchors
            from .patches import pointer
            result['evidence'][pointer('terminals', name, 'anchors')] = 'geometry_derivation'
        result['x_geometry_collection'] = FACT_METHOD
    return result


def point(value):
    if not isinstance(value, list) or len(value) != 2 or not all(numeric(v) for v in value):
        fail('Geometry point requires two finite numbers')


def box(value):
    if not isinstance(value, list) or len(value) != 2:
        fail('Invalid symbol bbox')
    for p in value:
        point(p)
    if any(value[0][k] >= value[1][k] for k in (0, 1)):
        fail('Symbol bbox requires positive dimensions')


def validate(value):
    from .validate import envelope, fields
    fields(value, ('source', 'depends_on', 'coordinate_system', 'unit', 'bbox', 'grid', 'terminals', 'parameterized'),
           ('evidence', 'geometry_parameters', 'label_boxes'))
    envelope(value)
    if value['coordinate_system'] != 'master_local' or value['unit'] != 'schematic_uu':
        fail('Unsupported symbol coordinates')
    if not unknown(value['bbox']):
        box(value['bbox'])
    if not unknown(value['grid']):
        fields(value['grid'], ('step', 'origin'))
        point(value['grid']['step'])
        point(value['grid']['origin'])
        if any(v <= 0 for v in value['grid']['step']):
            fail('Symbol grid must be positive')
    if not unknown(value['parameterized']) and type(value['parameterized']) is not bool:
        fail('Invalid parameterized symbol flag')
    if value['parameterized'] is True and not isinstance(value.get('geometry_parameters'), dict):
        fail('Parameterized geometry needs its effective parameters')
    for terminal, row in value['terminals'].items():
        fields(row, ('direction', 'anchors'))
        if not terminal or row['direction'] not in {'input', 'output', 'inputOutput', 'passive', 'unknown'}:
            fail('Invalid symbol terminal')
        if unknown(row['anchors']):
            continue
        if not isinstance(row['anchors'], list) or not row['anchors']:
            fail('Terminal needs anchors')
        seen = set()
        for anchor in row['anchors']:
            fields(anchor, ('id', 'xy', 'escape'))
            if not isinstance(anchor['id'], str) or not anchor['id'] or anchor['id'] in seen:
                fail('Invalid/duplicate anchor ID')
            seen.add(anchor['id'])
            point(anchor['xy'])
            if anchor['escape'] not in {'left', 'right', 'up', 'down'}:
                fail('Invalid anchor escape direction')
            if not unknown(value['bbox']) and any(not value['bbox'][0][k] <= anchor['xy'][k] <= value['bbox'][1][k]
                                                 for k in (0, 1)):
                fail('Anchor outside symbol bbox')
    for bounds in value.get('label_boxes', []):
        box(bounds)
