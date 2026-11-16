"""A confirmed symbol grid must contain every confirmed anchor, in decimal."""

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.geometry import validate
from cadai.pdk_standard.symbol_grid import lattice, off_grid


def symbol(grid=None, xy=(0.25, 0.1875)):
    return {
        'source': 'capture',
        'depends_on': ['cell0_symbol'],
        'coordinate_system': 'master_local',
        'unit': 'schematic_uu',
        'bbox': [[0, -0.1875], [0.25, 0.1875]],
        'grid': grid if grid is not None else {'state': 'unknown', 'reason': 'not captured'},
        'terminals': {'A': {'direction': 'input',
                            'anchors': [{'id': 'pin0', 'xy': list(xy), 'escape': 'right'}]}},
        'parameterized': False,
    }


def test_lattice_reports_the_decimal_step_of_observed_coordinates():
    assert lattice([[0.0, 0.0], [0.25, 0.1875], [0.0625, 0.0625]]) == [0.0625, 0.0625]
    assert lattice([[0.375, 0.0], [0.0, -0.375]]) == [0.375, 0.375]
    assert lattice([[0.5, 0.5]]) is None


def test_off_grid_uses_decimal_semantics_for_anchor_coordinates():
    grid = {'step': [0.0625, 0.0625], 'origin': [0.0, 0.0]}
    assert off_grid(symbol(grid), grid) == []
    assert off_grid(symbol(grid, xy=(0.26, 0.1875)), grid) == [
        {'terminal': 'A', 'id': 'pin0', 'xy': [0.26, 0.1875]}]


def test_confirmed_grid_must_contain_every_confirmed_anchor():
    validate(symbol({'step': [0.0625, 0.0625], 'origin': [0.0, 0.0]}))
    with pytest.raises(PdkUnavailable, match='off the declared symbol grid'):
        validate(symbol({'step': [0.5, 0.5], 'origin': [0.0, 0.0]}))


def test_pending_anchors_do_not_block_the_grid_check():
    value = symbol({'step': [0.0625, 0.0625], 'origin': [0.0, 0.0]})
    value['terminals']['A']['anchors'] = {'state': 'unknown', 'reason': 'review required'}
    validate(value)
