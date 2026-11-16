# Circuit-independent creation and simulation workflow

Apply this reference when creating circuits, configuring simulations or interpreting
Maestro results. The current release includes both generic infrastructure and
qualified example recipes; do not confuse their coverage.

## Stable conventions versus task data

Retain these engineering conventions across designs:

- At task start, ask for foreground/background unless the user already specified it;
  bind the choice with `begin_circuit_task` and retain it through the task.
- Prefer `sch*` for schematic/symbol work, `mae*/axl*` for Maestro. Check, save and
  close owned DB references; foreground windows stay with the user.
- Name non-PDK design instances `I_cellName_indexNumber`. Place inputOutput pins
  upper left, inputs lower left, outputs right, without changing electrical directions.
  A port carries two independent facts: `escape` draws its stub only, while the
  planned pin-master `orientation` comes from the direction and the edge it faces
  (input/inputOutput arrows point into the circuit, output arrows point away; only
  rotations, never mirrors). Left-hand inputs and right-hand outputs therefore stay
  at `R0`; never rotate a pin master from a stub direction.
- Create symbol analog pin annotations and an instance selection box including the
  body outline and terminal centers, excluding protruding terminal stub lines.
- Place TB stimuli (including supplies/bias/common-mode and standalone feedback
  test sources) left of the DUT, loads right, using electrically labelled short stubs.
- Use physical `basic/gnd` instances and direct pin wires for TB grounding. Do not
  create global labels or inherited connections unless explicitly requested by the user.
  `scope: ground` and `layout.pin_links` express the physical grounding plan;
  `global_net_authorization` records an explicit user request for global TB nets.
  Native `basic/gnd` resolves internally to ground; this does not authorize global labels.
- Preserve original targets, unsaved edits and request identities. After unknown
  execution state, inspect the retained record instead of launching again.

Use `inspect_cdf` for an explicit symbol or a direct instance's effective CDF without
callbacks. Keep default, current value and saved instance property separate. A cdf_ref
is a frozen session capture; fetch later pages from it and inspect the target again to refresh.
For uncertain parameter behavior, `probe_cdf_parameters` uses a confirmed task_ref and
an absent diagnostic schematic in a separate work library. Installed extension_ref controls
callbacks/order; do not pass source code or guess callbacks. Reuse request_id after unknown
execution and use `get_cdf_probe` for retained outcomes. Require all_requested_match,
saved/readback_verified and CDF restoration; a probe is not a connected circuit or a simulation.
Project checkpoint traces may be partial; read the complete artifact for parameter deltas.
Source CDF restoration covers scalar parameter values, not arbitrary callback side effects.

Supply these as explicit task/project/recipe data, never as global defaults:

- lib/cell/view, device masters and PDK/CDF parameter/callback mapping;
- connectivity, interface, stimuli/load roles and values, topology/placement template;
- simulator, analysis/options, variables, model file/section, corners and sweep axes;
- output expressions, expected test/corner/point coverage, numeric units, acceptance limits.

Do not infer gpdk models, 27°C, 1.3 V, AC/TRAN test names or performance thresholds
from a circuit being an amplifier. A template supplies topology/placement references;
other reusable knowledge belongs in explicit device/PDK and simulation/measurement
recipes. Neither should be hidden in an agent prompt.

## Project PDK context

The shared executor must not contain PDK-name branches, fixed installation paths,
device cell names, parameter mappings, model sections or vendor callback names.
Changing a project's PDK must not require editing the shared flow source.

