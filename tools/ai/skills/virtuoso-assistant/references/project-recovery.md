# Project Preflight and Interrupted Operations

Use this reference for a production circuit creation/simulation task or when a
generic operation loses its response. Keep the user's confirmed foreground or
background choice; a reconnect is not a new presentation decision.

## Before Execution

Call `preflight_circuit_project` with explicit target intents (`create` or `read`),
view types, expected library paths, project/run/temp directories, minimum free
bytes/inodes, listed model files and optionally an absolute simulator executable.
These are project requirements, not values to copy from acceptance fixtures.
Include all applicable directories; the tool does not infer a remote job path.

Check `checks_passed`, not just `ok`. `ok` means observations were returned.
Resolve failed library paths, existing creation targets, unreadable/modified
sources, missing APIs/files and directory capacity before writing. Preflight
never saves user edits, opens a design window or checks out a simulator license.
Filesystem checks are on the MCP host. The returned Virtuoso identity/path data
does not prove that a remote worker sees the same files. No check proves model
sections, recursive includes, callback readiness, future locks or queue capacity.

Use normal PDK binding, preview, prepare and inspect steps from
`circuit-workflow.md`; project preflight does not replace final native checks.
For supported production writes, call `execute_circuit_operation` with:

- `operation`: one of `create_circuit_from_plan`, `create_template_symbol`,
  `draw_symbol`, `create_circuit_config`, `create_simulation_setup`,
  `run_simulation_setup`.
- `arguments`: the exact complete arguments of that existing tool. Inspect its
  actual schema. Keep the same request ID and input digest in the task record.

This wrapper reuses those tools, persists intent before dispatch and prevents
automatic replay. It does not authorize overwrites or a wider workflow. Direct
tools remain available but do not receive this durable journal. Do not mix direct
and journalled invocation for the same request or switch workspaces to bypass an
existing record. Prepare, CDF probe, fixed reference recipes, releases/stops and
arbitrary SKILL are outside this journal's initial coverage.

## After Interruption

Call `get_circuit_operation(operation, request_id)` in the original workspace.
It reads the local journal and refreshes the original native receipt only. Use
`refresh=false` for an offline record without contacting Virtuoso. Refresh also
persists a bounded observation under `.cad/ai/circuit_operations/`; this is not
an OA backup, write-ahead database transaction or scheduler checkpoint.

Inspect `state`, `receipt`, `last_response`, `last_observation` and
`target_observation`. A historical saved/readback flag is not a current integrity
check; `live_integrity_verified` and `automatic_resume_allowed` remain false.

- `retained_receipt`: inspect the returned circuit/Symbol/Config/setup reference
  with its existing dedicated inspect tool before further writes.
- `running` / `stop_requested`: poll the exact returned run reference/history.
  Do not launch another run. A stop request does not prove remote job termination.
- `completed`: read this exact history/results and apply explicit specifications;
  completion alone is not electrical qualification.
- `partial_or_failed` / `target_changed`: inspect the partial or edited target.
  Never silently save edits, delete a partial view or recreate over it.
- `outcome_unknown` / `start_unknown` / `context_lost` / `session_mismatch`: stop
  automatic mutation. Reconcile the original view, ADE history and scheduler jobs
  with the user. Never select the newest history, clear the record, force-close a
  session or generate a new request ID merely to get past the uncertainty.

A restarted MCP can recover from a surviving original Virtuoso. A restarted
Virtuoso cannot inherit its predecessor's DB/session handles: only durable
historical evidence remains. Model removal/changes do not block reading the old
receipt, but do block a new execution's normal validation. No recovery query
revalidates an entire source hierarchy or PDK snapshot.

Failed journal persistence is reported separately. Preserve the workspace and
native session for reconciliation; a lost or deleted journal is not proof that
no write/run occurred. Native claims also refuse a conflicting journal in a
second workspace. Record capacity is bounded and records are not automatically
deleted. Deployment, remote job control and destructive cleanup need their own
explicit scope and validation.
