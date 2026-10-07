# PyQt5 AI Terminal

This is the maintained AI Assistant terminal frontend. It keeps Qt outside the
Virtuoso process and preserves the terminal command-line and `control.v2`
lifecycle contracts. The installed `tools/ai/bin/sico-ai-terminal` dispatcher selects
the RHEL 7 or RHEL 8 QTermWidget runtime and then starts
`tools/ai/bin/sico-ai-terminal-pyqt` with the production Python.

The terminal window supports up to eight independent agent tabs against the
same Virtuoso controller and workspace. Use `New Session` from a terminal's
Qt-native context menu (or `Ctrl+Shift+T`) to open a tab. Closing a running tab
asks before terminating only that tab's PTY; closing the window handles all
remaining sessions together.

Double-click a tab title, use its Qt-native `Rename Session` context action, or
press `F2` to edit the current title inline. Empty edits and Escape preserve the
existing title; names last for the lifetime of the terminal window.

A completed tab is removed automatically when another session remains. The
last completed tab stays visible with an `Exited` status until it is closed, so
an agent launcher error remains readable instead of making the window flash
and disappear.

QTermWidget coalesces terminal output before updating its internal screen. The
frontend refreshes Qt's input-method cursor rectangle after that bounded delay,
keeping the IBus/libpinyin candidate window attached to Claude's full-screen
prompt instead of leaving it at the display's upper-left corner.

## Production Runtime Contract

The site Python installation supplies all of the following:

- the interpreter selected by `CAD_PYTHON`, or
  `CAD_PYTHON_ROOT/bin/python3` when `CAD_PYTHON` is unset;
- PyQt5, including `PyQt5.sip` and its SIP metadata;
- the Qt 5 libraries, XCB platform plugin, input-context plugins, and matching
  ICU libraries used by that PyQt5 installation.

The CAD release deliberately does not copy `PyQt5/`, `libQt5*.so`,
`libicu*.so`, Qt plugins, or a second Python runtime. The terminal launcher sets
`PYTHONNOUSERSITE=1`, removes inherited EDA Qt search paths, and restores
`CAD_AI_PYTHON_LIBRARY_PATH` (falling back to
`CAD_SYSTEM_LD_LIBRARY_PATH`) before importing the production PyQt5 package.
This prevents a developer account's `$HOME/.local` package or Virtuoso's Qt
libraries from replacing the qualified site runtime.

The launcher selects one Python frontend root and never searches a fallback
list. An installed `tools/ai/bin/sico-ai-terminal-pyqt` loads only the matching
`tools/ai/python/cad_ai_terminal` package; the source launcher loads only its adjacent
`cad_ai_terminal` package. `CAD_AI_PYQT_ROOT` is an absolute development and
synchronized-test override. When it is set, the launcher uses only that root
and exits if any required frontend module is missing. It does not fall back to
the package in an older installed release.

The native Codex runtime is resolved by the controller, not by this PyQt
launcher. If the controller and terminal are intentionally taken from
different trees, configure `CAD_AI_RELEASE_ROOT` (or use a normal
`CAD_AI_TERMINAL` path) so the controller can locate the release-owned
`runtime/codex/x86_64-unknown-linux-musl/bin/codex`.

Verify the production installation before building or deploying:

```bash
export CAD_PYTHON_ROOT=/software/pkgs/python/3.9.13
export CAD_PYTHON="$CAD_PYTHON_ROOT/bin/python3"
export CAD_SYSTEM_LD_LIBRARY_PATH="$CAD_PYTHON_ROOT/lib"
export CAD_AI_PYTHON_LIBRARY_PATH="$CAD_SYSTEM_LD_LIBRARY_PATH"

env -u PYTHONPATH -u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH \
  PYTHONNOUSERSITE=1 LD_LIBRARY_PATH="$CAD_AI_PYTHON_LIBRARY_PATH" \
  QT_QPA_PLATFORM=offscreen \
  "$CAD_PYTHON" -s - <<'PY'
from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, qVersion
from PyQt5.QtWidgets import QApplication

app = QApplication([])
print("PyQt5", PYQT_VERSION_STR)
print("Qt build", QT_VERSION_STR)
print("Qt runtime", qVersion())
PY
```

Production uses Qt 5. PyQt6 and Qt 6 are not supported.
The site `PyQt5-sip` runtime must implement SIP ABI 12.11 or a later compatible
SIP 12 ABI. The shipped QTermWidget binding is always generated for ABI 12.11;
the build host's newer SIP runtime must not raise that deployment baseline.

## QTermWidget Runtime Build

The release supplies only the QTermWidget-specific runtime:

```text
python/QTermWidget.abi3.so
lib/libqtermwidget5.so.1
share/qtermwidget5/
share/licenses/qtermwidget5/
```