Before circuit design call `get_pdk_preparation`. Recommend the captured editor's
`design.technology_library`; multiple PDKs require an explicit user choice before
collection. `prepare_pdk_data` prefers `SICO_PDK_DATA`, then workspace `.cad/ai`
data. Missing, incomplete, stale or corrupt metadata produces a separate collection
subtask: autonomously advance its exact `collection_ref`/`next_batch` until `ready`.
This collects the full device directory followed by every device's available parameters,
ports, geometry, simulator and callback metadata into `$CWD/.cad/ai/pdk-data`.
Publication requires complete device coverage. Unavailable fields remain explicit gaps;
never invent vendor constraints. Discovery does not execute callbacks. Interrupted
collection resumes from validated checkpoints, with at most two automatic recoveries;
changed sessions or damaged checkpoints restart collection. Respect user cancellation
and the confirmed foreground/background mode. Do not ask users to supply metadata
or wait for CAD staff. Search also starts preparation when the database is missing.
`search_pdk_devices` reuses the prepared directory across queries. `get_pdk_device`
and creation-time revalidation still read live metadata.
Retain snapshot_ref/device_ref/revision, then use `bind_pdk_device` with explicit
project kind and requested parameters. The adapter derives write policies and pin
escapes from complete observations where supported and resolves matching installed
callback support. Omit `extension_ref`; never ask the user to obtain internal identifiers.
Prefer compact results and retain `binding_ref` values as `binding_refs` for preview,
geometry and preparation; the backend expands complete masters and geometry.
The creator automatically
revalidates producer dependencies and its supplemental body observation at prepare
and execute; unchanged completed request IDs return retained status.
Unknown editable conditions require explicit project evidence/policy. Dynamic
conditions/callbacks need a registered project extension, validated at native prepare.
The adapter supports saved static flat symbols with scalar rectangular pins only;
unsupported geometry, stale snapshots or changed bindings require new selection.
It does not select models, infer parameter roles or prove simulation readiness.
Do not substitute a fixture or remove provenance to bypass a missing capability.

Resolve the project's actual library bindings and technology from the running
Virtuoso session and its library configuration. Read selected masters, symbol
terminals, CDF parameters and simulator metadata through supported Cadence APIs.
Use the project's selected model configuration or known-good setup for models and
corners. Never infer model sections or process equivalence from corner labels alone.

Retain this evidence as project-scoped, versioned data with source library paths
and master/CDF/model identities. Revalidate it when bindings, versions or relevant
files change; do not reuse a global cache keyed only by a library or cell name.
Multiple device libraries may coexist in one design: bind each selected master
and its simulation dependencies, rather than assume one PDK for the entire session.

Ask only for unresolved choices, such as the intended device family or model set.
Do not require the user to transcribe discoverable CDF metadata. If a project needs
special callback handling that metadata alone cannot express, resolve compatible
registered support using `list_project_extensions` through the common interface;
keep that implementation outside shared flow code. Discovery must not execute
callbacks, and creation must not guess callback names/order or silently reinterpret
width, multiplicity or finger parameters. Report unavailable mappings before writes.

Topology/placement templates are reference geometry, not authorization to reuse
source-PDK masters or parameters. Cross-project use requires explicit target-master
and terminal/parameter validation; do not promise automatic PDK migration.

Template queries merge bundled, project/shared (`SICO_CIRCUIT_TEMPLATES_DIR`), and
launch-workspace private (`.cad/ai/circuit_templates`) catalogs. Prefer private,
then project, then bundled candidates; use `tier` only when a source scope is needed.
For ordinary creation requests, start with function/asset queries across all tiers;
do not hardcode a bundled source library or `tier=builtin`. Among suitable topology
and interface candidates, preserve user style first, enterprise style second, and
use builtin examples when those tiers have no suitable reference. Read gaps before
falling back; do not treat corruption or an unavailable mount as an empty library.
The bundled `custom_gates45` set provides one representative per selected gate
function from gsclib045/giolib045, with source symbol outlines and placement evidence.
Drive-strength variants are provenance, not independent template choices. Its
`family`/`reference_set` summary fields identify the curated set; do not use them to
exclude untagged user/company references. For a gate/custom Symbol outline, call
preview_template_symbol with style="template_outline", then draw_symbol with its exact
preview_ref/digest. This preserves the captured body and adjusts grid pin anchors so every
terminal stub touches the actual outline and is at most five 0.0625-UU grid steps long.
Use the returned anchors for TB binding; do not reuse old captured coordinates. It exposes missing schematic
ports explicitly; inspect added_ports and the SVG before drawing. Place power terms
above and ground terms below the body, even if a template uses other positions.
Use supply/ground sigType when available; otherwise supply explicit port_roles keyed
by source terminal names from confirmed project roles (do not infer roles from names). outline_scale is an
explicit uniform size choice, not a PDK or cell-name rule. The target Symbol must be new;
existing saved/user Symbols are never cleared. Unsupported geometry returns a gap.
Use inspect_template_symbol and review the saved Cadence Symbol and an actual instance.
Template-drawn labels use stick font at 0.0625: upper-left cdsName()/ILLabel,
center [@partName]/NLPLabel, upper-right [@cellName]/NLPLabel, and terminal cdsTerm()/ILLabel.
Keep fixed template_ref and inspect catalog_origin/available_from. Same cell names
do not overwrite other versions. Extracted templates and generated previews stay
private; publishing to a shared library is a separate explicit maintenance action.
Bundled authored templates are structural references without live PDK qualification.

