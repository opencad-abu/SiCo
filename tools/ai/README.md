# AI Assistant for Virtuoso

SiCo PDK data follows [《面向智能体的定制电路工艺数据标准（试行）》](docs/SICO_PDK_DATA_STANDARD.md)
(SICO-PDK-DATA 1.0.0, effective 2026-09-22). It defines the authoritative compact
format, confirmed device/CDF usage rules, workspace updates and bounded reads.
The [standard collection workflow](docs/PDK_COLLECTION_STANDARD_WORKFLOW.md) documents
the implemented tools, confirmation flow, compatibility paths and remaining capability limits.
Runtime distribution qualification remains separate.

Deployment packages follow the [CAD runtime release policy](../deploy/RUNTIME_RELEASE_POLICY.md):
no project docs, AI skill/prompt/reference/catalog assets, or proprietary source.
SKILL ships as protected Virtuoso contexts; Python ships as qualified native
artifacts. Operational instructions are supplied by the program and MCP server.
The source/development capabilities described below can include optional assets
that are unavailable in a runtime deployment.

The new Python **Silicon Copilot** is available as a separate SiCo menu
entry and through **Ask Silicon Copilot...** in Schematic/Layout context menus
and ADE Results/History extensions. Ask opens a PyQt5 quick-input composer through SKILL IPC (Chinese IME and multiline input), then forwards
to the window's bound session; the first submission bootstraps a session when
none exists. Only the user's **New session** action creates an extra conversation.
It provides a three-pane PyQt5 QMainWindow with session history/navigation, a
role-aligned conversation, and report/audit/data tabs. New sessions automatically
read their bound context once and show the tool result. History browsing is read-only.
Fixed targets per task and read-only ADE setup/History tools retain structured
evidence tables. See the [Agent quickstart](docs/AI_ASSISTANT_AGENT_QUICKSTART.md)
for launch and Anthropic/OpenAI API environment or JSON configuration, and the
[implementation status](docs/AI_ASSISTANT_AGENT_IMPLEMENTATION_STATUS.md) for
IC231 test evidence and remaining work. Its local TCP service and tool registry
are currently independent of the CLI/MCP runtime described below. Development
focuses on Agent core/GUI and Virtuoso interaction; future business tools will
connect to this existing MCP ecosystem. The product name is **Silicon Copilot**;
`SICO_*`, Python packages and SKILL entry points keep their existing names.

Silicon Copilot's desktop now uses a prepared frontend session and asynchronous
services for configuration, storage, transport, replay and workbench queries.
Qt owns presentation and page activation; backend publications carry identity and
committed-watermark checks. Production, demo and connect GUI startup share the
background preparation flow. See the [frontend/backend separation plan](docs/COPILOT_STUDIO_FRONTEND_BACKEND_PLAN.md)
for historical FB evidence. Production GUI now attaches to a project-level independent
Agent Service; GUI exit detaches accepted work. See [service operations](docs/COPILOT_STUDIO_AGENT_SERVICE_OPERATIONS.md).

This tool launches Codex CLI or Claude Code in an external terminal from the
Virtuoso CIW, Layout, or Schematic SiCo menu. Either agent can query the
current session, execute bounded SKILL, launch bounded workflows, and manage
live modeling through the authenticated MCP server:

- `get_context`: read cwd, current cellviews, window, and selection count.
- `inspect_schematic`: read bounded instances, nets, and terminals.
- `snapshot_schematic`, `query_schematic`, and `get_schematic_item`: create an
  immutable JSONL snapshot for a large schematic and retrieve bounded pages or
  exact records from its session-local SQLite index.
- `export_schematic_text`: write a searchable text artifact from a schematic
  snapshot without placing the full design in the model context.
- `inspect_layout`: read bounded layout shapes and instances.
- `inspect_symbol_ports`: read bounded symbol terminals and port ordering.
- `extract_circuit_templates`, `query_circuit_templates`, `get_circuit_template`,
  and `match_circuit_template`: capture and retrieve persistent topology,
  schematic placement, symbol appearance, and available layout instance references.
  See [template MCP v1](docs/CIRCUIT_TEMPLATE_MCP_V1.md) for examples, catalog
  location, qualification results and the optional graph-matching dependency.
  Libraries merge bundled, project (`SICO_CIRCUIT_TEMPLATES_DIR`), and workspace-private
  (`$CWD/.cad/ai/circuit_templates`) references. Extraction and previews write privately;
  see [three library tiers](docs/CIRCUIT_TEMPLATE_LIBRARY_TIERS.md).
- `preview_template_symbol`, `create_template_symbol`, `inspect_template_symbol`:
  consume a persistent template to create a new rectangular symbol for an existing
  saved schematic, with explicit port mapping, confirmed task mode, native geometry
  and interface checks. See [symbol template creation](docs/CIRCUIT_TEMPLATE_SYMBOL_V1.md).
- `prepare_template_circuit`: adapt one stored topology to an explicit generic circuit
  spec using selected project masters, terminal mappings and target parameters. Source
  relative placement and provenance follow the spec/geometry previews and creation
  preparation through `template_use`. See [circuit template adapter](docs/CIRCUIT_TEMPLATE_CIRCUIT_V1.md).
- `list_libraries` and `inspect_library`: inspect visible library metadata.
- `list_windows`: inspect open Virtuoso windows and cellviews.
- `tool_help`: use the bundled 248-directory Virtuoso index to narrow topics,
  discover Cadence installations from PATH, search installed HTML
  manuals under `doc`, and read version-specific pages and local chapter links.
  See [tool_help MCP](docs/TOOL_HELP_MCP.md).
- `Search`: search local text or file names using bundled ripgrep, with bounded
  results and no dependency on system `rg`. See [Search MCP](docs/SEARCH_MCP.md).
- `search_skill_api` and `get_skill_api`: query the bundled offline Cadence
  SKILL API reference without contacting Virtuoso.
- `check_skill`: check source text or an existing `.il`/`.ils` file locally
  for delimiter errors and the `procedure(...)` function-definition policy.
- `eval_skill`: evaluate one classic SKILL expression.
- `load_skill_file`: load one existing `.il` or `.ils` source file.
- `run_aivw_recipe`: run one approved AIVW recipe dependency closure from the
  controller-delegated workspace without exposing the session token to agent
  shell commands.
- `run_rce`, `run_drc`, `run_lvs`, `stream_gds`, `export_cdl`,
  `analyze_dspf`, and `generate_lef`: start repository CAD flows.
