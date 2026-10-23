# Default Bash terminal: initialize interactive helpers before restoring captured
# vendor loader paths. This is sourced by --rcfile, never an executed subprocess.
# Restore markers belong to SiCo and are not exported to initialization helpers.
builtin export -n SICO_TERMINAL_RESTORE_LD_LIBRARY_PATH SICO_TERMINAL_RESTORE_PYTHONPATH

if [[ -r "$HOME/.bashrc" ]]; then
    builtin source "$HOME/.bashrc"
elif [[ -r /etc/bashrc ]]; then
    builtin source /etc/bashrc
fi

if [[ ${SICO_TERMINAL_RESTORE_LD_LIBRARY_PATH+x} ]]; then
    builtin export LD_LIBRARY_PATH="$SICO_TERMINAL_RESTORE_LD_LIBRARY_PATH"
fi
if [[ ${SICO_TERMINAL_RESTORE_PYTHONPATH+x} ]]; then
    builtin export PYTHONPATH="$SICO_TERMINAL_RESTORE_PYTHONPATH"
fi
builtin unset SICO_TERMINAL_RESTORE_LD_LIBRARY_PATH SICO_TERMINAL_RESTORE_PYTHONPATH