When advertised, `prepare_template_circuit` consumes one persistent template_ref plus
explicit selected project masters, target parameters and terminal/net/port mappings.
It returns a complete ordinary spec and a separate template_use. Pass template_use
through preview_circuit_spec, preview_circuit_geometry and prepare_circuit_creation:
each rechecks saved source endpoints and the digests. Source positions are hints in
source user units, so provide target geometry and an explicit layout. Never copy source
sizing or infer finger/terminal equivalence. The current adapter requires whole direct
topology, signal nets and port names equal to their net names; unsupported supply/ground
sigType or unresolved aliases are reported. Synthetic examples are test fixtures only.

When a source global is to connect through an existing boundary pin, explicitly provide
`net_scope_map[source_net_id] = "local"` with matching local net_map/port_map names.
Pass this mapping through template_use and also to preview_template_symbol. It retains
direct endpoints but changes external global semantics; never infer this from a rename,
remove the evidence, or claim cross-PDK electrical equivalence. Globals without a saved
boundary port cannot be localized by this adapter. Preserve captured port directions.

## Available versus pending generic capabilities

Generic tools exist for context/library/device inspection, confirmed task mode,
explicit view opening, config binding and historical result reporting. Template
lookup/symbol support depends on the installed tool inventory. Use only advertised tools.

The RC, gpdk INV/NAND2 and OpAmp create/run tools are reference validation recipes.
Use one only when the task actually requests its qualified example. Their master,
model and source checks must not be disabled to make an unrelated circuit fit.
Generic schematic creation supports the bounded static-symbol workflow below.
Nominal multi-test Spectre recipes now use the bounded workflow below; report unsupported
capabilities instead of substituting an example.
Existing `create_maestro_testbench` is a bounded setup adapter around an existing
TB; it does not synthesize arbitrary circuit connectivity.

`preview_circuit_spec(spec, bindings)` is a local generic circuit/TB planning tool.
It validates explicit instance/master bindings, typed parameter values, named
scalar endpoints, intentional open terminals, ports and notes. It plans TB roles
as stimulus left, DUT center and loads right, with labelled stubs; design instance
names follow the user's convention. Coordinates await actual master geometry.
The consumer schemas are `cad.circuit.spec.v1` and `cad.circuit.bindings.v1`;
the discovery producer may use its own richer format with a small adapter.
`binding_complete` means supplied metadata covers the selected instances, not
authenticated source, live session freshness or electrical qualification.
`creation_ready` remains false in this batch. Missing metadata is reported as
issues; invalid terminals/parameters/references fail. Never treat a preview digest
as permission to write or reuse bindings across projects. Strings stay opaque;
no expressions/callbacks are evaluated and no units are converted.

`preview_circuit_geometry` takes the exact spec/bindings/preview_digest plus
per-instance local geometry and explicit layout. Geometry declares master revision
and requested-parameter digest; it does not prove runtime freshness. Resolve all
connected terminals to selected anchors, then transform points and outward stub
directions together. Multiple anchors require an explicit choice; never round pin
coordinates or invent an escape route through a device body. The current planner
requires boundary pins, orthogonal outward stubs, unit scale and a specified grid.
It reports occupied-box collisions, stub contacts with other nets/devices and TB/pin
position violations. Labels are positioned but actual font/annotation overlaps are
not checked. A successful geometry preview still has creation_ready=false; complete native preparation
before creating a view.