- `get_cad_flow_status`: read an asynchronous CAD flow job.
- `create_maestro_testbench`, `run_maestro_simulation`, and
  `stop_maestro_simulation`: manage a bounded Maestro workflow.
- `get_maestro_simulation_status`: read an asynchronous Maestro run.
- `list_measurement_capabilities`, `get_measurement_capability`: discover 17 implemented generic
  measurements with current tool/operation schemas, unit/type rules, failure conditions and
  catalog revision, offline without project defaults. See
  [measurement capability catalog](docs/CIRCUIT_MCP_PHASE26_MEASUREMENT_CATALOG.md).
- `read_maestro_waveform`, `query_waveform`, `measure_waveform`: capture an exact
  history/test/corner/point signal and apply explicit sampling, extrema, window
  mean/RMS and crossing recipes offline. See [waveform recipes](docs/CIRCUIT_MCP_PHASE20_WAVEFORM.md).
- `measure_waveform_pair`: compute explicit-event delay and real sampled/RMS output-to-input
  ratios with source-coordinate checks and denominator limits. See
  [paired waveform recipes](docs/CIRCUIT_MCP_PHASE21_WAVEFORM_PAIR.md).
- `evaluate_waveform_specs`, `query_waveform_spec_report`: qualify combined real/AC
  measurements against explicit coverage, full recipes and unit-aware limits; retain
  failure/missing/error details and worst-point provenance. See
  [unified AC specifications and generality audit](docs/CIRCUIT_MCP_PHASE25_AC_SPECS.md).
- `read_maestro_ac_waveform`, `query_ac_waveform`, `measure_ac_waveform`,
  `measure_ac_transfer`: preserve exact-history complex AC samples and measure explicit
  frequency magnitude, referenced amplitude dB and principal phase, including complex
  output/input with denominator limits. See [complex AC](docs/CIRCUIT_MCP_PHASE23_COMPLEX_AC.md).
- `measure_ac_response`, `query_ac_response`: measure explicit lowpass cutoff and declared-loop
  phase margin with full-window denominator checks, selected crossings and phase-branch provenance.
  See [AC response measurements](docs/CIRCUIT_MCP_PHASE24_AC_RESPONSE.md).
- `begin_circuit_task` and the fixed RC recipe tools: retain the user's initial
  foreground/background choice and create/run/read the RC acceptance workflow.
- `preview_gpdk_gate`, `create_gpdk_gate`, and `inspect_gpdk_gate`: create and
  verify qualified gpdk045 v3.5 INV/NAND2 schematic/symbol/testbench targets.
  See [phase 3](docs/CIRCUIT_MCP_PHASE3.md) for scope, PDK fingerprints and
  actual Maestro/Spectre acceptance evidence.
- `live_model_start`, `live_model_source_changed`, `live_model_status`,
  `live_model_events`, `live_model_stop`, `live_model_approve_publish`, and
  `live_model_cancel_publish`: manage the bounded live-model session.

The interactive AI Assistant launch configuration enables its advertised MCP tools. Codex
0.156.1 uses the native Responses namespace format; Claude uses its tool
allowlist to avoid repeated prompts. The server still requires the authenticated per-session
socket, exposes only the documented tool allowlist, and validates every
argument and path. A mutating call is never retried after a timeout or
disconnect because it may already have executed.

SKILL submitted through `eval_skill`, its internal native-output counterpart,
or `load_skill_file` receives automatic preflight checks before dispatch to
Virtuoso. Functions must be defined with `procedure(name(args) ...)`; alternative
function/macro definitions are rejected. Each `(` must close with `)`; `]` is
reserved for indexing. Strings, escaped symbol characters, `;` comments and
non-nested `/* ... */` comments are handled separately. Preflight failures carry
`executed: false`, a source SHA256, and bounded diagnostics with line/column
locations. Passing these checks does not establish full syntax or logic
correctness. See [SKILL preflight](docs/skill-preflight.md) for the contract,
examples, limits and compatibility with existing code.

## Runtime Architecture

```text
Virtuoso menu/form
  -> SKILL ipcBeginProcess
  -> optional interactive LSF stdio proxy
  -> Python 3.9+ session controller
  -> external Python/PyQt5/QTermWidget terminal
  -> bundled Codex CLI 0.156.1 or site-managed Claude Code 2.1.198
  -> stdio MCP helper
  -> authenticated private Unix socket
  -> controller JSONL
  -> current Virtuoso SKILL thread
```

Qt is never loaded into the Virtuoso process. The maintained terminal is a
separate Python process using the production PyQt5 and Qt 5 runtime with the
QTermWidget 1.4 binding supplied by this release. The session controller and
MCP helper use the Python standard library for the bridge and do not import PyQt,
`pysqlite3`, or Cadence Python modules; only the external terminal frontend
imports PyQt5. Python's optional `_sqlite3` extension is used when available
but is not required. The compressed reference fallback uses Python's optional
`zlib` extension. If neither extension is available, only the two local
SKILL reference tools report unavailable; the session and live Virtuoso tools still
start. Qt 6 and PyQt6 are not supported.

Persistent circuit template catalogs require Python's `sqlite3` extension. Their
optional graph matcher requires `networkx==3.2.1` through `cadai[templates]`;
capture, query and preview do not import NetworkX. `SICO_CIRCUIT_TEMPLATES_DIR` selects
the persistent catalog directory and is forwarded to Codex and Claude MCP sessions.

## Production Requirements

- Linux with Virtuoso and `ipcBeginProcess`.
- Production Python 3.9 or newer selected by `CAD_PYTHON`, or by
  `CAD_PYTHON_ROOT/bin/python3` when `CAD_PYTHON` is unset. The same path must
  be available on local and LSF execution hosts.
- PyQt5, `PyQt5.sip`, and their Qt 5 libraries, XCB/input-context plugins, and
  matching ICU installed in the production Python tree. User-site PyQt wheels
  and PyQt6/Qt 6 are not supported.
- Python `zlib` support when `_sqlite3` is unavailable and the offline SKILL
  reference tools must remain usable.
- QTermWidget exactly 1.4.0. The release supplies its platform binding,
  `libqtermwidget5.so.1`, resources, and license texts, but does not bundle
  Python, PyQt5, Qt, ICU, or Qt plugins.
- The release bundles OpenAI Codex CLI `0.156.1` as a native
  `x86_64-unknown-linux-musl` executable. Claude Code `2.1.198` (or a
  compatible site-managed release) remains optional.
- A writable project workspace selected in the launch form.

