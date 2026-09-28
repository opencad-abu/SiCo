# Development adapter. Runtime releases compile the Python owner and launcher.
# Always derive the common module from this installation, never from CWD.
sico_exec_with_temporary() {
    local sico_python=$1
    shift
    local sico_common=$SICO_HOME/tools/common/python
    exec "$sico_python" -E -s -c '
import sys
sys.path.insert(0, sys.argv[1])
from sicotemp import exec_command
exec_command(sys.argv[2:])
' "$sico_common" "$@"
}