`prepare_circuit_creation` recomputes the exact preview and geometry digests and
binds the confirmed task_ref to selected live masters, effective CDF, pin centers,
body extents and library paths. It creates no OA data. The first backend supports
saved static flat symbols, scalar connections, typed string/integer/number/boolean
parameters, a 0.0625 grid, labelled outward stubs and explicit collinear pin links.
CDF callbacks/hooks and dynamic editable conditions require an exact project-installed
extension registered before preparation; an MCP string never loads project code.
Parameterized/hierarchical symbols, inherited terminal/signal expressions, intentional
open terminals and complex routes remain unsupported. Native checks validate selected inputs;
they are not PDK discovery, producer-snapshot authentication or simulation qualification.

For a design request from a schematic editor, call `inspect_circuit_target` on the
captured source before planning. An existing empty schematic is a valid target: retain
its `target_ref` and use `target_action=reuse_empty`. For existing content, present the
host's `selection_question` and wait for the recorded answer: continue, replace after
backup, or use another cell. Continue supplies only additions with distinct instance
and port names; new devices may connect to existing named nets. Existing objects and
connections are preserved, and geometry collisions are rejected during preparation.
Replace saves and verifies a backup, including unsaved edits, before rebuilding.
Use `intent=edit` project preflight for a reused schematic. Preparation and execution
both check the retained content, library path and source window identity. A changed
or consumed observation requires inspection again.

Call `create_circuit_from_plan` with the returned prepare_ref, exact geometry digest
and a retained request_id. It revalidates dependencies and the selected target in the same
native turn, uses sch* instances/pins/wires/labels/notes, requires schCheck=(0 0),
checks actual connections and applied parameters, saves and validates saved data.
The target library must already exist. A design master may share its work library
with a new target in another cell; device-library aliasing and source-cell targets
are refused. The supplied device/design classification remains project input.

After a timeout, reuse the same request_id to retrieve the retained outcome; do not
retry with a new id. Use inspect_created_circuit for a fresh check of successful
creations. User modifications or rebound/closed foreground windows are refused
without check/save. release_created_circuit leaves GUI windows open and does not
delete views. A failed unsaved background view is retained for inspection, including
its DB reference; do not close it to force release. Failed/released/replayed records
are historical outcomes, not current-state or simulation verification.

The reply reports effective_parameters only for parameters explicitly applied;
it does not dump the complete CDF. Session records expire with Virtuoso. Formal PDK
bindings and created design Symbols can be composed in the same circuit/TB; this
does not select topology, stimuli, models or acceptance limits. When the server
advertises optional template_use on preparation,
pass it unchanged with the template-backed digests; do not strip provenance to
make a plan fit. Use only capabilities advertised by the installed server.

## Created Symbol handoff

After draw_symbol or create_template_symbol succeeds, call get_symbol_binding with its
symbol_ref and your intended master_id/instance_id. It revalidates the retained saved
Symbol and returns one design master row plus one geometry row, including actual pin
centers and outward escape directions (power up / ground down). Merge those rows into
the project's TB bindings/geometry, recompute the ordinary circuit preview and binding
digest, and use the existing geometry/prepare/create flow. Do not guess DUT pins from
input/output direction or reuse coordinates from the prior rectangular Symbol.

This adapter is for static flat scalar single-pin, non-parameterized design Symbols
created by this session. It does not discover PDK devices or choose stimuli, loads,
models or test specifications. Unsupported CDF hooks/parameters, interior pins and
changed symbols return errors. Use empty DUT parameters; retain explicit project
connections and basic/gnd pin links. Returned JSON is planning data; native preparation
and creation still revalidate dependencies. Requery for current saved-Symbol provenance.