Codex is pinned to `0.156.1`. MCP tools use the native Responses namespace
format and the app-server owns thread/turn lifecycle. The optional HTTP/SSE
wrapper is used only when a configured gateway needs flat tool names. The old
0.114 flat compatibility format is retired. See
[the runtime compatibility notes](docs/codex-0.156-compatibility.md) for
source references, checksums, and local verification commands.

The qualified site setup is:

```bash
export CAD_PYTHON_ROOT=/software/pkgs/python/3.9.13
export CAD_PYTHON="$CAD_PYTHON_ROOT/bin/python3"
```

The AI Assistant provides one terminal GUI implementation: the Python/PyQt5
frontend. The native QTermWidget binding and shared library are backend
dependencies of that frontend, not a second GUI implementation. The source and
release do not provide a C++ terminal executable or fallback path.

## Offline Cadence SKILL Reference

The release includes two equivalent, read-only reference payloads with 8,261
indexed APIs and extracted text for 8,240 source documents:
`reference/cadence-skill/skill_api.sqlite3` and
`reference/cadence-skill/skill_api.json.gz`. The MCP helper prefers SQLite and
automatically reads the compressed JSON fallback when the selected Python does
not provide `_sqlite3`. `search_skill_api` supports bounded name, signature,
and text search; `get_skill_api` returns exact entries. Neither tool sends a
request to Virtuoso.

These payloads contain text extracted from Cadence product documentation and
are subject to the notice in `reference/cadence-skill/NOTICE.md`. Distribute them
only in environments authorized to access the corresponding documentation.
The raw HTML/PDF site is not packaged. Authorized sites can regenerate both
payloads with `reference/cadence-skill/build_reference.py`, or set
`CAD_AI_SKILL_REFERENCE_ROOT` to a separately managed directory containing
both files. The installed reference directory also includes
`build_reference.py` for authorized regeneration. Release packaging rejects a
missing, empty, symlinked, corrupt-gzip, or platform-mismatched payload.

## Build the QTermWidget Runtime

The release build uses the pinned QTermWidget `1.4.0` and lxqt-build-tools
`0.13.0` commits. Build one `QTermWidget.abi3.so` and
`libqtermwidget5.so.1` pair for each target platform against a Qt 5 SDK whose
minor ABI matches production PyQt5. RHEL 7 and RHEL 8 symbol limits are
validated independently.

```bash
cd /path/to/cad
export QTERMWIDGET_PREFIX=/path/to/patched/qtermwidget-prefix
export QTERMWIDGET_BINDING="$PWD/.cad/runtime/ai/python/QTermWidget.abi3.so"
```

The pinned native Codex executable lives at
`tools/ai/runtime/codex/x86_64-unknown-linux-musl/bin/codex` beside its
`codex-code-mode-host`, and CMake installs that tree by default. The importer
reproduces an identical tree under `.cad` when an isolated input is preferred.
It accepts either the official archive or the matching native ELF (for example,
the ELF already present in an audited npm package); it verifies the release
SHA256, static-PIE shape, and `codex --version` before installing:

```bash
mkdir -p "$PWD/.cad"
tools/ai/terminal/scripts/import_codex_runtime.sh \
  "$PWD/.cad/codex-runtime" \
  /path/to/codex-x86_64-unknown-linux-musl.tar.gz
```

Then configure and install the stage. CMake rechecks the executable — the
in-tree runtime, or the copy named by `CAD_AI_CODEX_EXECUTABLE` — against the
committed lock file before copying it into the release tree:

```bash
cmake -S tools/ai/terminal -B "$PWD/.cad/terminal-install" \
  -DCMAKE_INSTALL_PREFIX="$PWD/.cad/stage/ai" \
  -DCAD_AI_QTERMWIDGET_ROOT="$QTERMWIDGET_PREFIX" \
  -DCAD_AI_QTERMWIDGET_PYTHON_MODULE="$QTERMWIDGET_BINDING"
cmake --install "$PWD/.cad/terminal-install"
```

Then reduce the pinned runtime to its shipped identity before verification and
archiving: `tools/ai/terminal/scripts/strip_codex_runtime.sh <stage>` strips both
executables to the `release_*` values recorded in the lock and refuses any
binary that is neither the pinned upstream one nor the recorded shipped one.
`verify_release_abi.sh` and the release inventory both check that stripped
identity, while CMake keeps checking the pinned upstream input.

The `x86_64-unknown-linux-musl` ELF is deliberately stored once in the
combined release. It has no `PT_INTERP`, no `DT_NEEDED`, and no glibc,
libstdc++, or Node.js dependency, so the same file runs in RHEL 7 and RHEL 8
userspaces. RHEL compatibility still applies to the two QTermWidget pairs;
the release verifier enforces their separate RHEL 7 and RHEL 8 symbol limits.
The pinned Codex runtime is stored once at
`tools/ai/runtime/codex/x86_64-unknown-linux-musl`; the reproducible import step
recreates the identical tree under `.cad` for an isolated build input. The
distributed package includes the Apache-2.0 license at
`tools/ai/LICENSES/openai-codex-Apache-2.0.txt`.

The binding has old-style `$ORIGIN/../lib` RPATH and resolves only the adjacent
QTermWidget library from the release. PyQt5, Qt, ICU, and Qt plugins continue to
resolve from the qualified production Python installation. See
[the terminal release guide](terminal/README.md) and
[binding build notes](terminal/pyqt/README.md) for platform build, packaging,
and verification commands.

QTermWidget contains GPL-2.0-or-later code. Any distributed terminal binary
and bundled QTermWidget library must satisfy the corresponding license and
source-availability obligations.

## Install Layout

The repository is installed as the read-only `$CAD_HOME/tools` tree. A combined
RHEL 7/RHEL 8 release stores the common Python frontend and resources once and
keeps only the QTermWidget binary pair per platform:

```text
$CAD_HOME/tools/
  cadToolRegister.il
  tools/utility/menu.toml
  tools/utility/menu.generated.il
  ai/
    README.md
    bin/aiassistant                 # stable controller CLI
    bin/sico-ai-pinyin               # reusable LSF pinyin command wrapper
    bin/sico-ai-terminal             # RHEL-family runtime dispatcher
    bin/sico-ai-terminal-pyqt        # common production Python launcher
    runtime/
      codex/
        x86_64-unknown-linux-musl/
          bin/codex                  # static native Codex CLI
          MANIFEST.txt               # pinned version and checksums
      ripgrep/
        x86_64-unknown-linux-musl/
          bin/rg                     # shared static ripgrep 15.2.0
          MANIFEST.txt
      rhel7/
        python/QTermWidget.abi3.so
        lib/libqtermwidget5.so.1
      rhel8/
        python/QTermWidget.abi3.so
        lib/libqtermwidget5.so.1
    share/logo.png
    share/qtermwidget5/
    share/licenses/qtermwidget5/
    python/sico-ai
    python/cadai/
    python/cad_ai_terminal/
    reference/cadence-skill/
      skill_api.sqlite3
      skill_api.json.gz
      README.md
      NOTICE.md
      build_reference.py
    skill/*.il
    skill++/AI.ils
    skills/
      cadence-skill/SKILL.md
      virtuoso-assistant/SKILL.md
    LICENSES/openai-codex-Apache-2.0.txt
    docs/bridge-lite-removal.md
    docs/pyqt-terminal.md
```

