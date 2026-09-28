# Silicon Copilot Agent and Python Host

Silicon Copilot uses the original Codex CLI 0.156.1 `app-server` as its Agent
core, with Python/PyQt5 for the desktop, CAD session control and cdns-ipc.
Environment configuration defaults to `SICO_BACKEND=codex` and
`SICO_PROVIDER=responses`. The optional `SICO_TOOL_FORMAT=flat` wrapper
adapts namespace tool identities for compatible Responses gateways; it does not
translate Anthropic or Chat APIs. The existing Python backend remains available
for those protocols with explicit `SICO_BACKEND=python` or a provider JSON.
An existing environment that selects `openai`/`anthropic` must explicitly select
Python or migrate to Responses. See the [architecture](../../../../README.md).

Both frontends use the original Codex 0.156.1 runtime. `SICO_LEGACY_0114=1`
enables the shared namespace-to-flat Responses wrapper, never a 0.114 executable.
`0` forces native mode; unset/empty preserves existing tool_format configuration,
which defaults to native. An explicit switch overrides the older tool_format
setting and takes effect only for a new session. The wrapper shares the same
0.156.1 history and tool engine; historical 0.114 state is not modified or reused.

The desktop and fixed-target SKILL bridge are available through
**SiCo → Silicon Copilot**. Production GUI, `demo --gui`, `connect --gui` and
`service-gui` all attach to one independent service per project. The service owns
execution and persistence; closing or quitting a GUI detaches it while accepted
work continues. **结束当前会话** explicitly ends one runtime; **停止项目服务**
refuses while work or reconciliation remains. See the
[service operations guide](../../../../README.md).

**Ask Silicon Copilot...** opens a standalone PyQt5 quick-input composer via SKILL IPC,
with Chinese input-method support, multiline text, Enter to send and Shift+Enter for a
new line. Submit forwards to
the source window's bound session, or a recent live session when unbound. Only
when none exists does submitting start the first conversation. Cancel starts
nothing. Durable acceptance binds the window to that session. Additional sessions
are created only by the user's **New session** button, which rebinds the current
source window while preserving other windows' routes. Busy sessions queue up to
16 inputs without replacing the main draft or the running task's source.

The desktop uses a three-column QMainWindow workspace with session history and
tasks on the left, conversation and input in the center, and report/audit/data
centers on the right. Side panels are dockable and their layout is saved.
Reports are explicitly published Markdown with immutable versions and registered
evidence. Older replies remain execution records. Work/stage filters, source/process/
output data, search and paged indexes open passive central detail pages with source
and report backlinks. Existing result archives retain checksum verification;
unavailable evidence is flagged without replacing report text or executing tools.
`set_copilot_stage`, `list_copilot_data`, `publish_copilot_report` and
`prepare_copilot_audit` are local application tools added to the existing business-tool
registry by the backend factory. Codex `item/tool/requestUserInput` is bound to the
evidence-backed Audit center: Copilot records the question, waits without holding the MCP
bridge, validates the target and evidence again, and delivers the reply to the same model
turn. Duplicate reply IDs are idempotent; cancelled, disconnected or invalidated tasks do
not replay a reply. Waveform plots and export remain later work.
See the [workbench contract](../../../../README.md).
New session is a session action;
the quick composer remains a separate PyQt dialog. SessionController owns model
execution outside Qt, including both main-editor and RMB input queues. Cancelling
pauses subsequent inputs until the user resumes the queue. Public events are read
by session/runtime identity and journal cursor. Project history can be viewed
read-only; right-click Continue opens the original conversation ready for input. The service
reuses matching in-memory credentials and settles interrupted local bookkeeping without
replaying old inputs. Continue automatically connects future tools to the current window's
Virtuoso host, or the newest reachable host in the same local project. A durable host
transition preserves historical task targets and the original Codex thread. If no host
is available, chat remains usable and design tools report the missing connection.

The Qt event loop starts before `service/frontend_startup.py` discovers and attaches
to the project service. `StartupLifecycle` owns cancellable waiting and credential
handoff; `StartupReady` publishes only a frontend handle. ProjectSessionOwner owns
session configuration, backend construction, journal/inbox and captured targets.
Credentials remain in necessary process memory; recovery snapshots contain only
bounded non-secret configuration. Attach does not replace captured configuration.