For template DUT/TB/Config/Maestro, retain one task_ref and pass formal PDK masters to
prepare_template_circuit; preserve template_use through DUT geometry/prepare/create.
Draw the Symbol and obtain its binding before combining it with selected TB sources,
loads and basic/gnd. Repeated masters still need per-instance geometry/parameter digests;
derive ground pin links from selected anchors. Create Config only when needed and pass
config_ref to its simulation test; add a separate schematic test only for requested comparison.
Inspect saved views/setup, exact history coverage and declared limits. Matching two
entry points is not cross-PDK equivalence or authorization to add tests to other setups.

## Generic simulation recipe

Use preview_simulation_recipe with an explicit cad.simulation.recipe.v1: target,
global variables, temperature_c, and 1–8 tests. Each test specifies its saved
schematic target, spectre simulator, local variables, model paths/sections, dc/ac/tran
analyses, outputs and switch/stop views. Empty model lists are legitimate; do not
supply example model files for an unresolved project. The current backend supports
nominal scalar values or explicit corners/global list sweeps as described below;
retained Config references as below; no MC or existing-setup updates.

Call create_simulation_setup with the exact digest, confirmed task_ref and retained
request_id. It verifies per-test settings, saves/reopens, and returns setup_ref plus
a context_ref for generic results. run_simulation_setup rechecks unchanged setup,
source schematic data and explicitly listed model content before asynchronous launch.
Use get_simulation_recipe_status with run_ref, then read_maestro_results with the
returned context_ref and exact history. GUI Nominal maps to RDB nominal. Expected
coverage comes from the recipe, numeric limits from the requested specifications.

Reuse the same run request after a timeout; never make a second request to recover
an unknown launch. stop_simulation_recipe requests exact-history cancellation; poll
for terminal status. release_simulation_setup owns cleanup: it closes only an idle,
unmodified owned background session and leaves foreground windows open. Do not use
release_design_context as a substitute for releasing a managed simulation session.
Failed partial setups retain their session for inspection. Source schematics must
already be Check & Saved; this workflow does not silently save user source edits.

Model checks cover directly listed files, not recursive includes or section semantics.
Source checks cover direct OA data and bindings, not complete hierarchy/PDK validity.
ADE evaluates Calculator expressions during runs; preview/readback do not evaluate
them. Runtime defaults and site job policy remain visible in setup snapshots, without
being presented as explicit project choices. A completed run is not spec qualification.


Nominal recipe variables must be scalar expressions, without whitespace or sweep
list/range delimiters. Do not turn off Maestro's Point Sweeps master switch to
implement a single point: on IC23.1 this also drops global variables from point
netlists. The backend creates corner/sweep lists only when explicitly supplied, preserves
the required switch, checks global/local enable settings, and requires the recipe's exact
test/corner/point coverage (one point per test for nominal scalar recipes). Sources must already be Check & Saved; do not silently save a
user-edited source to unblock simulation.

Generic simulation recipes optionally support explicit corners and global numeric list
sweeps. Supply each corner's temperature, numeric variable overrides and complete shared
model list; test.models must then be empty. Declare every swept/overridden variable in
global variables; reject overlap with test overrides or between corner and sweep values.
Sweep values are finite JSON SI numbers, not range strings or executable expressions.
Preview bounds the Cartesian product to 64 sweep combinations / 256 test points, then
run/status validate actual RDB tuples, corner models and temperatures. Do not assign
parameter tuples to point IDs based on preview order. Completed does not mean spec pass.
No per-test corner model merging, MTS, independent temperature sweep, Monte Carlo or
existing-setup update is supported by this recipe extension.

## Config inputs for generic simulation

Create a new Config using `create_circuit_config` with explicit top and direct instance
view bindings. `switch_views` and `stop_views` are ordered lists; legacy defaults remain
spectre/schematic and stop spectre. Alternate view names are permitted only when the actual
OA type is schematic. Instance library/cell must match its original master: this adapter
does not replace a fixed schematic master with a different cell/library. No global library
search list, occurrence, external source file or sub-config rules are supported here.