Runtime files and Codex state are created only in user-owned runtime/state
directories. The installed tree does not need to be writable after release.

Regenerate the checked-in menu data while preparing a release:

```bash
/usr/bin/python3 tools/utility/menu_to_skill.py \
  tools/utility/menu.toml tools/utility/menu.generated.il
```

## Dedicated Codex Login

The controller does not read, copy, or link `~/.codex`. It sets `CODEX_HOME`
to a dedicated directory with mode `0700`:

```text
${XDG_STATE_HOME}/cad-codex/codex-home
```

When `XDG_STATE_HOME` is unset, the default is:

```text
~/.local/state/cad-codex/codex-home
```

Create and authenticate that home once before normal use. The bundled native
CLI is used automatically; no Node.js/npm installation or `PATH` entry is
needed for Codex:

```bash
export CODEX_HOME="${XDG_STATE_HOME:-$HOME/.local/state}/cad-codex/codex-home"
mkdir -p "$CODEX_HOME"
chmod 700 "$CODEX_HOME"
/path/to/release/ai/runtime/codex/x86_64-unknown-linux-musl/bin/codex login
```

Replace `/path/to/release/ai` with the installed release's absolute `ai/`
directory. The controller uses that bundled executable for later sessions;
no `codex` command in `PATH` is required.

The first terminal can also be used to complete the Codex login flow. Do not
copy `~/.codex/auth.json` into this home.

At the start of a Codex session, the controller links the release-owned
`skills/` directory into `CODEX_HOME/skills/sico-cad-ai`. Codex discovers the
contained `cadence-skill` and `virtuoso-assistant` skills without replacing any
other user-installed skills. Claude receives the same
Virtuoso MCP tools; the packaged skill discovery is Codex-specific.

## Claude Authentication

Claude uses the normal Claude Code authentication available to the user who
started Virtuoso. Authenticate once with `claude auth login`, or configure a
supported Anthropic API environment before starting Virtuoso. The controller
does not copy Claude credentials into the private MCP configuration; that file
contains only the per-session cdns-ipc token.

## Virtuoso Use

Set `CAD_HOME` before Virtuoso starts and load the normal CAD registration
entry. No AI Assistant-specific `.cdsinit` load is required.

```skill
cadHome=(getShellEnvVar "CAD_HOME")
(load (strcat cadHome "/tools/cadToolRegister.il"))
```

Choose `SiCo -> AI Assistant`, select `Codex` or `Claude`, choose an existing
workspace directory, and press `Launch`; Claude is selected by default. Each
Virtuoso process owns one AI Assistant controller and terminal window shared by
its CIW, Layout, and Schematic views. Choosing the menu again while that window
is active restores and raises it.

Right-click inside a terminal and choose `New Session`, or press
`Ctrl+Shift+T`, to start another independent agent in a new tab. A window holds
at most eight tabs. Every tab uses the agent selected when the window was
launched and shares its workspace and Virtuoso controller; agent processes and
PTYs remain independent. Virtuoso requests from the tabs are serialized by the
controller.

Double-click a tab title, right-click the tab and choose `Rename Session`, or
press `F2` to rename the current session. Enter or moving focus away accepts the
name; Escape or an empty name keeps the previous title. Session names are local
to the current terminal window.

Closing a running tab asks before ending that tab alone. Closing the window
while agents are running offers `Exit Sessions`, `Minimize`, and `Cancel`.
Minimize and Cancel preserve the window, controller, MCP bridge, and all agent
tabs. Normal termination removes a completed tab when another session remains.
The last completed tab stays visible with an `Exited` status until it is closed,
so an agent launcher error remains readable instead of making the window flash
and disappear. Virtuoso shutdown or transport loss closes the complete owned
window so no controller or agent is left orphaned.

The terminal context menu also provides Copy, Paste, Select All, and Find.
Select All includes retained scrollback and is also available with
`Ctrl+Shift+A`.

`eval_skill` accepts one expression up to 65,536 UTF-8 bytes. Use
`load_skill_file` for multi-form sources. A returned value and the synchronous
`poport`/`woport`/`errport` output are bounded and include explicit truncation
flags. The output capture is temporary and unwind-protected: it includes
buffered `warn()` messages and SKILL error text, then restores the original
ports. It is not a reader for historical CIW contents and does not include
output emitted later by asynchronous jobs.

For example, `eval_skill` with `code` set to
`progn(printf("ready\n") warn("check device\n") 1+2)` returns:

```json
{
  "ok": true,
  "value": "3",
  "value_truncated": false,
  "output": "ready\n*WARNING* check device\n",
  "output_truncated": false,
  "output_capture": "skill_ports"
}
```

Read this response directly instead of polling `CDS.log`. Both result text and
output are limited to 65,536 UTF-8 bytes, with earlier truncation when JSON
escaping would exceed the SiCo response budget. Truncation preserves UTF-8
boundaries. On failure, `error`/`error_truncated` replace `value`/`value_truncated`;
`output` still includes preceding prints and the captured error messages.
The legacy Assistant controller wraps a failed evaluator payload in
`{"code":"skill_error", "message":..., "data":...}`; read `data.output` in
that case. SiCo returns the evaluator fields directly. For a successful legacy
Assistant response with `spooled: true`, `artifact.path` contains the result JSON
and `preview` contains its leading output. Neither case requires reading
`CDS.log`.

Only output routed through the three SKILL ports is covered; explicit
`stdout`/`stderr`, direct CIW GUI writes and subprocess logs may bypass them.
Use dedicated workflow tools for asynchronous ADE/GUI operations that retain
ports beyond a call. Their internal `eval_skill_native` path keeps the original
ports and reports `output_capture: "ciw"`.