`AssistantWindow` receives `FrontendSession` with a `RemoteFrontend` client and an
identity-only `SessionToken`. Events and queries use bounded subscriptions and
committed watermarks. Page activation, runtime, service generation and target checks
reject stale publications. Workbench queries, filtering and indexing stay in the
service. Qt renders prepared publications; its small layout QSettings and cached
icon/font I/O are the documented exceptions.

A service restart never starts old queues or resends unknown side effects. Right-click
Continue restores the conversation and its current project host without a setup dialog.
The paused recovery protocol remains diagnostic compatibility only, pending migration
of its callers. Read-only history creates no execution backend.

AS-07C removes runtime `DesktopStartup`, `LocalStartup`, `DesktopSessions`,
`FrontendPort` and local query adapters. Historical FB regression composition lives
only under `tests/agent_*_harness.py`, outside the native runtime inventory. The live
command worker cannot construct a local GUI. Bridge callers import `bridge_client`,
`instance_bridge` and `bridge_identity` directly. Typed `ServiceRequest` and
`ServiceChannel.exchange` are the single lifecycle transport contract.

Independent-service scope and evidence are maintained in the
[Agent Service plan](../../../../README.md).
The service accepts current-turn text steering and independent queued tasks with
attachments and frozen turn settings. Steering preserves the captured task/thread/turn;
attachments or changed settings must be submitted as a separate queued task.
Remote binding changes remain unavailable. Non-GUI demo/connect are diagnostic CLI
execution modes. Updating installed modules does not update an already running project
service; see the operations guide before restarting a service with pending work.

The following router controls describe the backend and historical FB frontend
contract. Project-service GUI commands for `cancel_router_request` and
`router_receipt` are not yet exposed; the service explicitly rejects them.
The backend command form remains:

```python
receipt = worker.command(
    session_token, "cancel_router_request", request_id, context=captured_context,
)
```

`request_id` is the 32-hex router request ID from `router.status`, not an inbox input ID
or a tool call ID. The controller supplies the owning session, and the broker
checks the registered target and instance generation. Backend callers observe the
Future; GUI code cannot use this example to bypass the project-service client.

Backend-only compatibility accepts an exact registered `SessionController` in
`SessionTokens.internal_token`; the frontend rejects that input. Remove this
adapter when the backend setup and test callers have migrated to tokens. No UI
caller depends on controller input, including startup and quick-input attachment.

| Receipt | Meaning |
| --- | --- |
| `cancel_requested` | The queued request will not dispatch. Its execution thread publishes one `cancelled_before_start` journal event. |
| `running` | The request has entered the execution slot. Cancellation does not stop SKILL or release the slot. |
| `running_unknown` | The request timed out with an unknown result. Only its matching late reply or generation lifecycle can resolve the slot. |
| `not_owner` | The request belongs to another session or target. |
| `not_found` | No live request has this ID; consult the journal for completed history. |
| `router_unavailable` | The requested generation is closed or has been replaced. |

Cancellation affects one routed request; task-wide **Stop** retains its existing
cooperative cancellation and queue-pause behavior. These APIs and request records
are owned by the service's worker. They do not create a second frontend transport.

The historical task-list action **取消排队请求** targets live queued Virtuoso calls.
It is not currently available through the project-service frontend. Each menu action
captures the session runtime, page selection, bound target and router identity;
the command handler rechecks the current cached request before enqueueing it.
Multiple calls in the same task have separate request actions. In-flight and
acknowledged cancellations stay disabled until the worker projection catches up.
Receipts appear briefly in the status bar; switching sessions, targets or opening
a history preview discards obsolete notices and menu actions. The worker still
validates ownership and handles the race where a queued call has started running.
No running or unknown request can be force-stopped through this action.

The historical **查询路由回执** action reads the latest request for the captured session, instance,
generation and target through the worker command queue. The worker validates live
or archived receipt ownership, formats its JSON and limits display text to 256 KiB
with an explicit truncation notice. Qt receives only the source identity, title
and prepared text. Raw receipts remain available through `router_receipt()`.
Pending queries and open receipt dialogs are invalidated when the page, source or
session changes. History preview cannot query the live session's receipt; stale
success and failure callbacks cannot open a dialog or replace another page's status.

Live session snapshots include a compact `router` projection. The existing metadata
worker samples the local broker every 200 ms; thread-name maintenance keeps its
three-second interval. Qt reads only the cached projection through `read_updates()`.
Unchanged routing state does not advance the session snapshot version. Sampling
does not call Virtuoso, replay journal files or issue RPC, and results captured for
an earlier target or connection generation are discarded.

