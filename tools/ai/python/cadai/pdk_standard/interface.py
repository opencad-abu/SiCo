"""Resolve the confirmed CDF design inputs and their complete dependency closure."""

from .jsonio import fail
from .policy_modes import references


def parameters(cdf):
    definitions = cdf['parameters']
    selected = set(cdf.get('interface_parameters', definitions))
    selected.update(n for n, p in definitions.items() if p['write'] in {'allow', 'conditional'}
                    or p['requirement'] in {'explicit', 'conditional'})
    return closure(cdf, selected)


def collection_parameters(cdf):
    """Detailed facts concern confirmed inputs, with minimal facts for dependencies.

    Unlike legacy design qualification, an absent interface declaration does not
    turn every observed CDF property into a documentation obligation.
    """
    inputs = {n for n, p in cdf['parameters'].items() if p['write'] in {'allow', 'conditional'}}
    selected = inputs | set(cdf.get('interface_parameters', []))
    return inputs, closure(cdf, selected) if selected else set()


def closure(cdf, selected):
    definitions = cdf['parameters']
    selected = set(selected) | references(cdf.get('rules', {}))
    while True:
        if selected - set(definitions):
            fail('Missing CDF interface dependency')
        expanded = selected | {dep for n in selected for dep in definitions[n].get('depends_on', [])}
        expanded.update(references({n: definitions[n] for n in selected}))
        if expanded == selected:
            return selected
        selected = expanded