In each simulation test, provide the returned `config_ref` and set `design` to that Config
lib/cell/view. Match test switch/stop lists to the Config. Never guess a ref or silently
adopt an arbitrary existing Config. A test without config_ref still requires a schematic.
The backend uses HDB lists/bindings for Config tests; ADE's inactive schematic lists may
reset on reopen and do not override HDB. Saved environment state is still checked thereafter.

Config sources are checked through the bounded schematic hierarchy to Spectre stop views
(max 8 levels/512 non-pin instances), including direct connections, raw properties, library
paths and actual bindings. Read `config_validation` for the captured source/binding paths.
Require clean, saved source schematics; never save user edits to pass preflight. After a child
edit followed by Check & Save, even restoring the old parameter may change extraction metadata:
create a new simulation setup to rebind the new source version, rather than bypassing checks.
This is not full PDK/CDF/PCell/AMS or Spectre-leaf-content certification.

## Generic history and specification report

1. Bind the requested existing Maestro with `open_design_view`; reuse the confirmed
   presentation choice and prefer read access for reporting.
2. Call `read_maestro_results(context_ref, history)` with the exact history name.
   It captures test states, actual point/corner parameters, expression values and
   historical definitions without saving, running or evaluating new expressions.
3. Retain `result_ref`. Page `query_maestro_results` with `entity` set to `tests`,
   `coordinates`, `definitions` or `outputs`. Unknown units remain null.
4. Call `evaluate_measurement_specs` with a contract containing `source`, an explicit
   `expected_test_points` list (`test/corner/point`), and `specifications` entries
   (`test/output/unit`, `lower` and/or `upper`, optional inclusive flags).
   Derive expected coverage from the intended setup/plan, not the observed result list.
   Obtain limits from the user or selected documented recipe. The unit declaration
   describes the stored numeric value; this version performs no unit conversion.
5. Report per-metric pass/fail/missing counts and worst corner/point. A known failure
   yields `fail` even when other results are missing; `coverage_complete` reports that
   separately. `incomplete` has `spec_qualified=null`. `pass` qualifies only the explicit
   contract, not the entire design or an untested PVT region.

Artifacts persist under workspace `.cad/ai/measurement-results`, addressed and checked
by SHA256. Query/report works after context release or server restart. Capture is
bounded to 2048 test points and 4096 expression results; unsupported aggregate or
reliability dimensions are refused, not flattened. Waveform-valued expressions remain
`non_scalar`; use the separate waveform flow below for raw real signals. Units/spec inference,
statistical/MC and reliability qualification remain outside this version.


## Exact-history waveform measurements

Use `read_maestro_waveform` with explicit `context_ref/history/test/corner/point/analysis/signal`.
Supply the raw simulator result name, not a guessed ADE analysis alias. Retain `waveform_ref`.
The source includes the actual coordinate parameters and native x/y units. Non-waveform types
(scalar/complex/family/missing/error) remain explicit; do not select a family member implicitly.

Use `query_waveform` to page samples and `measure_waveform` for up to 32 explicit recipes.
Every recipe needs id/operation/interpolation="linear"/result_unit. sample requires at;
minimum/maximum/mean/rms require window.start/stop; crossing also requires threshold,
rising/falling/either direction and a positive 1-based occurrence. Quantities have value and unit.
Windows must be increasing and fully inside the waveform; no extrapolation. Mean is axis-weighted;
RMS integrates the square of the piecewise-linear waveform. Crossing counts arrival from a strict
side, excludes start-at-threshold and does not repeatedly count plateau samples.

Use documented s/V/A/Hz prefix conversions; unknown native units allow identity only and missing
units require an error. Do not relabel numeric values to claim conversion. Report each missing/error
reason, preserve source identity, and never treat all_measured as spec qualification.
Measurements are offline and survive context release. Single-waveform tools exclude complex AC, family expansion, MC, GUI Calculator registration
and automatic Maestro output updates. Paired real recipes are described below.


## Paired real-waveform measurements