In a universal release the binding and shared library live below
`runtime/rhel7/{python,lib}` and `runtime/rhel8/{python,lib}`. The Python
frontend, QTermWidget resources, and licenses are stored once at the common
`ai/` level.

Build `libqtermwidget5.so.1` from QTermWidget exactly 1.4.0 with the repository's
relocatable-data and session-shutdown patches. The binding helper rejects an
unpatched prefix; `qtermwidget-1.4.0-session-shutdown.patch` is required so
closing the Python window also terminates the owned PTY. The binding adds
`screenLinesCount()` through
`qtermwidget-1.4.0-pyqt-screen-lines.patch`, allowing Select All to include the
visible screen and scrollback.

The source archive is pinned by SHA256:

```text
qtermwidget-1.4.0.tar.gz
f0de18a8fb61ac7bde4b052c962bd3934a6f9c4fa3fd35aae3ddd833fd3b8b60
```

Build one binding/library pair for each target platform. The qmake SDK must use
the same Qt 5 minor ABI as the production PyQt5 runtime; a newer patch release
of that same minor may provide the runtime. The selected production Python must
provide PyQt5, `sipbuild`, `pyqtbuild`, Python headers, and PyQt5 SIP metadata.
Build outputs and temporary files stay below `$CWD/.cad`:

```bash
export CAD_PYTHON_ROOT=/software/pkgs/python/3.9.13
export CAD_PYTHON="$CAD_PYTHON_ROOT/bin/python3"
export CAD_SYSTEM_LD_LIBRARY_PATH="$CAD_PYTHON_ROOT/lib"
export CAD_AI_PYTHON_LIBRARY_PATH="$CAD_SYSTEM_LD_LIBRARY_PATH"
export CAD_AI_PYQT_QMAKE=/path/to/qt-5.15-sdk/bin/qmake
export CAD_AI_PYQT_QT_ROOT=/path/to/qt-5.15-sdk

mkdir -p "$PWD/.cad/qtermwidget-runtime/ai/python" \
  "$PWD/.cad/qtermwidget-runtime/ai/lib"
install -m 0755 /path/to/qtermwidget-prefix/lib/libqtermwidget5.so.1 \
  "$PWD/.cad/qtermwidget-runtime/ai/lib/libqtermwidget5.so.1"

tools/ai/terminal/scripts/build_qtermwidget5_pyqt.sh \
  /path/to/qtermwidget-1.4.0.tar.gz \
  /path/to/qtermwidget-prefix \
  "$PWD/.cad/qtermwidget-runtime/ai/python" \
  "$PWD/.cad/qtermwidget-binding-build"
```

For a target ABI build, `CAD_AI_PYQT_CC`, `CAD_AI_PYQT_CXX`, and
`CAD_AI_PYQT_COMPILER_LIBRARY_PATH` select the qualified compiler wrappers
without changing the Python or Qt runtime used by the import smoke test. The
helper installs `QTermWidget.abi3.so` with old-style
`$ORIGIN/../lib` RPATH and performs a real import against the production PyQt5
runtime and adjacent `libqtermwidget5.so.1`. It also verifies both the generated
module source and a release-visible marker against the fixed SIP ABI 12.11
contract.

RHEL 7 artifacts must stay within `GLIBC_2.17`, `GLIBCXX_3.4.19`, and
`CXXABI_1.3.7`; RHEL 8 artifacts must stay within `GLIBC_2.28`,
`GLIBCXX_3.4.25`, and `CXXABI_1.3.11`. The release verifier checks those limits,
rejects bundled PyQt5/Qt/ICU/plugins, and repeats the production import smoke.

## Installation And Tests

CMake requires the QTermWidget prefix and the platform binding; the pinned
native Codex executable defaults to the in-tree
`tools/ai/runtime/codex/x86_64-unknown-linux-musl/bin/codex` and accepts a different
qualified copy through `CAD_AI_CODEX_EXECUTABLE`:

```bash
cmake -S tools/ai/terminal -B "$PWD/.cad/terminal-stage-build" \
  -DCMAKE_INSTALL_PREFIX=/path/to/stage/ai \
  -DCAD_AI_QTERMWIDGET_ROOT=/path/to/qtermwidget-prefix \
  -DCAD_AI_QTERMWIDGET_PYTHON_MODULE=\
/path/to/QTermWidget.abi3.so
cmake --install "$PWD/.cad/terminal-stage-build"
```

Python/PyQt5 is the only AI Assistant terminal GUI implementation. The
QTermWidget extension and shared library installed by CMake are native backend
dependencies of that Python frontend; CMake does not build or install a second
C++ terminal executable or fallback.

Protocol tests do not import PyQt5:

```bash
"$CAD_PYTHON" -m pytest -q tools/ai/python/tests/test_pyqt_terminal.py
```
