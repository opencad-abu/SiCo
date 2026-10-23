"""Model-deck reads: subckt ports, model cards, sections and .inc closure.

A device's netlist identity lives in the decks twice: the subckt or model
card that declares it, and the corner sections that feed it values. This
module reads both from the real files, following .inc directives with a
bounded depth so a corner section's closure can be searched as one text.
"""

import re

from .jsonio import fail

MAX_DEPTH = 4


def section_spans(text):
    """Section name -> (first body line, body) for both deck syntaxes."""
    rows, name, body, first = {}, None, [], None
    for number, line in enumerate(text.splitlines(), 1):
        low = line.strip().lower()
        match = re.match(r'^\.lib\s+(\S+)\s*$', low)
        if match:
            if name is not None:
                rows[name] = (first or number, '\n'.join(body))
            name, body, first = match.group(1), [], None
            continue
        if re.match(r'^\.endl\b', low) or re.match(r'^endsection\b', low):
            if name is not None:
                rows[name] = (first or number, '\n'.join(body))
            name, body, first = None, [], None
            continue
        match = re.match(r'^section\s+(\S+)\s*$', low)
        if match:
            if name is not None:
                rows[name] = (first or number, '\n'.join(body))
            name, body, first = match.group(1), [], None
            continue
        if name is not None:
            if first is None:
                first = number
            body.append(line)
    if name is not None:
        rows[name] = (first or 1, '\n'.join(body))
    return rows


def sections(text):
    """Section name -> body for hspice .lib/.endl and spectre section/endsection."""
    return {name: body for name, (_, body) in section_spans(text).items()}


def includes(text):
    """.inc / .include targets in file order."""
    rows = []
    for line in text.splitlines():
        match = re.match(r'^\s*\.?inc(?:lude)?\s+[\'"]?([^\s\'"]+)', line, re.I)
        if match:
            rows.append(match.group(1))
    return rows


def expand(text, load, depth=0):
    """Section closure with .inc files appended; bounded and order-preserving."""
    if depth >= MAX_DEPTH:
        return text
    extra = []
    for target in includes(text):
        try:
            child = load(target)
        except OSError:
            continue
        extra.append(expand(child, load, depth + 1))
    return text + '\n' + '\n'.join(extra)


def token(name):
    """A netlist name as a whole token, with underscores acting as separators."""
    return re.compile(r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9])')


def declares(text, name):
    """The text declares the name as a subckt or a model card.

    Element references such as a resistor macro model's internal diodes do
    not make the name a declaration of that section.
    """
    return name.lower() in declarations(text)


def declarations(text):
    """Declared netlist names -> subckt or model, ignoring comment lines."""
    rows = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('*') or stripped.startswith('//'):
            continue
        match = re.match(r'\.?(?:inline\s+)?subckt\s+(\w+)', stripped, re.I)
        if match:
            rows.setdefault(match.group(1).lower(), 'subckt')
            continue
        match = re.match(r'\.?model\s+(\w+)', stripped, re.I)
        if match:
            rows.setdefault(match.group(1).lower(), 'model')
    return rows


def declaration_rows(text):
    """Declared netlist names with line/kind/element-type evidence.

    This is the inventory projection used by model collection.  It keeps the
    declaration line and the model-card element type so a caller can choose a
    non-default model/section without inferring it from the file name.
    """
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith('*') or stripped.startswith('//'):
            continue
        match = re.match(r'\.?(?:inline\s+)?subckt\s+(\w+)', stripped, re.I)
        if match:
            name = match.group(1)
            rows.append({'name': name, 'kind': 'subckt', 'line': number,
                         'ports': subckt_ports(text, name) or []})
            continue
        match = re.match(r'\.?model\s+(\w+)\s+(\w+)', stripped, re.I)
        if match:
            rows.append({'name': match.group(1), 'kind': 'model', 'line': number,
                         'element_type': match.group(2).lower()})
    return rows


def subckt_ports(text, name):
    """Ports of the first subckt header for the name, or None when absent."""
    for line in text.splitlines():
        low = line.strip().lower()
        match = re.match(r'\.?(?:inline\s+)?subckt\s+(\w+)\s*(.*)', low)
        if not match or match.group(1) != name.lower():
            continue
        rest = match.group(2)
        if rest.startswith('('):
            return rest[1:rest.find(')')].split()
        tokens = rest.split()
        ports = []
        for index, item in enumerate(tokens):
            if '=' in item or item == '=' or (index + 1 < len(tokens) and tokens[index + 1] == '='):
                break
            ports.append(item)
        return ports
    return None


def model_card(text, name):
    """The element type of the model card for the name, or None when absent."""
    for line in text.splitlines():
        match = re.match(r'\.?model\s+(\w+)\s+(\w+)', line.strip(), re.I)
        if match and match.group(1).lower() == name.lower():
            return match.group(2)
    return None


def resolve_detail(name, simulator, entries):
    """Resolve one netlist name from an ordered list of corner entries.

    entries: (config_id, section, path, load) — the loader returns one
    included file's text, resolved relative to the including file.  A
    ``None`` section means the whole include is the closure (vendor model
    files commonly have no section of their own).
    Returns refs, ports, kind and the model-card element type.
    """
    refs, ports, kind, model_type = [], None, None, None
    for config_id, section, path, load in entries:
        text = load(path)
        body = text if section is None else sections(text).get(section)
        if body is None:
            fail('Model configuration section missing: ' + config_id)
        closure = expand(body, load)
        if not declares(closure, name):
            continue
        refs.append(config_id)
        if ports is None and kind is None:
            candidate = subckt_ports(closure, name)
            if candidate:
                ports, kind = candidate, 'subckt'
            else:
                card = model_card(closure, name)
                if card:
                    kind, model_type = 'model', card.lower()
    return refs, ports, kind, model_type


def resolve(name, simulator, entries):
    """Compatibility projection of :func:`resolve_detail`."""
    refs, ports, kind, _ = resolve_detail(name, simulator, entries)
    return refs, ports, kind