Call `measure_waveform_pair(input_ref, output_ref, recipes)` on saved waveform refs with the same
result_ref, target/library/history/test/corner/point/analysis, coordinate parameters and result path.
Do not combine corners, points or histories merely because the signal names match. Reopened session
handles may differ; full immutable source identity must still agree. Both sources are retained.

For delay, require pairing="explicit_occurrences", input_event and output_event, each with a
window/threshold/direction/1-based occurrence. Return signed output event time minus input event
time in the requested time unit; never silently choose a later output edge to force positive delay.
For gain_sample provide at; for gain_rms provide one shared window. Gain is output/input with
result_unit="1" and an explicit nonnegative denominator_floor quantity in input-compatible units.
abs(input)<=floor is an error. RMS includes DC; signed sampled ratio is not complex AC gain.

Each source uses its own linear interpolant; no sample-index pairing, resampling, extrapolation or
automatic overlap clipping. Inspect components for actual crossing times/sample/RMS values, preserve
missing/error reasons, and never treat all_measured as design/spec qualification. These calculations
are offline; release of the source contexts does not invalidate frozen inputs or reports.

## Explicit waveform specification qualification

Before running, obtain expected test/corner/point coverage and specifications from the task/project.
For a new generic setup, use the requested simulation recipe preview's expected_test_points;
never derive expected coverage from the result rows that happened to complete.

Call `evaluate_waveform_specs(result_ref, measurement_refs, contract)` with one exact frozen
point-results reference and selected real/AC measurement reports. Each specification needs
test/analysis/kind, signal roles, the complete original recipe (including id/result_unit), comparison
unit and at least one lower/upper bound. Bounds are inclusive unless explicitly stated otherwise.
Use unique recipe ids per test for different signals/windows/analyses. A matching metric id alone
is insufficient; do not rewrite equivalent units inside the recipe without rerunning the measurement.

The tool converts stored numeric units to supported specification units. Preserve raw_value/raw_unit
and the converted value/unit. Read per-metric margin/worst sources; do not rank incompatible units.
Use `query_waveform_spec_report` for rows, summaries and coordinates, including missing/error reasons
and pair components. Duplicate measurements at the same test/corner/point/id are refused.

Report pass only when expected coverage is complete and all specified measurements pass. Known
failures remain fail even with missing/error rows; always report coverage_complete as well.
incomplete/spec_qualified=null cannot be called pass. Unselected metrics are counted but not qualified.
These tools are offline, bounded and scoped to the explicit contract; they do not change Maestro
outputs, infer production limits or establish full-design/MC qualification. References are persistent
wave_specs_<sha256> artifacts, independent of open source contexts.

## Complex AC point measurements

Use `read_maestro_ac_waveform` with exact context/history/test/corner/point/signal and analysis="ac".
Use `query_ac_waveform` to page [frequency,real,imaginary] samples and native units/types. Do not
feed the old real-only complex marker into AC measurement or select a family member implicitly.

`measure_ac_waveform` measures the absolute signal; `measure_ac_transfer` measures complex
output/input with the same immutable source identity and explicit input denominator_floor.
Never assume the source is 1 V / 0 degrees. Both sides interpolate their own real/imaginary
components linearly in physical frequency before division; no polar/log-axis interpolation,
index alignment or extrapolation. Request interpolation="cartesian_linear_frequency" and at
with a frequency unit. Capture preserves phasor magnitude; no automatic RMS/peak conversion.

magnitude uses compatible V/A units (transfer uses 1). magnitude_db requires positive reference
quantity, result_unit=dB and uses 20*log10(abs(z)/reference); never invent a reference or use a
power convention. phase requires phase_mode=principal, result_unit=deg/rad and nonnegative
magnitude_floor in the phasor's units (transfer floor is dimensionless). At/below floor is error,
including zero; negative real axis maps to +180 degrees, interval (-180,180], with no unwrap.

Preserve source references, interpolation brackets, input/output phasors, units and error reasons.
AC measurement reports are persistent/offline with spec_qualified=null. Use the separate
explicit specification tool to qualify these reports; point values do not infer cutoff or margin.
Before extending to margin calculations, obtain the loop sign, crossing selection, frequency
window, gain reference and phase continuation convention explicitly from the task/project.

