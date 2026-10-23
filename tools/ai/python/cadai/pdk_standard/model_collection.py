"""Derive model.json and model-byte dependencies from registered vendor decks.

The vendor deck layout already states which sections exist and which files
each section includes.  This module reads that structure once, turns every
declared simulator section into a model configuration, groups matching corner
sections into a corner, records model/subckt declarations with the configs
and sections that reach them, and fingerprints every registered model file.
It never invents a section name or a corner membership that the decks do not
declare.
"""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from .model_decks import sections
from .project import safe_id

METHOD = 'sico-model-deck-config-v1'
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
CORNER_SUFFIXES = {'tt', 'ff', 'ss', 'mc', 'snfp', 'fnsp'}


def simulator(path):
    parts = Path(path).parts
    if 'spectre' in parts:
        return 'spectre'
    if 'hspice' in parts:
        return 'hspiceD'
    return None


def language(name):
    return 'spectre' if name == 'spectre' else 'spice'


def suffix(section):
    return section.rsplit('_', 1)[-1] if '_' in section else section


def dependency_id(file_ref):
    return 'bytes_' + sha256(file_ref.encode('utf-8')).hexdigest()[:20]


def unique(identifier, used):
    candidate = safe_id(identifier)
    if candidate not in used:
        return candidate
    for index in range(2, 1000):
        candidate = safe_id(identifier + '.' + str(index))
        if candidate not in used:
            return candidate
    raise ValueError('Model configuration id space exhausted')


def read_text(path):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('model file exceeds the collection byte budget')
    return path.read_text(encoding='utf-8', errors='replace')


def resource_path(row, roots):
    root = roots.get(row.get('root'))
    if not root or not isinstance(row.get('path'), str):
        return None
    return Path(root) / row['path']


def fingerprint_resources(files, roots):
    """Return (updated file rows, dependencies, issues) for model resources."""
    updated, dependencies, issues = deepcopy(files), {}, []
    total = 0
    for key, row in sorted(updated.items()):
        if row.get('kind') != 'model':
            continue
        path = resource_path(row, roots)
        if path is None:
            issues.append({'code': 'model_resource_path_missing', 'file': key,
                           'message': 'Model resource has no resolvable root/path'})
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            issues.append({'code': 'model_resource_unavailable', 'file': key,
                           'path': str(path), 'message': str(exc)})
            continue
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            issues.append({'code': 'model_resource_budget_exceeded', 'file': key,
                           'message': 'Registered model files exceed the collection byte budget'})
            break
        dep = dependency_id(key)
        purpose = row.get('purpose')
        if isinstance(purpose, str) and 'unconfirmed' in purpose.lower():
            purpose = 'Model resource referenced by model.json; bytes fingerprinted'
        updated[key] = {**row, 'dependency': dep, **({'purpose': purpose} if purpose else {})}
        dependencies[dep] = {'kind': 'model', 'target': key,
                             'fingerprint': 'sha256:' + sha256(raw).hexdigest(),
                             'method': 'sha256-bytes-v1'}
    return updated, dependencies, issues


def sectioned(files, roots):
    """simulator -> [(file_ref, path, section names)] plus non-section issues."""
    rows, issues = {}, []
    for key, row in sorted(files.items()):
        if row.get('kind') != 'model':
            continue
        name = simulator(row.get('path') or '')
        if name is None:
            issues.append({'code': 'model_simulator_unknown', 'file': key,
                           'path': row.get('path'),
                           'message': 'Model file is outside a registered simulator directory'})
            continue
        path = resource_path(row, roots)
        if path is None:
            continue
        try:
            text = read_text(path)
        except (OSError, ValueError) as exc:
            issues.append({'code': 'model_resource_unavailable', 'file': key,
                           'path': str(path), 'message': str(exc)})
            continue
        declared = sections(text)
        if declared:
            rows.setdefault(name, []).append((key, path, sorted(declared)))
    return rows, issues