Silicon Copilot's finite `circuit_call` path also captures synchronous schematic,
CDF update, library creation, template symbol, and config diagnostics. Their
preparation/inspection tools return `output`, `output_truncated`, and
`output_capture: "skill_ports"`, including `get_symbol_binding`. For the related
`execute_circuit_operation` writes, read those fields in `last_response`; a dispatch
exception keeps them in `dispatch_error`. `get_circuit_operation(refresh=false)`
can read the recorded write output without accessing Virtuoso. Check each
operation's saved/check/readback receipt to establish success; schematics require
`saved`, `readback_verified`, and `check: [0, 0]`. Diagnostic text alone does not prove it.

`list_project_extensions` and synchronous operation preflight also capture their
diagnostics. Ordinary `inspect_schematic`, `inspect_layout`, `inspect_symbol_ports`,
`inspect_library`, and `list_libraries` preserve output from their existing
capturing evaluator when decoding the returned JSON, including failed reads.
Read `output` before looking for the same synchronous diagnostic in CDS.log.

`probe_cdf_parameters` captures its synchronous preflight, parameter callbacks,
save/readback, and restoration diagnostics with the same three output fields.
The bounded output is retained in the probe outcome and its evidence artifact;
both `get_cdf_probe` and an identical repeated request return that original output,
including failures, without rerunning callbacks. The existing CDF chunk transport
preserves it in both Assistant and SiCo. New probes always create their diagnostic
schematic through `dbOpenCellViewByType` in the current workspace's `SicoTest` library.
The library is created/reused in background; even foreground design tasks open no
probe window and keep their original presentation preference. A conflicting
`SicoTest` path is rejected. Only target design views and directly related
TB/maestro/layout deliverables follow the user's foreground/background choice.
Probes retained before capture was available report
`output_capture: "ciw"`; historical output cannot be recovered retroactively.

`inspect_cdf` also preserves its synchronous output through the CDF chunk
transport and artifact. PDK collection/binding calls aggregate captured output
across native reads without including it in device or geometry identities.
Snapshot/template artifact conversion and legacy CAD flow decoding preserve
diagnostics on success and failure. See the
[complete tool output audit](docs/TOOL_OUTPUT_CAPTURE_AUDIT.md) for all 144
registered tools and the remaining native/async coverage differences.

Each call owns an in-memory output port and returns its text through the existing
framed SiCo pipe. Window initialization uses the original ports so deferred GUI
work cannot retain a closed capture port. Simulation and ADE still use their
native ports. Warnings deliberately consumed by native routines are not recovered;
the final pending warning is flushed before capture ends. This extension
applies to the SiCo circuit adapter and the shared CDF probe; the legacy terminal creation path continues
to use `eval_skill_native`. See
[circuit output capture](docs/CIRCUIT_OUTPUT_CAPTURE.md) for scope and validation.

`run_aivw_recipe` is the approved orchestration entry for the generic mixed-signal
workbench. It accepts only a checked-in recipe ID, target gate, profile, and bounded
timeout. The MCP helper delegates its existing Unix-socket credentials only to the
short-lived AIVW process group; the token is excluded from normal agent shell
commands and from the tool result. Generic recipe execution cannot publish a
Virtuoso view. A request through `structure` first runs the authenticated,
saved-state snapshot and starts the registered `cadence.si` child only if that
dependency passes. Controller `CAD_AI_*`/`CAD_CODEX_*` variables are removed
from non-IPC executor contexts, so Cadence netlisting and Xcelium children do
not receive the private session credential.

Dedicated validated tools can start the repository's RCE, DRC, LVS,
Stream GDS, Export CDL, DSPF Analyzer, and LEF Generator flows. Separate
Maestro tools create a setup around an existing schematic testbench and manage
an asynchronous simulation. See [CAD Workflow And Maestro Tools](docs/workflow-tools.md)
for the input, status, compatibility, and sign-off boundaries.

The Codex executable is resolved in this order:

1. the `--codex` command-line option
2. `CAD_CODEX_CLI`
3. the release's bundled native Codex runtime
4. `$CAD_HOME/bin/codex`
5. `codex` from `PATH`

The bundled Codex runtime is the official `x86_64-unknown-linux-musl` static
PIE executable and is shared by RHEL 7 and RHEL 8 releases. It does not need
Node.js, npm, glibc, libstdc++, or a sibling package directory. The controller
searches the resolved `bin/sico-ai-terminal` release first, then an absolute
`CAD_AI_RELEASE_ROOT`, then its own source/install tree. Skills and the MCP
helper remain paired with the controller's source tree. A missing or
non-executable override is skipped without disabling the remaining fallbacks.

Claude retains its existing site-managed order: `$CAD_HOME/bin/claude`, then
`--claude` (or `CAD_CLAUDE_CLI` when the option is unset), then `claude` from
`PATH`. Claude is not bundled.

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `CAD_PYTHON` | Production Python 3.9+ executable used by the controller and PyQt5 terminal. |
| `CAD_PYTHON_ROOT` | Production Python prefix; its `bin/python3` is used when `CAD_PYTHON` is unset. |
| `CAD_CODEX_CLI` | Optional Codex executable override; the bundled native runtime is the default. |
| `CAD_CLAUDE_CLI` | Optional Claude fallback when `$CAD_HOME/bin/claude` is unavailable. |
| `NODEJS_HOME` | Optional Node.js prefix or `bin` directory used when an npm Codex launcher cannot find `node` in `PATH`. |
| `CAD_AI_TERMINAL` | Optional agent-neutral terminal executable override; the installed default is the PyQt5 dispatcher. |
| `CAD_AI_PYQT_ROOT` | Optional absolute Python frontend root for source/synchronized testing. It is strict and never falls back to an installed frontend. |
| `CAD_AI_PLATFORM` | Optional `rhel7` or `rhel8` override for the combined-release dispatcher. |
| `CAD_AI_QTERMWIDGET_RUNTIME` | Internal absolute path selected by the dispatcher for the platform QTermWidget binding/library pair; users must not set it. |
| `CAD_AI_QTERMWIDGET_DATA_PATH` | Internal QTermWidget resource root selected by the dispatcher; users must not set it. |
| `CAD_CODEX_HOME` | Optional dedicated Codex state directory override. |
| `CAD_CODEX_MODEL` | Optional model ID passed to Codex with `--model`. |
| `CAD_CLAUDE_MODEL` | Optional model ID passed to Claude with `--model`. |
| `CAD_CODEX_API_BASE_URL` | Optional OpenAI-compatible Responses API base URL. |
| `CAD_CODEX_API_KEY` | Optional API key read by the environment-configured provider. |
| `CAD_AI_REQUEST_TIMEOUT` | Virtuoso request timeout in seconds. Default: 300. |
| `CAD_AI_MCP_TIMEOUT` | MCP request timeout in seconds. Default: 305. |
| `CAD_AI_PYTHON_ENTRY` | Optional controller entry override for a source or deployment tree. |
| `CAD_AI_RELEASE_ROOT` | Optional absolute `ai/` release root when the controller/source tree and terminal/runtime release are separate; normally inferred from `CAD_AI_TERMINAL`. |
| `CAD_AI_PYTHON_LIBRARY_PATH` | Clean production Python library path used by the controller, terminal, and MCP helper; falls back to `CAD_SYSTEM_LD_LIBRARY_PATH`. |
| `CAD_AI_SKILL_REFERENCE_ROOT` | Optional absolute directory containing alternate `skill_api.sqlite3` and `skill_api.json.gz` payloads. |
| `PROJ_AI_LSF_EXE` | Optional interactive LSF executable or site wrapper; unset keeps the complete session local. |
| `PROJ_AI_LSF_ARGS` | Arguments passed to `PROJ_AI_LSF_EXE` before the remote session command. |

