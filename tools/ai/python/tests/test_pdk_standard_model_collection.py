"""Model configuration is derived from the vendor decks that are already indexed."""

from pathlib import Path

from cadai.pdk_standard import validate
from cadai.pdk_standard.model_collection import derive, ensure
from cadai.pdk_standard.model_decks import resolve_detail
from cadai.pdk_standard.simulation_collection import entries


def decks(root: Path):
    for name in ('hspice', 'spectre'):
        (root / 'models' / name).mkdir(parents=True)
    (root / 'models/hspice/lib.lib').write_text(
        ".lib tt\n.inc 'cell.mdl'\n.endl tt\n"
        ".lib dio_tt\n.inc 'dio.mdl'\n.endl dio_tt\n")
    (root / 'models/hspice/cell.mdl').write_text(
        '.model cell0 nmos\n.subckt cell0_ckt d g s b\n.ends\n')
    (root / 'models/hspice/dio.mdl').write_text('.model dio0 d\n')
    (root / 'models/spectre/lib.lib').write_text(
        'section tt\ninclude "cell.mdl"\nendsection tt\n'
        'section dio_tt\ninclude "dio.mdl"\nendsection dio_tt\n')
    (root / 'models/spectre/cell.mdl').write_text(
        'model cell0 bsim4 type = n\nsubckt cell0_ckt (d g s b)\nends\n')
    (root / 'models/spectre/dio.mdl').write_text('model dio0 diode\n')


def rows():
    return {
        'file_hs_lib': {'kind': 'model', 'root': 'pdk', 'path': 'models/hspice/lib.lib'},
        'file_hs_cell': {'kind': 'model', 'root': 'pdk', 'path': 'models/hspice/cell.mdl'},
        'file_hs_dio': {'kind': 'model', 'root': 'pdk', 'path': 'models/hspice/dio.mdl'},
        'file_spe_lib': {'kind': 'model', 'root': 'pdk', 'path': 'models/spectre/lib.lib'},
        'file_spe_cell': {'kind': 'model', 'root': 'pdk', 'path': 'models/spectre/cell.mdl'},
        'file_spe_dio': {'kind': 'model', 'root': 'pdk', 'path': 'models/spectre/dio.mdl'},
    }


def test_derive_reads_sections_and_binds_every_model_file(tmp_path):
    decks(tmp_path)
    model, files, dependencies, issues = derive(rows(), {'pdk': str(tmp_path)}, 'derived')
    assert issues == [] and model is not None
    validate.document('model.json', model)
    assert model['corners']['hspiceD.tt']['configs'] == ['hspiceD.dio_tt', 'hspiceD.tt']
    assert model['corners']['spectre.tt']['configs'] == ['spectre.dio_tt', 'spectre.tt']
    assert model['configs']['spectre.dio_tt']['includes'] == [
        {'file_ref': 'file_spe_lib', 'section': 'dio_tt'}]
    assert all(files[key]['dependency'] for key in rows())
    assert len(dependencies) == len(rows())
    assert set(model['depends_on']) == set(dependencies)


def test_entries_returns_every_include_in_a_config(tmp_path):
    decks(tmp_path)
    model, files, _, _ = derive(rows(), {'pdk': str(tmp_path)}, 'derived')

    class Package:
        roots = {'pdk': str(tmp_path)}

        def available(self, name):
            return name in {'model.json', 'file.json'}

        def document(self, name):
            return model if name == 'model.json' else {'items': files}

    package = Package()
    found = entries(package, 'spectre')
    configs = [config for config, _, _, _ in found]
    assert 'spectre.tt' in configs and 'spectre.dio_tt' in configs
    refs, ports, kind, model_type = resolve_detail('cell0', 'spectre', found)
    assert refs == ['spectre.tt'] and ports is None
    assert kind == 'model' and model_type == 'bsim4'


def test_cards_record_model_and_subckt_declarations(tmp_path):
    decks(tmp_path)
    model, _, _, _ = derive(rows(), {'pdk': str(tmp_path)}, 'derived')

    def card(name, simulator):
        return next(row for row in model['cards'].values()
                    if row['name'] == name and row['simulator'] == simulator)

    cell = card('cell0', 'spectre')
    assert cell['kind'] == 'model' and cell['element_type'] == 'bsim4'
    assert cell['file_ref'] == 'file_spe_cell'
    assert cell['lines'] == [1]
    assert cell['configs'] == ['spectre.tt']
    assert cell['sections'] == ['tt']
    dio = card('dio0', 'hspiceD')
    assert dio['element_type'] == 'd'
    assert dio['configs'] == ['hspiceD.dio_tt'] and dio['sections'] == ['dio_tt']
    subckt = card('cell0_ckt', 'hspiceD')
    assert subckt['kind'] == 'subckt'
    assert subckt['ports'] == ['d', 'g', 's', 'b']
    assert subckt['configs'] == ['hspiceD.tt']


def test_ensure_is_idempotent_once_dependencies_are_bound(tmp_path):
    decks(tmp_path)
    model, files, dependencies, _ = derive(rows(), {'pdk': str(tmp_path)}, 'derived')

    class Package:
        roots = {'pdk': str(tmp_path)}

        def available(self, name):
            return name in {'model.json', 'file.json'}

        def document(self, name):
            if name == 'model.json':
                return {**model, 'depends_on': sorted(dependencies)}
            if name == 'file.json':
                return {'source': 'capture', 'depends_on': [], 'items': files}
            return {}

    assert ensure(Package(), 'derived') is None


def test_ensure_adds_the_card_inventory_to_an_existing_model_document(tmp_path):
    decks(tmp_path)
    model, files, dependencies, _ = derive(rows(), {'pdk': str(tmp_path)}, 'derived')
    legacy = {key: value for key, value in model.items() if key != 'cards'}
    legacy['evidence'] = {path: source for path, source in model['evidence'].items()
                          if not path.startswith('/cards/')}

    class Package:
        roots = {'pdk': str(tmp_path)}

        def available(self, name):
            return name in {'model.json', 'file.json'}

        def document(self, name):
            if name == 'model.json':
                return {**legacy, 'depends_on': sorted(dependencies)}
            if name == 'file.json':
                return {'source': 'capture', 'depends_on': [], 'items': files}
            return {}

    pending = ensure(Package(), 'derived')
    assert pending is not None
    assert {'cell0', 'dio0', 'cell0_ckt'} <= {
        row['name'] for row in pending['document']['cards'].values()}
