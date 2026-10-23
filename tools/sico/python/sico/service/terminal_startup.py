"""Stage captured loader paths around an ordinary Bash interactive startup."""

import os
from pathlib import PurePosixPath

from sicoresources import current

from .terminal_environment import shell_command, shell_environment


_LOADERS = ('LD_LIBRARY_PATH', 'PYTHONPATH')
_PREFIX = 'SICO_TERMINAL_RESTORE_'


def prepare_shell(environment=None, *, command=None, inherited=None):
    """Return launch argv/environment without changing the parent or site files.

    Captured vendor libraries cannot be used to initialize system profile tools.
    Default Bash uses its normal non-login interactive rc with these loaders
    staged until initialization finishes. Explicit commands and other shells
    retain their existing semantics. Values are never embedded in argv.
    """
    source = dict(os.environ if environment is None else environment)
    target = shell_environment(source)
    if any(name.startswith(_PREFIX) for name in source):
        raise ValueError('Terminal startup restore names are reserved')
    shell = source.get('SHELL', '').strip() or '/bin/bash'
    if command is not None or PurePosixPath(shell).name != 'bash' or not any(target.get(k) for k in _LOADERS):
        return shell_command(source, command=command, inherited=inherited), target
    if not PurePosixPath(shell).is_absolute():
        raise ValueError('Terminal shell must have an absolute path')
    rc = current(source).path('tools/sico/shell/interactive.bash')
    if not rc.is_file():
        raise ValueError('Terminal Bash initialization is unavailable')
    for name in _LOADERS:
        if name in target:
            target[_PREFIX + name] = target.pop(name)
    argv = [shell, '--noprofile', '--rcfile', str(rc), '-i']
    parent = dict(source)
    parent.update(os.environ if inherited is None else inherited)
    return shell_command(target, command=argv, inherited=parent), target