`CAD_AI_RUNTIME`, `CAD_AI_SOCKET`, `CAD_AI_SPOOL`, `CAD_AI_TOKEN`,
`CAD_AI_WORKSPACE`, and `CAD_AI_CONTROL_FD` are internal per-session values. Do
not set them in a site environment.

### Remote LSF Session

Set the project launch variables before starting Virtuoso to move the complete
AI Assistant session to an LSF execution host:

```bash
export PROJ_AI_LSF_EXE=/path/to/bsub
# Enable -XF only after the managed SSH host-key check below passes without prompts.
export PROJ_AI_LSF_ARGS="-I -XF -q ai"
# Optional when automatic locale probing must use a site-specific name:
# export CAD_AI_UTF8_LOCALE=en_US.utf8
```

`PROJ_AI_LSF_EXE` must be one executable name or path, not a shell fragment.
`PROJ_AI_LSF_ARGS` uses POSIX quoting to split arguments, but does not perform
shell expansion, variable substitution, globbing, or command substitution.
The launcher replaces itself with the configured executable and appends one
shell-quoted Python `sico-ai session` command string. Paths and arguments remain
literal when the LSF execution host's shell starts that command. The two
project variables are removed from the submitted environment after they are
consumed.

The LSF command must run synchronously in interactive mode and preserve
bidirectional stdin/stdout until the remote controller exits. With standard
`bsub`, `PROJ_AI_LSF_ARGS` must start with plain `-I`; the production X11 path
also uses `-XF` (or a site wrapper with an equivalent approved X11 policy). The `-Is` and `-Ip`
pseudo-terminal modes are rejected because terminal echo and line-ending
translation can corrupt the Virtuoso JSONL control channel. The `-i`, `-is`,
`-o`, and `-oo` redirection options are also rejected because that channel owns
standard input and output. An ordinary
batch submission returns before `session.ready` and cannot carry that channel.
A site wrapper must also propagate stdin EOF and TERM to the remote job so
normal Virtuoso shutdown still retires the complete session tree.

Local startup keeps the existing five-second `session.ready` deadline. An LSF
handoff uses a 300-second deadline so normal queue and dispatch latency does
not retire the interactive job before its controller starts.

The Python entry, launch directory, workspace, terminal runtime, and selected
agent must be available at the same paths on the execution host. The launch
directory and workspace must be writable shared filesystems with the same
numeric user ID on both hosts. A remote session creates a private
`$CWD/.cad/ai/.cad-ai-runtime-<uid>/session-*` bridge directory below the launch
directory. Only the bounded SKILL source and result files
that Virtuoso must read or write cross this bridge. The session directory is
removed with a non-recursive `rmdir` on a clean exit; the per-user container
and its protective `.gitignore` remain for later sessions. A crash or a request
whose execution status is unknown can leave the session directory for manual
diagnosis.

The session token, MCP configuration, MCP large-payload spool, large result
artifacts, Unix socket, and all other runtime files use private permissions
below the launch directory's `.cad/ai`. LSF must preserve the X11 variables
described below so the remotely running terminal opens on the local desktop.
The controller, MCP helper, private Unix socket, PTY, terminal, and agent all
stay together on the execution host; no network listener is opened.

All AI child processes receive `CAD_TEMP_DIR`, `TMPDIR`, `TMP`, `TEMP`, and
`SQLITE_TMPDIR` pointing at the launch directory's `.cad/ai`. Their cache and XDG
runtime roots are `.cad/ai/cache` and the private `0700` `.cad/ai/runtime`. A
legacy `CAD_TEMP_DIR=.../.cad` is normalized to `.../.cad/ai`. Shared mount
aliases are preserved instead of resolving to host-only filesystem paths. The
selected workspace does not change these paths. The persistent Codex login
home remains in its configured state location.

The result reader allows up to two seconds for acknowledged NAS results to
become visible (`ENOENT`/`ESTALE` only). It retries the file read, never the
Virtuoso operation. File type, ownership and size checks remain active; an
actual SKILL failure without a result file preserves its original diagnostic.

Both Codex and Claude receive startup instructions identifying the current
Virtuoso MCP session, requiring context/inspection first and preferring
dedicated tools and SKILL for design work. Supporting Python scripts use
`"$CAD_PYTHON" -s`; the selected interpreter's directory leads the child PATH.
Codex also sets `CAD_PYTHON` explicitly in its shell environment policy.

Before sanitizing Python/Qt library settings or handing off to LSF, the
launcher captures the source Virtuoso environment into these exported values:

| Source variable | Value available on the agent node |
| --- | --- |
| `PATH` | `CAD_VIRTUOSO_PATH` |
| `LD_LIBRARY_PATH` | `CAD_VIRTUOSO_LD_LIBRARY_PATH` |
| `PYTHONPATH` | `CAD_VIRTUOSO_PYTHONPATH` |
| `MODULEPATH` | `CAD_VIRTUOSO_MODULEPATH` |
| `LOADEDMODULES` | `CAD_VIRTUOSO_LOADEDMODULES` |
| `_LMFILES_` | `CAD_VIRTUOSO__LMFILES_` |
| `MODULESHOME` | `CAD_VIRTUOSO_MODULESHOME` |
| `LMOD_CMD` | `CAD_VIRTUOSO_LMOD_CMD` |

