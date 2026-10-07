# CAD AI Assistant Terminal

The maintained terminal is a separate PyQt5/QTermWidget process launched from
Virtuoso. Keeping it out of the Virtuoso address space avoids mixing Cadence's
Qt libraries with the production Python GUI runtime. The frontend is
agent-neutral and preserves the same PTY and `control.v2` lifecycle for Codex
CLI and Claude Code.

## Runtime Ownership

The production environment owns:

- `CAD_PYTHON`, or `CAD_PYTHON_ROOT/bin/python3` as its fallback;
- PyQt5 and `PyQt5.sip` installed in that Python tree;
- the Qt 5 libraries, platform/input-context plugins, and ICU used by PyQt5.

The CAD release owns the QTermWidget payload and a native Codex CLI:

- `QTermWidget.abi3.so`;
- `libqtermwidget5.so.1`;
- the selected QTermWidget keymap and color scheme;
- QTermWidget license texts.
- the pinned static Codex executable, checksum manifest, and Apache-2.0 license.

The AI Assistant terminal GUI itself is implemented only in Python/PyQt5.
QTermWidget remains a native C++ library with a Python binding because it is
the terminal backend; it is not an independently maintained GUI or fallback.

The release must not contain a private `PyQt5/` tree, Qt or ICU libraries, or Qt
plugins. This keeps the universal package small and makes the production Python
installation the single Qt runtime contract for CAD Python GUIs.

At startup `tools/ai/bin/sico-ai-terminal` selects the RHEL 7 or RHEL 8 QTermWidget
pair, exports `CAD_AI_QTERMWIDGET_RUNTIME` and
`CAD_AI_QTERMWIDGET_DATA_PATH`, and executes the common
`sico-ai-terminal-pyqt` launcher. The launcher disables user-site packages and
places only the selected QTermWidget `python/` and `lib/` directories ahead of
inherited EDA paths. PyQt5 and Qt continue to load from the production Python.
The common launcher loads `cad_ai_terminal` only from the same release's
`tools/ai/python` directory. An explicit absolute `CAD_AI_PYQT_ROOT` is strict: an
incomplete override fails startup instead of falling back to the release copy.
The build helpers and QTermWidget patches remain in the private source tree.

## Production Prerequisites

Set and verify the shared Python before starting Virtuoso or validating a
release:

```bash
export CAD_PYTHON_ROOT=/software/pkgs/python/3.9.13
export CAD_PYTHON="$CAD_PYTHON_ROOT/bin/python3"
export CAD_SYSTEM_LD_LIBRARY_PATH="$CAD_PYTHON_ROOT/lib"
export CAD_AI_PYTHON_LIBRARY_PATH="$CAD_SYSTEM_LD_LIBRARY_PATH"

env -u PYTHONPATH -u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH \
  PYTHONNOUSERSITE=1 LD_LIBRARY_PATH="$CAD_AI_PYTHON_LIBRARY_PATH" \
  QT_QPA_PLATFORM=offscreen \
  "$CAD_PYTHON" -s -c \
  'from PyQt5.QtCore import qVersion; from PyQt5.QtWidgets import QApplication; app=QApplication([]); print(qVersion())'
```

The same production Python path must be visible on local and LSF execution
hosts. It must provide Python headers, PyQt5, `PyQt5.sip`, `sipbuild`,
`pyqtbuild`, and PyQt5 binding metadata when building the QTermWidget extension.
Runtime hosts need PyQt5 but do not need the build tools or headers.

Qt 5 is required. PyQt6/Qt 6 and user-site PyQt wheels are unsupported.
The QTermWidget binding targets SIP ABI `12.11`, so it works with production
`PyQt5-sip` 12.11 and later compatible SIP 12 runtimes. Rebuilds must retain
this target even when the build host has a newer `PyQt5-sip` installed.

## Platform Runtime Build

The repository also includes ripgrep 15.2.0 as a shared static x86-64 runtime.
CMake verifies `ripgrep-runtime.lock` and installs `runtime/ripgrep` alongside
Codex. All release packagers require it. Managed Agent terminals add its
directory to `PATH`, and the `Search` MCP tool calls it directly. See
[Search MCP](../docs/SEARCH_MCP.md) for parameters and release checks.

QTermWidget is pinned to version 1.4.0. Build its shared library from the local
QTermWidget and lxqt-build-tools sources with the repository patches, then build
the SIP extension against the same prefix. The qmake SDK must use the same Qt 5
minor ABI as production PyQt5; the production runtime may be a newer patch
release of that minor.

