"""Inventory of model-card and subckt declarations from registered decks.

The inventory is a projection of the deck text, not a second source of model
paths or parameters.  Each card records where it is declared and which
config/section closures actually reach it, so a caller can distinguish an
available alternate model from a registered file that is never loaded.
"""

from hashlib import sha256
from pathlib import Path

from .model_collection import read_text, resource_path, simulator
from .model_decks import declaration_rows, includes, section_spans


def text(path, cache):
    if path not in cache:
        cache[path] = read_text(path)
    return cache[path]


def closure_entries(path, cache, section=None):
    """(path, text) for a section body and its include closure."""
    path = Path(path)
    content = text(path, cache)
    if section is None:
        body = content
    else:
        span = section_spans(content).get(section)
        if span is None:
            return None
        _, body = span
    seen = {path}
    entries = [(path, body)]

    def visit(current, value):
        for target in includes(value):
            child = Path(current).parent / target
            try:
                if not child.is_file() or child in seen:
                    continue
                seen.add(child)
                child_text = text(child, cache)
                entries.append((child, child_text))
                visit(child, child_text)
            except OSError:
                continue

    visit(path, body)
    return entries


def cards(files, roots, configs):
    """Inventory of model-card and subckt declarations, keyed stably."""
    cache, result, issues, identity, path_ref = {}, {}, [], {}, {}
    for key, row in sorted(files.items()):
        if row.get('kind') != 'model':
            continue
        path = resource_path(row, roots)
        if path is None:
            continue
        path_ref[path.resolve()] = key
        name = simulator(row.get('path') or '')
        if name is None:
            continue
        try:
            content = text(path, cache)
        except (OSError, ValueError) as exc:
            issues.append({'code': 'model_resource_unavailable', 'file': key,
                           'path': str(path), 'message': str(exc)})
            continue
        for row_ in declaration_rows(content):
            card_identity = f"{name}:{key}:{row_['kind']}:{row_['name']}"
            card_id = 'card_' + sha256(card_identity.encode('utf-8')).hexdigest()[:20]
            entry = result.setdefault(card_id, {
                'name': row_['name'], 'kind': row_['kind'], 'simulator': name,
                'file_ref': key, 'lines': [], 'configs': [], 'sections': []})
            identity[(name, key, row_['kind'], row_['name'])] = entry
            if row_['line'] not in entry['lines']:
                entry['lines'].append(row_['line'])
            if row_.get('element_type'):
                entry['element_type'] = row_['element_type']
            if row_.get('ports'):
                entry['ports'] = row_['ports']
    for config_id, config in sorted(configs.items()):
        for include in config.get('includes', []):
            ref = include.get('file_ref')
            path = resource_path(files.get(ref, {}), roots)
            if path is None:
                continue
            try:
                closure = closure_entries(path, cache, include.get('section'))
            except (OSError, ValueError) as exc:
                issues.append({'code': 'model_resource_unavailable', 'file': ref,
                               'path': str(path), 'message': str(exc)})
                continue
            section = include.get('section')
            if closure is None:
                issues.append({'code': 'model_configuration_section_missing',
                               'config': config_id, 'file': ref,
                               'message': 'Declared model section is absent: ' + str(section)})
                continue
            for file_path, content in closure:
                file_ref = path_ref.get(Path(file_path).resolve())
                if file_ref is None:
                    continue
                current = simulator(files[file_ref].get('path') or '')
                for row_ in declaration_rows(content):
                    card = identity.get((current, file_ref, row_['kind'], row_['name']))
                    if card is None:
                        continue
                    if config_id not in card['configs']:
                        card['configs'].append(config_id)
                    if section and section not in card['sections']:
                        card['sections'].append(section)
    for card in result.values():
        card['lines'] = sorted(set(card['lines']))
        card['configs'] = sorted(set(card['configs']))
        card['sections'] = sorted(set(card['sections']))
    return result, issues