## AC cutoff and declared-loop phase margin

Use `measure_ac_response(input_ref,output_ref,denominator_floor,recipes)` for saved complex pairs.
Every recipe explicitly supplies a full frequency window, cartesian_linear_frequency interpolation,
crossing direction and 1-based occurrence. The input floor applies across the entire window,
including interior complex segment minima. No automatic overlap clipping or endpoint crossing.

bandwidth currently means definition=lowpass_cutoff: explicit positive reference_gain in unit 1,
positive drop_db, falling crossing, output Hz/prefix. Window start must be above the threshold.
Do not infer DC/peak gain or claim a bandpass width from this result.

phase_margin requires characteristic=one_plus_L, loop_sign +/-1 on output/input, explicit integer
phase_anchor_turns added to the principal phase at window start, continuous_cartesian_path and
dimensionless magnitude_floor. Request deg/rad. Phase continuation follows separate numerator and
denominator line segments to the chosen unity crossing, not nearest-angle unwrap of their ratio.
Return pi+continuous phase without rewrapping or discarding negative margins. Never infer the
loop sign, missing low-frequency turns or true stability from a plotted transfer curve.

Strict side-changing crossings exclude tangencies and window endpoints. Threshold plateaus and
roots indistinguishable at floating precision are errors; missing requested occurrences remain
missing/null. Use `query_ac_response` for rows/all crossings with metric filter, provenance,
threshold/bracket and selected phase branch. A successful measurement or positive margin is not
full stability/spec qualification. These tools remain offline and do not perform loop injection.

## AC specification contracts and generic measurement policy

Use the existing `evaluate_waveform_specs` for AC reports: kind=ac_signal, ac_transfer or ac_response,
analysis=ac. Paired AC specifications additionally require the exact top-level denominator_floor
from the report. Match the full recipe: frequency/window, interpolation, dB reference, magnitude
floor, lowpass definition/reference gain/drop_db, loop sign/characteristic/phase anchor, crossing
direction/occurrence and result_unit. Equivalent physical units written differently inside a recipe
are not silently treated as identical; preserve the original recipe and choose comparison unit separately.

AC comparisons allow deg/rad conversion without rewrapping signed phase or continuous margins.
No implicit dB/linear conversion or reference substitution. Preserve source/recipe/floor mismatch
errors, raw values/units and selected crossing/path provenance. A passing explicit contract leaves
stability_qualified=null; it does not certify the circuit's stability.

All measurement tools consume waveform and task/project data. Never choose circuit/PDK-specific
branches or copy fixture signal names, frequencies, thresholds, references, coverage or limits into
an actual task. RC/RLC and other examples are acceptance fixtures only. Supported scope is explicit:
lowpass cutoff rather than arbitrary bandpass width; compatible V/V, A/A or dimensionless transfer;
no automatic loop extraction, family/MC flattening or unstated phase branch selection.

## Discover measurement capabilities before constructing recipes

Call `list_measurement_capabilities` to select among implemented operations. Exact kind,
operation and tool_name filters combine with AND; query is a literal case-insensitive substring,
not semantic or regex search. Lists are id-sorted/paged. Retain catalog_revision and request
`get_measurement_capability(capability_id,catalog_revision)` for the selected item.

Read both recipe_schema and rules: structural validity alone does not establish unit/data or
physical compatibility. Inspect analysis/input, concrete quantities paths (recipe.* or tool.*),
output_unit, failures, capture and specification links. AC operations require raw analysis=ac;
real operations depend on real data and units, and delay requires time. Populate names, frequencies,
windows, thresholds, references, floors, phase branch and limits from task/project requirements.

Unknown IDs and stale revisions are errors. Empty search means no match, not permission to invent
an operation or substitute a reference circuit. Directory lookup is offline and does not establish
that the live session has suitable data or licenses. Query once per selected capability/revision,
then use its declared measurement tool and the existing explicit specification report flow.