These values describe the environment inherited by Virtuoso from its login or
EDA node, not later changes in an unrelated login shell. LSF site export policy
must retain the `CAD_VIRTUOSO_*` variables. Site prologs must not overwrite them.
Additional exported settings (for example simulator/license roots) can be read
from the live source process with `eval_skill` and `getShellEnvVar("NAME")`.
Do not assume that a `module` shell function is available on the agent node.
For an authorized standalone EDA job, restore the captured search paths inside
the EDA job submitted through `bsub`, or initialize the site's module system
there and load the recorded modules. The assistant terminal keeps its production
Python/Qt environment; Cadence's original library paths are available as data.

SiCo main windows, quick composers and terminals share the native-compilable
`sico_pinyin.input_session` lifecycle before creating QApplication. Local sessions
retain the desktop IME; positive `LSB_JOBID` sessions get private IBus/libpinyin.
The public `bin/sico-ai-pinyin -- COMMAND [ARG ...]` entry also wraps other GUIs.
See [Pinyin input](docs/PINYIN_INPUT.md) for host dependencies, failure behavior,
process ownership and real input acceptance.

#### User-managed SSH Trust for `-XF`

LSF handles `-XF` through an SSH command before the remote Python session or Qt
terminal starts. An authenticity prompt for
`127.0.0.1 (<no hostip for proxy command>)` therefore comes from the LSF
SSH/ProxyCommand path, not from the assistant. The loopback
name is a logical proxy target and does not identify the real SSH endpoint.

The production site does not allow users to change LSF configuration. For a
standard `bsub -XF` handoff, the launcher supplies `ssh -X -n` with
`BatchMode=yes` and `StrictHostKeyChecking=yes`; an existing site-specific
`LSB_SSH_XFORWARD_CMD` remains unchanged. Configure OpenSSH trust only in the
submitting user's home on the login/submission host. This requires no change
to `lsf.conf`, queue definitions, or system SSH files. Obtain the complete host
public key through an independent trusted channel and verify its SHA256
fingerprint; a fingerprint cannot be converted back into the public key.
IBM requires `ssh -X` from the submission host to every execution host to work
outside LSF before `-XF` is used. Host trust and non-interactive user authentication are therefore
separate prerequisites. CAD never stores or supplies an SSH password.

First obtain an execution hostname without `-XF`, then pin the same host while
diagnosing key changes:

```bash
bsub -I -q ai /bin/hostname -f
bsub -I -XF -q ai -m <execution-host> /bin/true
```

A fingerprint must remain stable for repeated jobs on the same execution
host. Different queue nodes normally have different persistent host keys; a
key that changes while the same host is pinned indicates a dynamic proxy or
SSH identity that cannot be pinned safely by the user.

For an account that does not use literal `ssh 127.0.0.1` for a real localhost
connection, isolate the LSF key with a user SSH configuration:

```sshconfig
Host 127.0.0.1
    HostKeyAlias cad-ai-lsf-x11-prod
    UserKnownHostsFile ~/.ssh/cad-ai-lsf-x11_known_hosts
    StrictHostKeyChecking yes
    BatchMode yes
```

Store only independently verified keys in that dedicated file:

```text
cad-ai-lsf-x11-prod ecdsa-sha2-nistp256 <verified-public-key-base64>
```

The `.ssh` directory should be mode `0700`, and the config and key file mode
`0600`. Place this block before broader blocks that set the same options, then
use `ssh -G 127.0.0.1` to verify the effective `hostkeyalias`,
`userknownhostsfile`, `stricthostkeychecking`, and `batchmode` values.

This block affects every SSH target whose literal name is `127.0.0.1`. If the
account must also connect to a real localhost, the less isolated fallback is
to leave SSH config unchanged and add the verified key to the default
`~/.ssh/known_hosts` under `127.0.0.1`. That makes real localhost and the LSF
proxy share one trust namespace, so retain existing keys and verify every
additional proxy key separately. A dedicated alias file may contain one
verified key for every approved queue node, but then the alias identifies that
approved key set rather than one real hostname. Replicate the user files on
each submission host when login homes are not shared.

When only an independently approved fingerprint is available, temporarily use
`StrictHostKeyChecking ask` without `BatchMode yes`, then run
`bsub -I -XF -q ai -m <execution-host> /bin/true` outside Virtuoso. Compare the
prompt character by character and answer `yes` manually once. Immediately
locate the written key with `ssh-keygen -F`, recompute its fingerprint with
`ssh-keygen -lf`, and then switch to `StrictHostKeyChecking yes` and
`BatchMode yes`. Accepting before independent verification, `accept-new`, and
automated answers are not acceptable.

The first `ask` bootstrap can encounter multiple SSH connections that were
started concurrently. Accept only prompts whose fingerprint exactly matches
the independently approved value for the currently pinned host. After that
empty job exits, run the same `-m` host again; the second run must have no
host-key prompt. A working `HostKeyAlias` also changes the logical host shown
by a new prompt from `127.0.0.1` to `cad-ai-lsf-x11-prod`.

##### SSH User Authentication for `-XF`

A `Password:` prompt after host-key acceptance is a separate failure: the SSH
server did not accept a public key, GSSAPI credential, or agent identity.
`BatchMode=yes` only turns that prompt into a fast failure. Production must set
up public-key authentication outside Virtuoso; CAD must not receive the
password.

Prefer an existing site-approved key. If none is available, create a dedicated
passphrase-protected RSA key on the login host and load it into the desktop
agent:

```bash
install -d -m 700 "$HOME/.ssh"
ssh-keygen -t rsa -b 3072 -o -a 100 -f "$HOME/.ssh/cad_ai_lsf"
# Run the next line in the same shell that starts Virtuoso only when no desktop agent exists:
# eval "$(ssh-agent -s)"
ssh-add "$HOME/.ssh/cad_ai_lsf"
ssh-add -l
```

Only when using that dedicated key and after confirming that LSF reads the user
SSH configuration, add these lines to the same `Host 127.0.0.1` block. Do not
add them when using an existing site key, because `IdentitiesOnly yes` would
hide other desktop-agent identities:

```sshconfig
    IdentityFile ~/.ssh/cad_ai_lsf
    IdentitiesOnly yes
```

Install only the `.pub` key in the execution user's `authorized_keys`. When
direct SSH is available, one password entry outside Virtuoso can provision it:

```bash
ssh-copy-id -i "$HOME/.ssh/cad_ai_lsf.pub" "$USER@<execution-host>"
```

When login and execution hosts share the same `$HOME`, no initial SSH is
needed: after confirming that shared-home arrangement, add the public key once
on the login host with this idempotent operation:

```bash
install -d -m 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
if ! grep -qxF -f "$HOME/.ssh/cad_ai_lsf.pub" "$HOME/.ssh/authorized_keys"; then
    cat "$HOME/.ssh/cad_ai_lsf.pub" >> "$HOME/.ssh/authorized_keys"
fi
```