The source archives and build SDKs are build inputs only. None of Python,
PyQt5, Qt, ICU, qmake, compilers, headers, or sysroots is copied into the
release. Build files must remain below the launch directory's `.cad` tree.

The release also carries the pinned native Codex CLI `0.156.1`. It is a
static-PIE `x86_64-unknown-linux-musl` ELF with no `PT_INTERP`, `DT_NEEDED`,
glibc, libstdc++, or Node.js dependency. The same executable is therefore
shared by the RHEL 7 and RHEL 8 packages; only QTermWidget's binding/library
pair is platform-specific.

This pin uses the native Responses namespace format. `SICO_LEGACY_0114=1`
enables the same namespace-to-flat wrapper used by Copilot; it never selects an
older executable. `0` forces native mode; unset preserves existing
`SICO_TOOL_FORMAT` configuration and defaults to native. Restart the session
after switching. Compatibility mode requires explicit `CAD_CODEX_API_BASE_URL`
and `CAD_CODEX_API_KEY`; the controller owns the wrapper and real key, while the
terminal receives its loopback URL and local token. Normal exit and startup
failure close the wrapper. Core, home and history remain 0.156.1 in both modes.
See [Codex compatibility and checksums](../docs/codex-0.156-compatibility.md).

The pinned runtime lives in the source tree at
`tools/ai/runtime/codex/x86_64-unknown-linux-musl` — the same layout as
`tools/ai/runtime/ripgrep` — and CMake installs it by default. The importer writes an
identical tree when the input is refreshed or an isolated stage is preferred:

```bash
tools/ai/terminal/scripts/import_codex_runtime.sh \
  "$PWD/.cad/codex-runtime" \
  /path/to/codex-x86_64-unknown-linux-musl.tar.gz \
  /path/to/codex-code-mode-host-x86_64-unknown-linux-musl.tar.gz
```

Both RHEL stages use
`runtime/codex/x86_64-unknown-linux-musl/bin/codex` and its adjacent
`codex-code-mode-host`; `-DCAD_AI_CODEX_EXECUTABLE` installs a different
qualified copy. Omitted importer inputs are downloaded from the exact pinned
release and verified; offline import requires both inputs. The native release is
static PIE and has no interpreter or dynamic library dependency, so it is
independent of the RHEL 7 versus RHEL 8 glibc baseline. Node.js/npm are not
part of the Codex runtime. CMake verifies both pinned checksums and the CLI
version, then stages the binaries and manifest. Run
`tools/ai/terminal/scripts/strip_codex_runtime.sh "$CAD_AI_STAGE"` after
`cmake --install` and before verification: the shipped release carries the
stripped copies recorded as `release_*` in the lock, and `verify_release_abi.sh`
checks that identity. The step is idempotent and rejects a binary that is
neither the pinned upstream one nor the recorded shipped one. This stage still
requires the runtime-only release inventory and application qualification.

Development launchers resolve one installation from `SICO_HOME`. Use
`SICO_AI_TERMINAL` for an explicit terminal command; an optional
`SICO_AI_RELEASE_ROOT` must identify that installation's `tools/ai`, not a
second runtime. `SICO_AI_PYQT_ROOT` selects a source frontend only for source
installations. The current temporary authority is the project `.sico/ai`.

Source-test refreshes update `bin/sico-ai-terminal` and
`bin/sico-ai-terminal-pyqt` from their source launchers. The tracked
`bin/sico-ai-pinyin` delegates to the shared `sico_pinyin` implementation.
Binary packages install native entries at these paths and obtain their Qt and
Python dependencies exclusively from the bundled runtime.

After producing a patched QTermWidget prefix, build the binding:

```bash
export CAD_AI_PYQT_QMAKE=/path/to/qt-5.15-sdk/bin/qmake
export CAD_AI_PYQT_QT_ROOT=/path/to/qt-5.15-sdk

mkdir -p "$PWD/.cad/rhel8-runtime/ai/python" \
  "$PWD/.cad/rhel8-runtime/ai/lib"
install -m 0755 /path/to/qtermwidget-prefix/lib/libqtermwidget5.so.1 \
  "$PWD/.cad/rhel8-runtime/ai/lib/libqtermwidget5.so.1"
tools/ai/terminal/scripts/build_qtermwidget5_pyqt.sh \
  /path/to/qtermwidget-1.4.0.tar.gz \
  /path/to/qtermwidget-prefix \
  "$PWD/.cad/rhel8-runtime/ai/python" \
  "$PWD/.cad/rhel8-binding-build"
```

Use `CAD_AI_PYQT_CC`, `CAD_AI_PYQT_CXX`, and
`CAD_AI_PYQT_COMPILER_LIBRARY_PATH` for qualified target compiler wrappers.
Build and validate separate RHEL 7 and RHEL 8 pairs. The verifier enforces:

| Target | GLIBC | GLIBCXX | CXXABI |
| --- | --- | --- | --- |
| RHEL 7 | `2.17` | `3.4.19` | `1.3.7` |
| RHEL 8 | `2.28` | `3.4.25` | `1.3.11` |

`QTermWidget.abi3.so` must have old-style `$ORIGIN/../lib` RPATH, depend on
`libqtermwidget5.so.1`, declare SIP ABI `12.11`, and import successfully with
the production PyQt5. The
shared library must contain the session-shutdown and relocatable-resource
patches. See [the PyQt terminal build notes](pyqt/README.md) for the complete
contract.

## Stage And Verify

Install one platform stage by providing its binding and QTermWidget prefix:

```bash
export CAD_AI_STAGE="$PWD/.cad/rhel8-stage/ai"

cmake -S tools/ai/terminal -B "$PWD/.cad/rhel8-install-build" \
  -DCMAKE_INSTALL_PREFIX="$CAD_AI_STAGE" \
  -DCAD_AI_QTERMWIDGET_ROOT=/path/to/qtermwidget-prefix \
  -DCAD_AI_QTERMWIDGET_PYTHON_MODULE=\
"$PWD/.cad/rhel8-runtime/ai/python/QTermWidget.abi3.so"
cmake --install "$PWD/.cad/rhel8-install-build"

```

The default CMake stage contains only native dependencies and minimal UI resources.
It is not a runnable application release. Internal source-based ABI verification
requires a separate development stage configured with
`-DCAD_AI_DEVELOPMENT_STAGE=ON`; that stage is never distributable.
Use `verify_rhel7_abi.sh` or `verify_rhel8_abi.sh` for the corresponding internal
terminal contract, including offscreen `QApplication`/QTermWidget import.

## Runtime Release Policy

All legacy platform, universal and source packagers are disabled as of 2026-09-11.
Follow [the CAD runtime policy](../../deploy/RUNTIME_RELEASE_POLICY.md). Add
qualified native application executables/libraries and Virtuoso contexts to a
clean runtime stage, then use the common release gate:

```bash
python3 deploy/package_runtime_release.py \
  /path/to/qualified/runtime-stage \
  /path/to/private/release-inventory.json \
  /path/to/output/cad-runtime.tar.gz
```

Every payload file must be explicitly inventoried and bound to target acceptance.
Project documentation, AI skills/prompts/references/catalogs, source code, bytecode
and source bundles are excluded. Instructions are supplied by the application/MCP
server. Required third-party licenses remain individually listed.

A combined RHEL 7/RHEL 8 payload requires separate target acceptance reports.
Share only the artifacts actually qualified on both targets. Keep the matching
QTermWidget binding/library pair per platform and qualify a compiled dispatcher;
the historical shell dispatcher is not eligible for distribution.

## Input And Lifecycle Behavior

The maintained frontend inherits the desktop input environment for local
sessions. The Python environment sanitizer does not select or replace an input
method. The terminal uses UTF-8 for PTY input and output independently of the
locale inherited from Virtuoso.

The frontend enters the shared `sico_pinyin.input_session` before QApplication.
A positive `LSB_JOBID` selects a private IBus/libpinyin stack owned by that GUI;
the dispatcher does not add a second wrapper. Main windows and quick composers
use the same implementation. Other GUI commands can use
`tools/ai/bin/sico-ai-pinyin -- COMMAND [ARG ...]`.
See [Pinyin input](../docs/PINYIN_INPUT.md) for runtime dependencies, cleanup and
source-hidden binary acceptance.

Right-click exposes New Session, Copy, Paste, Select All, and Find. New Session
opens an independent agent tab against the same Virtuoso controller and
workspace; `Ctrl+Shift+T` provides the same command. The window supports at
most eight sessions. Select All includes the visible screen and retained
scrollback. Typing, committed input-method text, or pasting returns a terminal
that was reading history to the live bottom; output alone does not force that
move.

Closing a running tab asks before terminating only that session. Closing the
window offers `Exit Sessions`, `Minimize`, and `Cancel`; Cancel is the default
and Escape action. Minimize preserves all tabs, the controller, and their
agents. Exit Sessions releases every QTermWidget and its patched PTY session.
Controller shutdown, control-channel EOF, or Virtuoso exit closes the window
without a second prompt.

QTermWidget 1.4.0 contains GPL-2.0, LGPL-2.0+, and BSD-3-Clause material.
Review the intended distribution model and satisfy the applicable source and
license obligations before shipping it outside the organization.