def derive(files, roots, source):
    """Build (model.json or None, updated file rows, dependencies, issues)."""
    updated, dependencies, issues = fingerprint_resources(files, roots)
    decks, deck_issues = sectioned(updated, roots)
    issues.extend(deck_issues)
    configs, corners, evidence = {}, {}, {}
    for name, rows in sorted(decks.items()):
        if len(rows) != 1:
            issues.append({'code': 'model_configuration_ambiguous', 'simulator': name,
                           'message': f'Multiple sectioned {name} decks; '
                                      'corner membership is ambiguous'})
            continue
        file_ref, path, names = rows[0]
        section_config = {}
        for section in names:
            config_id = unique(name + '.' + section, configs)
            configs[config_id] = {
                'simulator': name,
                'language': language(name),
                'includes': [{'file_ref': file_ref, 'section': section}],
                'note': f'Derived from the declared {section} section; '
                        'model bytes are fingerprinted.',
            }
            section_config.setdefault(section, []).append(config_id)
            evidence['/configs/' + config_id] = source
        groups = {}
        for section, ids in section_config.items():
            group = suffix(section)
            if group in CORNER_SUFFIXES:
                groups.setdefault(group, []).extend(ids)
        for group, ids in sorted(groups.items()):
            corner_id = name + '.' + group
            corners[corner_id] = {
                'name': group.upper(),
                'configs': sorted(ids),
                'note': f'Derived from the declared {group} sections; '
                        'no temperature/supply is implied.',
            }
            evidence['/corners/' + corner_id] = source
        if name + '.tt' not in corners:
            issues.append({'code': 'model_default_corner_missing', 'simulator': name,
                           'message': f'The {name} decks declare no tt/default corner'})
    if not configs or not corners:
        return None, updated, dependencies, issues
    from .model_cards import cards as card_inventory

    card_rows, card_issues = card_inventory(updated, roots, configs)
    issues.extend(card_issues)
    for card_id in card_rows:
        evidence['/cards/' + card_id] = source
    document = {'source': source, 'depends_on': sorted(dependencies),
                'configs': configs, 'corners': corners, 'cards': card_rows,
                'evidence': evidence}
    return document, updated, dependencies, issues


def derive_package(package, source):
    """Derive from an effective package without writing anything."""
    return derive(package.document('file.json')['items'], package.roots, source)


def ensure(package, source=None):
    """Return pending model documents when configuration or byte bindings need repair."""
    original = package.document('file.json')['items']
    files, dependencies, issues = fingerprint_resources(original, package.roots)
    if package.available('model.json'):
        model = deepcopy(package.document('model.json'))
        changed = False
        if 'cards' not in model:
            from .model_cards import cards as card_inventory

            card_rows, card_issues = card_inventory(files, package.roots, model['configs'])
            model['cards'] = card_rows
            model.setdefault('evidence', {})
            source = model.get('source') or 'model_deck_derivation'
            for card_id in card_rows:
                model['evidence']['/cards/' + card_id] = source
            issues.extend(card_issues)
            changed = True
        refs = set()
        for config in model['configs'].values():
            refs.update(include['file_ref'] for include in config['includes'])
        missing = {key: row for key, row in files.items()
                   if key in refs and row.get('dependency') and
                   key in original and not original[key].get('dependency')}
        if (not changed and not missing
                and set(dependencies) <= set(model.get('depends_on') or [])):
            return None
        model['depends_on'] = sorted({*model.get('depends_on', []), *dependencies})
        return {'document': model, 'files': files,
                'dependencies': {key: dependencies[key] for key in
                                 {row['dependency'] for row in missing.values()}},
                'source': None, 'issues': issues}
    legacy, files, dependencies, issues = derive(original, package.roots, source)
    return {'document': legacy, 'files': files, 'dependencies': dependencies,
            'source': source, 'issues': issues}