Keep `.ssh` mode `0700` and `authorized_keys` mode `0600`. Non-shared homes
require `ssh-copy-id` against every actual queue hostname, never the logical
proxy address `127.0.0.1`. Never copy the private key to an execution host,
CAD tree, or repository, and do not create an unencrypted key merely to
suppress prompts.

Verify both the IBM prerequisite and the LSF path against a pinned host:

```bash
ssh -o BatchMode=yes -X <execution-host> /bin/true
bsub -I -XF -q ai -m <execution-host> /bin/true
```

Virtuoso must inherit the desktop agent's `SSH_AUTH_SOCK`. If direct
passwordless `ssh -X` succeeds but pinned `bsub -XF` still asks for a password,
the LSF SSH path is ignoring the user identity/config/agent or reaches a
different authentication endpoint. Do not use `sshpass`, `expect`, plaintext
environment passwords, or CAD code to bypass it.

The user configuration cannot work if a second run still names `127.0.0.1`, or
if the LSF SSH command overrides it with `-F`, `HostKeyAlias`, or
`UserKnownHostsFile`, runs under another user or HOME, uses non-persistent
storage, or presents unapproved keys for different proxy endpoints. Do not
shadow `ssh` through `PATH`, automate `yes`, use `accept-new`, trust a blind
`ssh-keyscan 127.0.0.1`, or weaken host checking in those cases. Stop using
`-XF` and report that administrator-side identity chain as a blocker.

Run `bsub -I -XF -q ai -m <execution-host> /bin/true` repeatedly before testing
all representative execution hosts. Every run must exit normally without a
question before enabling the assistant. If it does not, inspect the effective
`HOME`, identity/known-hosts paths, agent socket, and real proxy endpoint.

`CAD_CODEX_MODEL` changes the model while retaining the normal dedicated Codex
login and provider. To use a model through a custom API gateway, set the model
and Responses API endpoint before starting Virtuoso:

```bash
export CAD_CODEX_MODEL="gateway-model-id"
export CAD_CODEX_API_BASE_URL="https://gateway.example/v1"
export CAD_CODEX_API_KEY="value-from-your-secret-manager"
```

When only `CAD_CODEX_API_KEY` is set, the endpoint defaults to
`https://api.openai.com/v1`. Omitting the key allows an unauthenticated local
endpoint. The endpoint must implement the OpenAI Responses API, including
streaming, and the selected model must support function/tool calls. A Chat
Completions-only endpoint is not supported by Codex 0.145.

The API key value is never placed in the Codex command line. The Codex process
reads it by variable name, while the existing `CAD_CODEX_*` environment policy
keeps it out of model-launched shell commands and the MCP helper's explicit
environment allowlist. Prefer a secret manager or protected session setup over
storing the key directly in a shared setup file. The base URL is visible in the
process command line, so never embed credentials in its userinfo or query.
Environment changes take effect after Virtuoso is restarted.

Claude API authentication and endpoint selection use Claude Code's standard
environment, including `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL`. OAuth and
keychain authentication remain available because the launcher does not enable
Claude's `--bare` mode. Claude-compatible gateways must implement Anthropic's
Messages API and tool use. Direct provider environments are isolated by agent:
Claude sessions remove `CAD_CODEX_*`, `CODEX_*`, and `OPENAI_*`; Codex sessions
remove `CAD_CLAUDE_*`, `ANTHROPIC_*`, and `CLAUDE_CODE_*`. General AWS/GCP
credentials are retained because other CAD tools may depend on them.

## Verification

Run the Python tests with the production interpreter:

```bash
cd tools/ai/python
"${CAD_PYTHON:-/usr/bin/python3}" -m pytest -q -p no:cacheprovider
"${CAD_PYTHON:-/usr/bin/python3}" -m compileall -q cadai tests
ruff check --no-cache .
```

When `dbAccess` is available, the test suite also loads the SKILL sources twice
and verifies evaluation, error handling, bounded output, file loading, JSON,
and context collection against the installed Cadence runtime.

Run the Virtuoso form probe explicitly on an active display:

```bash
CAD_AI_RUN_VIRTUOSO_PROBE=1 \
  "${CAD_PYTHON:-/usr/bin/python3}" -m pytest -q tests/test_skill_virtuoso.py
```

Run the complete Virtuoso -> controller -> PyQt5 terminal -> MCP session probe
against an installed terminal:

```bash
CAD_AI_RUN_FULL_PROBE=1 \
CAD_AI_TERMINAL=/path/to/ai/bin/sico-ai-terminal \
  "${CAD_PYTHON:-/usr/bin/python3}" -m pytest -q tests/test_skill_virtuoso.py
```

## Security Boundaries

- Each Virtuoso-owned controller uses a private `0700` runtime directory and
  `0600` Unix socket.
- The socket accepts at most eight authenticated MCP clients, releases a slot
  when a client disconnects, and serializes their requests into Virtuoso.
- Codex starts with `workspace-write`, `on-request`, project trust forced to
  `untrusted`, and plugins/apps/Bubblewrap disabled. The pinned Codex release
  executes enabled custom MCP tools directly. Claude allows documented session MCP tools through its
  launch-time allowlist. These settings do not grant arbitrary tools or bypass
  the authenticated MCP server's validation.
- Claude's `--strict-mcp-config` isolates MCP servers only. Normal user/project
  settings, `CLAUDE.md`, hooks, plugins, and built-in file or shell tools can
  still load according to Claude Code's rules. Its default permission mode is
  not an OS sandbox; open only workspaces whose Claude customizations you trust.
- The selected agent still receives its own authentication environment. Prefer
  Codex's dedicated login and Claude OAuth/keychain over long-lived plaintext
  API keys where available.
- Only the documented MCP tools are enabled; unknown tools are rejected.
- `load_skill_file` snapshots a bounded regular file before Virtuoso loads it.
- Unknown execution status is reported and retained for diagnosis, never
  retried automatically.

The client approval dialog is disabled for the documented MCP tools. Review
generated SKILL, configuration paths, target cellviews, and run modes before
using mutating operations; the authenticated server and fixed-shape argument
validation remain the enforcement boundary.

正式 PDK 检索结果现在可通过 `bind_pdk_device` 接入通用静态创建，保留器件来源、项目参数策略和 pin 选择，prepare/execute 自动重新校验。接口、支持边界及下一步见 [第二十七批说明](docs/CIRCUIT_MCP_PHASE27_PDK_BINDING.md)。
