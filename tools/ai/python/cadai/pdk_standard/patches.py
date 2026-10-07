"""Object-only RFC 6901/6902 edits with explicit existence semantics."""

from copy import deepcopy
import re

from .jsonio import fail


def tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/') or re.search(r'~(?![01])', pointer):
        fail('Invalid JSON Pointer')
    return [p.replace('~1', '/').replace('~0', '~') for p in pointer[1:].split('/')]


def pointer(*parts):
    return ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def node(value, path):
    for part in tokens(path) if path else []:
        if isinstance(value, list) and part.isdigit() and str(int(part)) == part and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            fail('Pointer does not address an existing node: ' + path)
    return value


def apply(value, operations):
    result = deepcopy(value)
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        fail('An update needs 1–100 object-member operations')
    for op in operations:
        if not isinstance(op, dict) or op.get('op') not in {'add', 'replace', 'remove'}:
            fail('Unsupported patch operation')
        expected = {'op', 'path'} | (set() if op['op'] == 'remove' else {'value'})
        if set(op) != expected:
            fail('Invalid patch fields')
        parts = tokens(op['path'])
        parent = result
        for part in parts[:-1]:
            if not isinstance(parent, dict) or part not in parent:
                fail('Patch parent must exist; arrays must be replaced as a whole')
            parent = parent[part]
        if not isinstance(parent, dict):
            fail('Only object members can be patched')
        key = parts[-1]
        if (key in parent) == (op['op'] == 'add'):
            fail('Patch existence precondition failed: ' + op['path'])
        if op['op'] == 'remove':
            del parent[key]
        else:
            parent[key] = deepcopy(op['value'])
    return result