The projection contains instance/generation/target identity, router state, the
execution-slot owner, the instance queue count, and this session's live requests.
Queued rows carry their current FIFO position. An unknown request retains its owner
and start time after the tool caller has returned; a matching late reply clears the
live state without requiring another event in the observing session. The task list
and status bar show queued/running/unknown states, with request and owner details in
tooltips. History previews remain read-only and do not display live router details.
This is a bounded in-memory view of current routing, not durable execution history;
terminal request events remain in the journal. InstanceBridge runs independently
and serves the project service's captured session targets. The independent
multi-session Agent Service, durable input recovery and explicit crash reconciliation
are complete under AS-01–AS-07; their evidence and limits are in the service plan.

See the [quick start](../../../../docs/html/environment.html) for CIW loading,
API URL/model/Key configuration, and ADE menu coverage. The independent SKILL
entry is `tools/sico/skill++/SICO.ils`; `sicoShow()` captures the current target.

Menu and CLI launches accept centrally supplied `SICO_API_URL` and
`SICO_MODEL`. Codex uses `SICO_PROVIDER=responses` with an exact 0.156.1
executable optionally selected by `SICO_CODEX_CLI`; otherwise it resolves the
shared installed 0.156.1 runtime before the site/PATH candidates. The Python backend implements
`SICO_PROVIDER=anthropic` (legacy default) or `openai` function tools.
Keys come from `SICO_API_KEY`; menu startup asks for a missing key in
a password dialog and keeps it in memory. A partial URL/model pair is an error.
Selection order is explicit `--provider-config`, `SICO_PROVIDER_CONFIG`,
the environment pair, then `<launch directory>/.cad/ai/agent-provider.json`.
JSON contains `provider`, `base_url`, `model`, and `api_key_env`, without inline
secrets. No configuration means an explicitly labelled simulated provider.

The native `sico` entry supports `demo` and `connect`, with or without
`--gui`. `demo` uses explicitly simulated context data. `connect` prints a CIW
invocation that uses a protected context, never SKILL source files. Context
selection is `--context-file /absolute/path/name.cxt`, then `CAD_AI_CONTEXT`, then
`context/cadAiRunCtx.cxt` beside the installed `python` directory. Without a
declared or installed file, the target Virtuoso must already have the site's AI
context loaded. A declared missing context is an error; it does not fall back.
The context must be qualified for that Virtuoso version. Native relay startup
requires the installed ELF `bin/sico` and cannot fall back to `python -m`.
For source development, use `"$SICO_HOME/bin/sico" demo --gui`
after setting `CAD_PYTHON` to the selected interpreter's absolute path.
`history --session <id>` reads
public events after the session writer exits. CLI `demo`/`connect` use the same
configuration order and require their credential in the environment.

The interaction tests use `list_windows`, `inspect_window`, `get_entry_context`,
`get_context`, `read_ade_setup`, `read_ade_history`, and `read_artifact`.
The first three share their schemas, native readers and guidance with AI Assistant.
Shared circuit/project tools use the existing adapters and authorization contracts;
their scope is documented in the [MCP workflow](../../../../README.md).
Startup and each subsequent task refresh the captured source through
`get_entry_context`; older hosts without this capability retain legacy startup.
See the [shared context contract](../../../../README.md).
Context replies travel inline over IPC/TCP without spool files. Each task freezes
its own source; new tasks can use other windows while keeping the conversation.
The running target never follows later GUI focus; stale targets enter
`needs_reconcile`. Accepted input and source snapshots are stored in each session's
`inbox/` without automatic crash replay. Data and runtime
directories are below `.cad/ai/agent`, preserving NAS aliases. The current broker
is loopback-only. Current development focuses on Agent core/GUI and Virtuoso
interactive context/desktop lifecycle. Business tools already reuse the existing
AI Assistant MCP ecosystem; ADE mutations, simulation, analysis and LSF handlers
are not developed again here. Remote service deployment and module restoration
remain later integration work. The SKILL launcher captures eight EDA/module
environment values before sanitizing Qt/Python paths; Codex forwards only those
known snapshots and required runtime values to MCP, without model credentials.

Conversation, tool evidence, field details and input areas support mouse-wheel routing
from both Qt viewport and top-level window events. Scrolling up pauses stream
following; **回到最新** restores the conversation tab and scrolls the currently
selected session to the bottom without changing sessions. Launch clears inherited
EDA XI2 switches.

Source provenance and adaptation boundaries are recorded in the
[source map](../../../../README.md); test evidence and limits
are in the [implementation status](../../../../README.md).
