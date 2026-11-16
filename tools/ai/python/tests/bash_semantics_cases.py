"""User-observable state cases shared by local and managed Bash qualification."""

import shlex
import subprocess


CASES = [
    ('status-zero', ['true', 'printf "%s" "$?"'], b'0'),
    ('status-one', ['false', 'printf "%s" "$?"'], b'1'),
    ('status-return', ['return 7', 'printf "%s" "$?"'], b'7'),
    ('status-max', ['return 255', 'printf "%s" "$?"'], b'255'),
    ('function-status', ['f(){ return 9; }; f', 'printf "%s" "$?"'], b'9'),
    ('short-circuit', ['false && printf wrong', 'printf "%s" "$?"'], b'1'),
    ('pipefail', ['set -o pipefail', 'false | true', 'printf "%s" "$?"'], b'1'),
    ('ordinary-pipeline', ['false | true', 'printf "%s" "$?"'], b'0'),
    ('positional', ["set -- one 'two space'", 'shift; printf "%s:%s" "$#" "$1"'], b'1:two space'),
    ('state', ["export VALUE=kept; unset HOME; f(){ printf function; }; IFS=,; set -u",
               'printf "%s:%s:%s:" "$VALUE" "${HOME-unset}" "$IFS"; f'], b'kept:unset:,:function'),
    ('shell-options', ['set -euo pipefail; shopt -s nullglob',
                       '[[ -o errexit && -o nounset && -o pipefail ]]; shopt -q nullglob; printf options'], b'options'),
    ('err-trap', ["trap 'printf \"err:%s;\" \"$?\"' ERR", 'false',
                  'printf "status:%s" "$?"; trap - ERR'], b'err:1;err:1;status:1'),
    ('errtrace', ['set -E; trap \'printf "err:%s;" "$?"\' ERR', 'false',
                  'printf "status:%s" "$?"; trap - ERR'], b'err:1;err:1;status:1'),
    ('return-trap', ["trap 'printf return;' RETURN; printf first", 'printf next'], b'firstreturnnextreturn'),
    ('exit-trap', ["trap 'printf unexpected' EXIT; false", 'printf "%s" "$?"; trap - EXIT'], b'1'),
    ('empty-source', ['false', '# comment only', 'printf "%s" "$?"'], b'0'),
    ('aliases', ['shopt -s expand_aliases; alias show=\'printf alias\'', 'show'], b'alias'),
]


def reference_bash(directory, texts):
    """Top-level source with no intermediate control or status-reset commands."""
    directory.mkdir()
    lines = []
    for index, text in enumerate(texts):
        path = directory / f'input-{index}.sh'
        path.write_text(text)
        lines.append('builtin source ' + shlex.quote(str(path)))
    return subprocess.run(['/bin/bash', '--noprofile', '--norc'],
        input=('\n'.join(lines) + '\n').encode(), capture_output=True, timeout=5,
        cwd=directory, env=dict(PATH='/usr/bin:/bin', HOME=str(directory), LANG='C.UTF-8'))
