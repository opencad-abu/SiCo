"""Package-level typed fields, resource paths and category references."""

from pathlib import PurePosixPath
import re

from .constraints import numeric, unknown
from .jsonio import fail

DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')


def text(value, maximum=256):
    if not isinstance(value, str) or not value or len(value) > maximum:
        fail('Nonempty bounded string required')


def path(value):
    text(value, 4096)
    if '\\' in value or PurePosixPath(value).is_absolute() or any(p in {'', '.', '..'} for p in value.split('/')):
        fail('Invalid package-relative path')


def state(value):
    if isinstance(value, dict) and 'state' in value:
        if set(value) != {'state', 'reason'} or value['state'] not in {'unknown', 'not_applicable'}:
            fail('Invalid special fact state')
        text(value['reason'])


def manifest(value):
    from .validate import fields, identifier
    for key, row in value['libraries'].items():
        identifier(key)
        fields(row, ('name', 'root', 'path'))
        text(row['name'])
        identifier(row['root'])
        path(row['path'])
    for key, row in value['dependencies'].items():
        identifier(key)
        fields(row, ('kind', 'target', 'fingerprint', 'method'))
        if row['kind'] not in {'library', 'cdf', 'callback', 'symbol', 'interface', 'model', 'file', 'technology'}:
            fail('Unknown dependency kind')
        if not unknown(row['fingerprint']) and not DIGEST.fullmatch(row['fingerprint']):
            fail('Invalid dependency fingerprint')
        text(row['method'])
    for name, digest in value['files'].items():
        path(name)
        if name == 'package.json' or not DIGEST.fullmatch(digest):
            fail('Invalid manifest inventory')
    if not {'device.json', 'category.json', 'file.json', 'sources.json'} <= set(value['files']):
        fail('Missing mandatory package files')
    text(value['revision'])
    text(value['created_at'])
    if not isinstance(value['options'], dict):
        fail('Process options must be a mapping')


def devices(value):
    from .validate import fields, identifier
    for row in value['items'].values():
        if not unknown(row['view']):
            text(row['view'])
        if 'dir' in row:
            path(row['dir'])
        if not unknown(row['categories']):
            categories = row['categories']
            if not isinstance(categories, list) or not categories or len(set(categories)) != len(categories):
                fail('Invalid categories')
            for key in categories:
                identifier(key)
        if not unknown(row['voltage']):
            fields(row['voltage'], ('domain', 'nominal', 'unit'))
            text(row['voltage']['domain'])
            if row['voltage']['unit'] != 'V' or not numeric(row['voltage']['nominal']):
                fail('Invalid voltage domain')
        for limit in row.get('limits', []):
            fields(limit, ('terminals', 'unit'), ('min', 'max', 'when'))
            if (not isinstance(limit['terminals'], list) or len(limit['terminals']) != 2
                    or limit['unit'] != 'V' or not set(limit) & {'min', 'max'}):
                fail('Invalid device voltage limit')
            if any(not numeric(limit[k]) for k in ('min', 'max') if k in limit):
                fail('Invalid voltage boundary')
            if 'min' in limit and 'max' in limit and limit['min'] > limit['max']:
                fail('Empty voltage limit')


def catalog(name, value):
    from .validate import envelope, fields, identifier
    envelope(value)
    fields(value, ('source', 'depends_on', 'items'), ('evidence',))
    for key, row in value['items'].items():
        identifier(key)
        if name == 'category.json':
            fields(row, ('name',), ('parent', 'note'))
            text(row['name'])
        else:
            fields(row, ('kind', 'root', 'path', 'purpose'), ('tool', 'dependency'))
            if row['kind'] not in {'model', 'document', 'runset', 'techfile', 'other'}:
                fail('Unknown resource kind')
            identifier(row['root'])
            path(row['path'])
            text(row['purpose'])
    if name == 'category.json':
        for key in value['items']:
            seen = set()
            while key:
                if key in seen or key not in value['items']:
                    fail('Category parent missing or cyclic')
                seen.add(key)
                key = value['items'][key].get('parent')
