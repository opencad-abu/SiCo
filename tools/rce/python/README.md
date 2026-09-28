# RCE Python Backend

This backend replaces the old csh orchestration with a TOML-driven Python flow.
SKILL writes the TOML file; Python normalizes it, generates the tool command
files, and runs the selected stages serially.

## Path Setup

See [the repository environment guide](../../../docs/html/environment.html) for the complete
DRC/LVS/RCE variable list. This section focuses on paths used by RCE TOML.

Hand-written TOML files can use `$VAR` or `${VAR}` in path values. The backend
expands those variables before resolving the path and reports an error when a
referenced variable is unset or empty. Keep site-specific prefixes in the shell
environment and keep only relative suffixes in TOML. For example:

```sh
export CAD_HOME="/path/to/cad"
export PROJECT_ROOT="${PWD}"
export PDK_ROOT="${PROJECT_ROOT}/ETIP_N55_PDK_V1.0"
export RCE_RUN_ROOT="${PROJECT_ROOT}/.rce"
export SMIC28_ROOT="${PROJECT_ROOT}/smic28"  # SMIC28 example only
```

`CAD_HOME` is the sole installation root; this repository is installed below
`${CAD_HOME}/tools`. `PROJECT_ROOT` is the design workspace, `PDK_ROOT` is the
selected PDK installation, and `RCE_RUN_ROOT` is the extraction-output root.
The Bash environment example infers `CAD_HOME` from its own installed path when
it is unset. The csh example requires `CAD_HOME` to be set before it is sourced.
Both examples preserve caller-provided project and PDK path values.

The production RCE runtime inherits `CAD_PYTHON`. Install `tomli` for RCE TOML flow
configuration, plus PyQt5 and `pysqlite3` for the DSPF Analyzer. DSPF indexing prefers
`pysqlite3.dbapi2` and falls back to the standard-library `sqlite3` module when
the external package is unavailable. The selection order is `RCE_PYTHON`, then
`CAD_PYTHON`, `CAD_PYTHON_ROOT/bin/python3`, and `python3`; `RCE_ANALYZER_PYTHON` can replace that
selection only for the DSPF GUI. The executable wrapper uses an `env python3`
shebang for direct shell use, while Virtuoso always passes the selected interpreter
explicitly.

After environment expansion, relative paths use these bases:

- `run.run_dir`, design inputs, `run.cds_lib`, layer maps, LVS runsets, RC tech
  directories, and StarRC input files are relative to the TOML file.
- `netlist.output_path` is relative to `run.run_dir`.
- `extract.starrc.coupling_report_file` is relative to `run.run_dir`.
- `lvs.hcell_file` is relative to the TOML file.
- For Calibre XRC, `extract.tech_dir` is the RC technology root. The effective
  rule directory is `extract.tech_dir/extract.corner`; relative
  compatibility overrides `extract.xrc.rule_file`, `deck_file`, and
  `hcell_file`, plus `extract.xrc.xcell_file`, use that selected corner
  directory as their base.
- For StarRC, `extract.tech_dir/extract.corner` is the single-corner technology
  directory. A `tech_dir` that already ends in the selected corner remains
  compatible. Explicit `extract.starrc` input-file overrides stay relative to
  the TOML file.

## CLI

```sh
RCE_RUNTIME="${RCE_PYTHON:-${CAD_PYTHON:-${CAD_PYTHON_ROOT:+${CAD_PYTHON_ROOT%/}/bin/python3}}}"
RCE_RUNTIME="${RCE_RUNTIME:-python3}"
"${RCE_RUNTIME}" "${CAD_HOME}/tools/rce/python/rce" generate "${CAD_HOME}/tools/rce/python/examples/and2x1h7.toml"
"${RCE_RUNTIME}" "${CAD_HOME}/tools/rce/python/rce" run "${CAD_HOME}/tools/rce/python/examples/and2x1h7.toml"
"${RCE_RUNTIME}" "${CAD_HOME}/tools/rce/python/rce" run "${CAD_HOME}/tools/rce/python/examples/and2x1h7.toml" --dry-run
"${RCE_RUNTIME}" "${CAD_HOME}/tools/rce/python/rce" run "${CAD_HOME}/tools/rce/python/examples/and2x1h7.toml" --stop-after query
```

`--stop-after` accepts `cdl`, `gds`, `lvs`, `query`, `extract`,
`xrc_lvs`, `xrc_pdb`, and `xrc_fmt`. For an XRC run, use the upstream
`cdl`/`gds` names when present, or one of the three `xrc_*` names.

## Run Directory Ownership

The Virtuoso launcher reserves the canonical run directory before checking CDF
or writing `rce.toml`. A second Run, config write, or batch using that directory
is rejected. Batch submission reserves every row before generating any config;
reservations cover queue time, extraction, and the final OA publication.
Launch failures and normal completion/cancellation release the reservations.
Direct CLI runs use the same nonblocking filesystem lock. Active extraction
subprocesses inherit its descriptor to protect against an orphaned tool.

Locks live in `.cad-rce-locks` beside the run directory. The `.lock` inode is
permanent and must not be deleted. A `.json` reservation persists if Virtuoso
exits before its completion callback. The error identifies that JSON file;
it contains the canonical `run_dir` and `token`. After confirming that the
owning GUI, queued jobs, extraction processes, and publication have stopped,
release the abandoned reservation using those values:

```sh
"${RCE_RUNTIME}" "${CAD_HOME}/tools/rce/python/rce" lock release "/canonical/run_dir" "token-from-json"
```

Release checks the token and refuses while an execution lock is held. The JSON
`host` and `pid` identify the short-lived reservation helper, so that PID alone
does not establish whether a GUI or queued job has finished. Locks require a
shared filesystem that supports advisory `flock` across execution hosts.

Before launching, RCE archives `db`, `log`, and the configured output netlists.
It rejects configured inputs inside those managed directories or at an output
path before archiving. Files and directories already present elsewhere in the
run root are preserved, including CCI/SVDB inputs and selection files. Successful
cleanup removes only newly created known scratch (`qrcTemp` and `_xrc*.cal_`);
unknown files and previous run archives remain. GUI preparation uses this same
backend policy instead of archiving every file with a netlist extension.

## Core TOML

```toml
[run]
run_dir = "${RCE_RUN_ROOT}/ESCN55H7.AND2X1H7.test"
cds_lib = "${PROJECT_ROOT}/cds.lib"
run_type = "Current Host"

[input]
type = "OA"

[input.schematic]
lib = "ESCN55H7"
cell = "AND2X1H7"
view = "schematic"
cdl_header_file = ""

[input.layout]
lib = "ESCN55H7"
cell = "AND2X1H7"
view = "layout_drc"
layer_map = "${PROJECT_ROOT}/ETIPN55/ETIPN55.layermap"

[lvs]
tool = "Calibre"
runset_file = "${PDK_ROOT}/pv/ETIPN55.lvs.cal"
ignore_error = false
case_sensitive = true
hcell_enable = false
hcell_file = ""
virtual_connect = "(nil t)"
virtual_connect_names = "?"
recognize_gates = "NONE"

[extract]
tool = "QRC"
tech_dir = "${PDK_ROOT}/rc/QRC"
tech_name = "typ"
corner = "Typ"
corners = ["Typ"]
temperature = "25"
corner_temperatures = ["25"]
rc_type = "R+Cg+Cc"
top_cell_source = "schematic"
name_source = "schematic"
output_type = "dspf"

[runtime]
lvs_cpus = "1"
ext_cpus = "1"

[selection]
net_enable = false
net_type = "Include Nets"
nets = ""
cell_enable = false
cell_type = "Block Cells"
cells = ""

[netlist]
output_path = "AND2X1H7.dspf"
create_view = false
pin_order_enable = true
pin_order_type = "CDL Netlist File"
pin_order_file = ""
hierarchy_delimiter_enable = false
hierarchy_delimiter = "/"
brackets_replace = false
brackets_replace_type = "<> ===> []"
parasitic_coordinates = false
parasitic_res_layer = false
parasitic_res_dimensions = false
```

Reduction is disabled by default, so existing configurations keep the original
RCE behavior. To run Quantus standalone reduction after a successful
extraction, add a `[reduction]` table:

```toml
[reduction]
enabled = true
mode = "Default"                    # Default, Reduction Control,
                                    # Delay and Frequency, or Selection File
output_tag = "reduced"
cpus = "4"                          # optional; defaults to runtime.ext_cpus
control = "0.5"                     # Reduction Control mode only
delay_rel = "0.05"                  # Delay and Frequency mode only
delay_abs = "1e-12"                 # seconds
frequency = "20"                    # GHz
selection_file = "reduce.sel"       # Selection File mode only
temperature = ""                    # DSPF and Smart View only, degrees C
ground = "VSS"
reduce_negative = false
canonical_device_file = ""          # SPICE only
```

The form places the `Reduction` boolean control below `View Target` (and any
applicable native-view mapping fields). This lets the user finish selecting
the output destination before requesting standalone reduction. Its options
remain hidden until reduction is enabled, and mode-specific fields are shown
only for the selected mode.
Reduction is available for DSPF/SPF, SP/SPICE/HSPICE, and Quantus Smart or
Extracted View; SPEF, Calibre View, and StarRC OA view outputs are not supported
by `qreduce`.
The two boolean controls are independent: enabling or disabling Reduction never
changes the selected or enabled state of `Create Parasitic Netlist View`.
For a supported text-netlist output, that view control remains selectable while
the input fields are being completed; its OA library/cell destination is
validated only when the run is submitted.
`Reduction Control` maps to `--rcontrol` and is mutually exclusive with the
delay/frequency switches, as required by Quantus. `Selection File` requires an
existing file. `output_tag` may contain only letters, digits, and underscores.

RCE preserves the unreduced extraction result and writes a distinct reduced
result for a single-corner run. For example, `top.dspf` becomes
`top.reduced.dspf`, and native view `av_extracted` becomes
`av_extracted_reduced`. A different `output_tag` replaces `reduced` in those
names. Completion Summary, Open/Copy, DSPF Analyzer, and batch file-result
handling use the reduced result when the option is enabled.
Text-view publication deliberately keeps both representations, as described
below.

RCE deliberately disables Reduction for `Multiple Corners`. Changing the form
to that mode disables and clears the checkbox and hides all Reduction Options;
returning to `Single Corner` leaves the checkbox cleared. A hand-written TOML
profile with `reduction.enabled = true` is rejected before extraction when it
selects `Multiple Corners` or more than one corner. Multi-corner extraction
itself is unchanged and still publishes the normal unreduced per-corner (or
vectorized, where supported) result.

Each single-corner invocation writes Quantus diagnostics under `run_dir/log`:
`qreduce.log`, `qreduce.rpt`, and `qreduce.stdout.log`. Native-view reduction
runs from the generated `run_dir/db/cdl` directory because it contains the
`cds.lib` that references the design libraries. Set `RCE_QREDUCE` to override
the executable; otherwise RCE resolves `qreduce` from `PATH`.

`Create Parasitic Netlist View` by itself imports the extractor's original
netlist into the standard `dspfText` or `spiceText` view for a single output.
When extraction publishes a separate netlist per corner, each view receives
the corresponding `_<corner>` suffix, for example `dspfText_RCmax` and
`dspfText_Cmin`. When both `Create Parasitic Netlist View` and `Reduction`
are enabled, the completion callback
imports the original netlist into that standard view and imports the qreduce
result into a second postfix-named view. With the default postfix these pairs
are `dspfText` plus `dspfText_reduced`, or `spiceText` plus
`spiceText_reduced`; a custom `output_tag` is used in place of `reduced`.
The original and reduced physical netlist files are both preserved. Multi-Cell
applies the same two-view publication contract independently to every row.

Invalid reduction setup, a nonzero qreduce exit, or a missing/empty reduced
file fails the complete RCE run and records the reason in
`run_dir/log/exit-abnormally`; setup errors are rejected before extraction, and
the original unreduced result remains available after qreduce runtime failures
for diagnosis. Deliberately stopping at the flow's final extraction stage with
`--stop-after` skips reduction, preserving the command's existing
stage-boundary semantics.

In both RCE and standalone LVS, `More LVS Options` places the
`Recognize Logic Gate` menu above `Customized SVRF Command`. It accepts
`NONE`, `ALL`, or `SIMPLE`, defaults to `NONE`, and writes
`LVS RECOGNIZE GATES <selection>` to the Calibre control file. The setting
is stored as `lvs.recognize_gates` in run TOML and saved profiles; older
configurations without the key also use `NONE`. RCE's Calibre xRC control
file uses the same selection. `SIMPLE` is the SVRF keyword for simple gates.

The RCE and standalone LVS forms label the colon toggle `Colon(:)` and
default `Virtual Connect By: Name` on with the
name pattern `?`. Calibre interprets `VIRTUAL CONNECT NAME "?"` as virtually
connecting every set of disjoint nets that share the same non-empty name; nets
with different names are never connected together. Clearing and re-enabling
Name restores `?`, while a non-empty custom pattern is preserved. RCE writes
the two switches as `lvs.virtual_connect_enable` (colon) and
`lvs.virtual_connect_name_enable` (Name). If either key is present, these
booleans take precedence and an omitted switch defaults to false. Older TOML
using `lvs.virtual_connect` remains supported; without either representation,
virtual connect remains disabled. If Name is enabled,
`lvs.virtual_connect_names` must contain at least one pattern.

RCE's LVS stage always emits `MASK SVDB DIRECTORY "..." QUERY CCI`, without
`PINLOC`. The optional `lvs.svdb_query` selections, including `PINLOC`, apply
only to the standalone LVS flow; its default remains `QUERY CCI PINLOC`.

CDL and GDS command-file rendering is implemented by the shared
`common/python/cadstage` package. The `rcepy.gen_cdl` and `rcepy.gen_gds`
modules remain compatibility adapters for historical RCE imports; DRC, LVS,
and the standalone `cdlout`/`gdsout` stages use the shared implementation
directly.

Calibre LVS/xRC virtual-connect rendering, custom-SVRF concatenation, and
`-hcell` argument construction live in `common/python/cadcalibre`. RCE keeps
the configuration and path-resolution adapters because those remain part of
the RCE TOML contract.

## Process Corners

The Virtuoso form scans the direct children of the selected `Process Directory`
instead of offering a fixed corner list. QRC corners must contain
`qrcTechFile`, `qrc.tch`, or `cap_coeff.dat`; Calibre XRC corners must contain
`xrc.cal`; StarRC corners need a resolvable NXTGRD and mapping file. A Process
Directory that is already a valid corner remains supported.

`Extract Options` provides a `Corner Type` radio selector. `Single Corner`
keeps the original `Process Corner` and `Temperature` controls. `Multiple
Corners` replaces them with a scrollable row for every valid corner; each row
has a selection toggle and an editable temperature. At least one corner must be
selected. The form writes both the legacy scalar fields and the complete,
aligned arrays:

```toml
[extract]
corner = "RCmax"                    # first selected corner, compatibility
corners = ["RCmax", "Cmin"]
temperature = "125"                 # first selected temperature
corner_temperatures = ["125", "-40"]
```

Hand-written TOML may continue to use only `corner` and `temperature`. A single
temperature applies to every selected corner when `corner_temperatures` is
omitted. Multi-corner file outputs use `<stem>_<corner><suffix>`; the completion
Summary lists all outputs and lets Open, Copy, and DSPF Analyzer operate on the
selected result.

The extractor-specific execution is:

- Quantus uses a generated named technology library with
  `process_technology -technology_corner` and aligned temperatures. DSPF/SPEF
  files are published per corner; multi-corner SPICE and native view outputs
  remain one vectorized result.
- StarRC uses `CORNERS_FILE`, `SELECTED_CORNERS`, and
  `SIMULTANEOUS_MULTI_CORNER`. It accepts at most 15 corners and requires a file
  netlist for multi-corner runs.
- Calibre XRC uses native formatter `-corner corner1,corner2` when an explicit
  shared multi-corner deck and common temperature permit it. Otherwise RCE
  shares LVS and runs PDB/FMT per corner, allowing a separate `xrc.cal` and
  temperature for each row. Multi-corner Calibre View output is not supported.

Reduction is independent of the extractor's multi-corner capability: RCE
allows qreduce only after a single-corner extraction. It does not fan out
qreduce jobs across the corners selected in `Multiple Corners` mode.

Generated TOML preserves the directory's actual spelling. For hand-written
TOML, QRC first tries the exact `tech_dir/corner`, then a unique
case-insensitive match. An invalid selection fails before Quantus starts and
lists the available valid corners.

The QRC Calibre query requests device pin locations for complex recognition
layers. QRC and StarRC file-netlist stages are successful only when they create
a fresh, non-empty output, including when the vendor wrapper exits with zero.

## OA Input Name Synchronization

For `OA` input, the two arrow buttons between Schematic and Layout copy the
selected Library and Cell in either direction. They intentionally preserve both
View fields so projects can use different schematic and layout view names. The
buttons are hidden for non-OA input, and the Layout View defaults to `layout`.

## OA Terminal Order Check

Before an `OA` run writes its configuration or starts extraction, RCE compares
the selected schematic view's `schGetPinOrder` with the same cell's `symbol`
pin order and CDF `auCdl`/`spectre` terminal lists. It also checks effective
terminal orders and existing view-specific CDF overrides for the selected
schematic view and existing `symbol`, `auCdl`, and `spectre` views. It checks
the selected cell only; batch runs check each row's schematic cell. Other input
types and the standalone Python CLI do not perform this live Virtuoso check.

A mismatch dialog shows the schematic, symbol, simulator, and view orders and
offers `Update and Continue`, `Continue Without Updating`, and `Cancel` (the
default). Updating uses the schematic order for the existing symbol, both
simulator CDF lists, and existing view overrides, saves the changes, and
verifies them before proceeding. A failed update restores the view overrides
as well as simulator and symbol orders. Missing CDF terminal lists can be
populated; a missing symbol is not created. The check does not register a global
schematic Check & Save trigger or traverse device libraries.

Schematic input must be saved before checking. If the symbol has unsaved edits
or its terminal names differ from the schematic, the dialog offers only
continuing unchanged or cancelling; fix those differences before updating.
An update rechecks the displayed data before writing. A failed update stops
the run, attempts to restore the previous orders, and reports any failed
restoration in CIW. Existing schematic/symbol editor handles remain open.

## Top Cell Name

The Virtuoso form shows `Top Cell Name` under `More Extraction Options`. Its
`Schematic`/`Layout` radio selection updates a read-only cell-name field from
the active input pair:

| Input | Schematic | Layout |
| --- | --- | --- |
| `OA` | `input.schematic.cell` | `input.layout.cell` |
| `SCH+GDS` | `input.schematic.cell` | `input.gds.cell` |
| `CDL+LAY` | `input.cdl.cell` | `input.layout.cell` |
| `CDL+GDS` | `input.cdl.cell` | `input.gds.cell` |

The GUI defaults to `Schematic` and writes `extract.top_cell_source` explicitly.
Hand-written legacy TOML without this key retains the previous `layout` default.
`SVDB` and `CCI` have one database identity, so the radio is fixed to `Layout`.

The selection changes the extraction top-cell identity, including StarRC
`BLOCK` plus `CELL_TYPE`. Quantus 23.1 does not support a bare
`input_db -design` option; its `input_db -run_name` is the Calibre-query file
prefix, so query, run-name, and map-file identities remain tied to the layout
cell. Calibre SVDB/PHDB/XDB paths and OA extracted-view targets likewise remain
tied to the physical layout cell.

`Use Name From`, in the same disclosure section, independently selects whether
both net names and instance names in the parasitic netlist come from the
schematic or layout. The GUI defaults to `Schematic` and writes
`extract.name_source` explicitly:

| Use Name From | Quantus QRC | StarRC | Calibre XRC |
| --- | --- | --- | --- |
| `Schematic` | `output_setup -net_name_space schematic` | `XREF: YES` | `PEX NETLIST SOURCENAMES` |
| `Layout` | `output_setup -net_name_space layout` | `XREF: NO` | `PEX NETLIST LAYOUTNAMES` |

For hand-written legacy TOML without `extract.name_source`, QRC and XRC inherit
the effective `extract.top_cell_source`. StarRC instead preserves its legacy
`extract.starrc.xref` setting, whose default is `YES`. This fallback applies
only when `name_source` is absent; newly generated GUI configurations always
record both choices.

When source and layout top-cell names differ, StarRC rejects
`top_cell_source = "schematic"` together with `name_source = "layout"`:
selecting a schematic `BLOCK` requires `XREF: YES`, while layout-derived net
and instance names require `XREF: NO`. Select schematic names as well, or use
the layout top cell.

With StarRC `XREF: YES`, matched objects use schematic names; unmatched nets
and instances retain layout-derived names with StarRC's configured fallback
prefixes. Quantus extracted-view output is always in the schematic namespace.

## More Extraction Options

The RCE form keeps advanced controls collapsed by default. `More Input Options`
contains `CDL Include`, `More LVS Options` contains `Customized SVRF Command`,
and `More Extraction Options` contains `Top Cell Name`, `Use Name From`, net
selection, block-cell selection, and filtering controls.

Net and Block Cell selection use one input contract for all three extraction
engines:

```toml
[selection]
net_enable = true
net_type = "Include Nets" # or "Exclude Nets"
nets = "VDD VSS clk"
cell_enable = true
cell_type = "Block Cells"
cells = "lists/block_cells.txt"
```

`nets` and `cells` each accept either space-separated names or the path of an
accessible text file containing one name per line. Relative file paths are
resolved from the TOML directory. Empty lines and `#` comments are ignored;
duplicates are removed while preserving the first occurrence. The GUI exposes
only Include/Exclude for nets and one Block Cells switch. Old advanced-net and
hierarchy-cell selection modes are not supported.

Each generator converts the normalized input to its native controls:

| Selection | Quantus QRC | StarRC | Calibre XRC |
| --- | --- | --- | --- |
| Include/Exclude Nets | run-local nets file plus `extract -selection` | run-local `NETS_FILE` | `PEX EXTRACT INCLUDE/EXCLUDE` plus `-select`/`-cselect` where required |
| Block Cells | configured foundry block-cell file plus optional run-local merged file passed to `extraction_setup`; blocking type from `quantus.options` (shipped default `white`) | run-local `SKIP_CELLS_FILE` | merged hcell/xcell files passed with `-hcell` and `-xcell` |

For Quantus QRC, `RCE_QUANTUS_BLOCK_CELL_FILE` selects the foundry blocking-cell
file. An absolute value is used directly; a relative value is resolved below
the effective process-corner directory. A configured missing file fails before
Quantus starts. When the variable is unset or empty, RCE makes no assumption
about the foundry filename and only applies user Block Cells. Without user Block
Cells, the CCL refers to the configured foundry file directly. With user Block
Cells, RCE writes `log/cells.merged`, preserving the foundry contents and
appending user cells that are not already covered by a foundry exact or `*`
wildcard entry. Quantus also recognizes its own fixed-name
`blocking_cells_file`; that native list remains cumulative and is not copied or
duplicated by RCE. RCE does not emit the separate global `graybox -type layout`
macro-cell mode.

StarRC `SKIP_CELLS` entries supplied by a corner `common.opt` remain cumulative,
so foundry block cells and user Block Cells are both retained. In contrast,
`NETS` and `NETS_FILE` are rejected from `common.opt` whenever user net
selection is enabled, because their cumulative wildcard semantics would make
the requested Include/Exclude set ambiguous.

For Calibre XRC, the base xcell file is an explicit
`extract.xrc.xcell_file`, or `tech_dir/corner/xcell_list` when that foundry file
exists. Without user Block Cells, the base file is passed directly to Calibre.
When Block Cells is enabled, RCE writes `log/xcell_list.merged` and
`log/hcell_list.merged`, preserving the foundry files unchanged and adding
same-name layout/source mappings only for user cells not already covered by
compatible foundry entries. Conflicting foundry mappings fail before Calibre
starts. The base hcell file follows the normal `[lvs]` hcell resolution; any
xcell use therefore requires an enabled, accessible hcell file. Use cell names
that are identical in source and layout for this normalized one-name input.

## Netlist Customize

The GUI's `Netlist Customize` frame writes the following independent controls:

```toml
[netlist]
pin_order_enable = true
pin_order_type = "CDL Netlist File" # or "User Defined File"
pin_order_file = "pins/top.spi"
brackets_replace = true
brackets_replace_type = "<> ===> []" # or "[] ===> <>"
hierarchy_delimiter_enable = true
hierarchy_delimiter = "."
```

The backend uses the extraction engine's native controls whenever they preserve
the requested semantics:

| Control | Quantus QRC | StarRC | Calibre XRC |
| --- | --- | --- | --- |
| Pin order | `output_db -pin_order_file` | `SPICE_SUBCKT_FILE` | `PEX PIN ORDER SOURCE/FILE` |
| Hierarchy delimiter | output `-hierarchy_delimiter` | `HIERARCHICAL_SEPARATOR` | `PEX NETLIST ... SEPARATOR` |
| Bracket replacement | safe output postprocess | safe output postprocess | `PEX NETLIST CHARACTER MAP` |

`CDL Netlist File` uses the flow's source CDL. `User Defined File` is resolved
relative to the TOML file and must exist before command generation. QRC file
netlists require schematic output names when pin ordering is enabled. StarRC
pin ordering is supported only for DSPF/SPF because `SPICE_SUBCKT_FILE` is not
valid for SPEF. Unsupported combinations are disabled in the GUI and rejected
by the Python generator when explicitly requested in a run configuration.

New GUI forms enable `Use Pin Order From: CDL Netlist File` by default. Profiles
that omit the enable field use this default; a profile's explicit `false` is
preserved. Hand-written run TOML keeps its existing explicit opt-in behavior.
SVDB/CCI inputs have no source CDL, so the GUI restricts the source to
`User Defined File`. An empty, missing, or non-file path blocks Run/Run & Close
with a dialog before extraction starts; a changed nonempty path is also checked
when leaving the file field. Switching to an unsupported output temporarily
disables pin ordering and restores the previous choice when support returns.

When StarRC pin ordering is enabled, a conflicting `SPICE_SUBCKT_FILE` or
`NETLIST_FORMAT` in the corner `common.opt`, including its `INCLUDE_FILE` chain,
fails command generation. Equivalent pin-file paths are allowed and compared
case-sensitively relative to the StarRC run directory.

The GUI offers `/` and `.` as portable hierarchy delimiters. Hand-written QRC
SPEF and StarRC configurations may also use `|` or `:`. QRC keeps the Calibre
query input delimiter at `/`; the selected value applies only to the output
netlist.

Calibre XRC changes bus characters natively and keeps the DSPF `BUSDELIM`
header consistent. Quantus and StarRC do not provide an equivalent safe name
conversion for these LVS-input flows, so RCE transforms only netlist identifier
tokens after a successful extraction. It updates `*|BUSBIT` or
`*BUS_DELIMITER`, leaves comments, quoted metadata, and property assignments
untouched, replaces the requested output in place, and saves the unmodified
file as `<output>.pre_brackets` whenever a change is made.

StarRC file output supports `dspf`/`spf` (`NETLIST_FORMAT: SPF`) and `spef`.
Ordinary `sp`, `spice`, and `hspice` requests are rejected because they are not
valid StarRC `NETLIST_FORMAT` values.

## More Netlist Options

The `Parasitic Information` choices under `More Netlist Options` are stored as
three named booleans. They default to `false` and are independent except where
an extraction engine exposes only a combined native control:

```toml
[netlist]
parasitic_coordinates = true
parasitic_res_layer = true
parasitic_res_dimensions = true
```

The command generators map them as follows:

| GUI option / TOML key | Quantus QRC `output_db` | StarRC | Calibre XRC `PEX NETLIST` |
| --- | --- | --- | --- |
| `R&C Coordinates` / `parasitic_coordinates` | `-output_xy parasitic_res parasitic_cap` | `NETLIST_CONNECT_SECTION: YES`, `NETLIST_NODE_SECTION: YES`, `EXTRA_GEOMETRY_INFO: NODE RES`, `KEEP_VIA_NODES: YES`, `CAPACITOR_TAIL_COMMENTS: YES`, `NETLIST_UNSCALED_COORDINATES: YES`, and `NETLIST_UNSCALED_RES_PROP: YES` | `CLOCATION LOCATION RLOCATION` |
| `Parasitic R Layer Name` / `parasitic_res_layer` | `-include_parasitic_res_model_by_sub_conductor true` | `NETLIST_TAIL_COMMENTS: YES` | `RLAYER` |
| `Parasitic R Size (W&L)` / `parasitic_res_dimensions` | `-include_parasitic_res_length true` plus `-include_parasitic_res_width_drawn true` | resistor tail comments with `NETLIST_UNSCALED_RES_PROP: YES` | `RLENGTH RWIDTH` |

QRC supports all three choices for `dspf`, `sp`, and `spef`. The hand-written
`spice` and `hspice` output names are compatibility aliases for `sp`; the GUI
writes the canonical `sp` value. For view output, QRC supports resistor layer
and dimensions in both `extview` and `smartview`, but coordinates only in
`smartview`. The GUI hides `More Netlist Options` for view output; these view
combinations are available only to hand-written TOML.

StarRC supports the complete coordinate option only in DSPF/SPF. Unlike QRC or
XRC, it does not attach a direct XY pair to every capacitor. Its closest native
mapping uses capacitor layer metadata together with endpoint node/pin
coordinates from the `*|S`/`*|I` sections. The required
`CAPACITOR_TAIL_COMMENTS` command is not valid for SPEF, so a SPEF request with
`parasitic_coordinates = true` is rejected before `StarXtract` starts.
Resistor layer and dimensions are supported for both SPF and SPEF, but StarRC
emits both through the same resistor tail comments. The GUI therefore keeps
those two choices selected together; hand-written TOML may select one, but the
resulting StarRC netlist can contain the other information as well. Dimensions
additionally request unscaled resistor properties.

For consistent physical metadata, RCE deliberately uses unreduced extraction
whenever any StarRC parasitic-information choice is enabled. It therefore
generates `REDUCTION: NO`, `POWER_REDUCTION: NO`, and
`NETLIST_TAIL_COMMENTS: YES`, plus the geometry commands needed for coordinates.
Coordinate output also preserves original via nodes so via-resistor geometry is
not lost; this can increase the extracted topology and netlist size.
Because a corner's `common.opt` is appended after generated commands, RCE
rejects a `common.opt` whose final setting overrides any required command with
an incompatible value. The check follows nested `INCLUDE_FILE` commands in
their effective order and rejects missing or cyclic includes. Relative include
paths follow StarRC's working directory, which is the RCE run directory. This
includes protection for `NETLIST_FORMAT` and `EXTRACTION`, preventing a run
from silently changing the requested file syntax or dropping part of the
requested parasitic detail.

Calibre XRC supports the three choices independently for RCE's DSPF and HSPICE
outputs. Calibre also documents that `CLOCATION` produces no capacitor
locations in capacitance-only (`-c`) extraction, so the GUI disables
coordinates for `Cg`/`Cg+Cc` and hand-written TOML using that combination is
rejected. `NONE`/noRC produces an ideal netlist and therefore clears and
disables all three choices.

Existing configurations can retain the positional compatibility setting:

```toml
[netlist]
addon_info = "(t nil nil)" # coordinates, R layer, R dimensions
```

RCE still accepts three booleans in that legacy order, including the old
`NETLIST_EXT_ADDON_INFO` input. An explicitly present named boolean overrides
only its corresponding legacy position. New TOML should use the named options;
`addon_info` is retained for compatibility and is no longer emitted by the GUI
or examples.

## Parasitic Netlist Views

When the Virtuoso RCE form uses `output_type = "dspf"`, `"sp"`, or `"spef"`,
Output Configature shows `Create Parasitic Netlist View`. It is cleared by
default. When selected, the frontend waits for a successful extraction and a
non-empty output, then imports it into the target cell's matching `dspfText`,
`spiceText`, or `spef` text view. DSPF and SP use Cadence's registered `DSPF`
and `Spice` view types. SPEF uses a registered `SPEF` type when available,
otherwise the generic `text` type because standard IC23.1 does not register
SPEF. An existing view is overwritten under DD checkout and locking.

For multi-corner extraction with separate output files, every selected corner
is imported into its own `<view>_<corner>` view, preserving the corner's case.
For example, `RCmax` and `Cmin` DSPF outputs become `dspfText_RCmax` and
`dspfText_Cmin`. The completion summary and multi-cell batch flow use these
same targets. A single selected corner keeps the standard view name; Quantus
multi-corner SPICE also keeps `spiceText` because it produces one vectorized
netlist.

The target follows `Top Cell Name`. OA input can target either OA cell.
For SCH+GDS, the schematic choice targets the OA schematic cell; a layout choice
requires the GDS name to match that OA cell. For CDL+LAY, the layout choice
targets the OA layout cell; a schematic choice requires the CDL name to match
that OA cell. CDL+GDS, SVDB, and CCI inputs have no implicit OA target, so the
checkbox is disabled.
`netlist.create_view` records the GUI choice in TOML, but view creation belongs
to the live Virtuoso IPC completion callback; invoking the Python CLI directly
does not create a Cadence view.

## Completion Summary

The Virtuoso launcher displays a native SKILL summary window after every RCE
process finishes. A complete extraction, including any requested text or native
OA view creation, is shown in green. A nonzero process exit, failed view import,
or missing/empty file netlist is shown in red. The summary freezes the input,
top cell, extractor, corner, RC type, output target, run directory, launch log,
and start time when Run is pressed, so later form edits do not change the result.

When `lvs.ignore_error = true` and Calibre explicitly reports `LVS completed.
INCORRECT.`, RCE continues through PEX/FMT. After extraction and postprocessing
succeed, it records `log/lvs-ignored-mismatch` for both file and native OA view
outputs. Files must pass fresh, non-empty output checks; the Virtuoso callback
still verifies native view freshness. The result is shown in amber. DSPF/SPF
remains available to `Analyze DSPF` for non-signoff debug, while the launch log
stays open. This exception does not suppress PEX/xRC errors or expose a failed
partial output.

The batch monitor labels such a completed task `LVS Warning` and counts it
separately from Passed. JSON/TSV uses `succeeded_with_warnings`, records a
`warning_message`, and sets the overall result to `completed_with_warnings`
when there are no failures or cancellations. Warnings survive OA publication;
failed publication is still a failure. A warning-only batch retains exit code 0.

`Open Netlist` starts `gvim` asynchronously. `Copy Netlist` opens a Save As
dialog and copies the generated file without loading a potentially large
DSPF/SPEF into Virtuoso memory. Both actions are enabled only for a successful,
non-empty file output; native OA view results intentionally leave them disabled.
`Close` hides the modeless form, which is reused for the next result.

For successful DSPF/SPF output, including the completed ignored-LVS debug case
above, `Analyze DSPF` starts the external PyQt5 analyzer with a streaming SQLite
index, an interactive per-net R Network view with resistance Open/Short checks,
and an optional
read-only OA exact-net/region bridge. Setup, CLI, cache, GUI, and interaction
details are in the [DSPF Analyzer user guide](../../../docs/html/rce.html).
The SiCo pulldown in the CIW and Layout Editor also provides
`DSPF Analyzer...` for manually choosing a DSPF/SPF. That standalone entry does
not bind an arbitrary file to the current OA layout, so OA actions remain off.

## Native OA Parasitic Views

`extract.output_type = "view"` selects the extraction engine's native
OpenAccess output instead of a DSPF/SP/SPEF file. This is different from
`netlist.create_view`: the latter imports a completed text netlist into a text
cellview, while native view output preserves the engine's OA parasitic objects
and annotations.

The common configuration is:

```toml
[run]
cds_lib = "${PROJECT_ROOT}/cds.lib"

[extract]
tool = "QRC"                 # QRC, StarRC, or CalXRC
output_type = "view"
top_cell_source = "schematic"
name_source = "schematic"

[extract.view]
kind = "smart"               # QRC: smart or extracted
library = "design_lib"       # library and cell are an optional pair
cell = "top"
name = "av_extracted"
```

`extract.view.library` and `extract.view.cell` must either both be set or both
be omitted. OA library, cell, and view names cannot contain whitespace or `/`.
The engine-specific defaults and writers are:

| Engine | `extract.view.kind` | Default view name | Native writer |
| --- | --- | --- | --- |
| QRC/Quantus | `smart` (default) or `extracted` | `av_extracted` | `output_db -type smart_view` or `extracted_view` |
| Calibre XRC | `calibre` | `calibre` | `PEX NETLIST ... CALIBREVIEW SOURCENAMES/LAYOUTNAMES`, followed by Calibre Interactive import |
| StarRC | `oa` | `starrc` | `NETLIST_FORMAT: OA` and the StarRC OA writer commands |

The Virtuoso form shows these settings below `Output Type` when `view` is in
`RCE_OUTPUT_CHOICES`. `Native View` selects the QRC variant; XRC and StarRC each
have one variant. `View Name` is editable and `View Target` shows the resolved
`library/cell/view`. QRC shows no mapping selector, XRC shows `Calibre Cellmap
File`, and StarRC shows both `Device Mapping File` and `Layer Mapping File`.
`Create Parasitic Netlist View` applies only to file output and is not part of
this flow.

Native view target selection is separate from `Top Cell Name`. The GUI resolves
an OA-backed target from the input type and naming source:

| Input type | QRC Smart/Extracted View target | XRC/StarRC with Schematic names | XRC/StarRC with Layout names |
| --- | --- | --- | --- |
| `OA` | layout OA cell | schematic OA cell | layout OA cell |
| `SCH+GDS` | schematic OA cell | schematic OA cell | unavailable |
| `CDL+LAY` | layout OA cell | unavailable | layout OA cell |
| `CDL+GDS`, `SVDB`, `CCI` | unavailable | unavailable | unavailable |

QRC Smart/Extracted View always uses the schematic output namespace; changing
`extract.name_source` does not switch it to layout names. Calibre View and
StarRC honor `extract.name_source` and move the implicit target to the matching
schematic or layout OA cell. The GUI enables a run only when that target cell
exists. After a successful process it checks that the requested view was
created or updated.

### Quantus Smart and Extracted Views

QRC creates both view variants directly during `output_db` and requires an
accessible `run.cds_lib`. With `OA` or `CDL+LAY` input, the target is
`input.layout.lib/input.layout.cell` and its `input.layout.view` must exist; a
hand-written configuration can override the layout view with
`extract.view.layout_view`. With `SCH+GDS`, the target is the schematic OA cell
and the physical top comes from GDS/Calibre data, so a top layout cellview does
not need to exist. The `input_db -design_cell_name` layout-view token defaults
to `layout` in this case; it names the expected view type but does not require
that top view to exist. Override it with `extract.view.layout_view` when the
project uses a different layout-view name. `CDL+GDS`, `SVDB`, and `CCI` have no
implicit QRC OA target.

Smart View is the canonical default and uses Quantus `smart_view` output with
the `presistor` component mapping. Both variants default the OA view name to
`av_extracted`. Extracted View uses `extracted_view`, maps
`pcapacitor.c` and `presistor.r`, and enables the cellview compatibility check.
In either case no `netlist.output_path` file is expected.

See [examples/qrc_smart_view.toml](examples/qrc_smart_view.toml) for a complete
OA-input Smart View configuration.

For compatibility, existing QRC configurations may retain
`output_type = "smartview"` or `output_type = "extview"`; these select Smart
View and Extracted View respectively, regardless of `extract.view.kind`. New
configurations should use `output_type = "view"` plus `extract.view.kind`.

### Calibre View

Calibre XRC formatter output is an intermediate CALIBREVIEW netlist, normally
`run_dir/db/<source-cell>.pex.netlist`. RCE also writes
`run_dir/log/calibreview.setup`. After all XRC stages succeed, the live
Virtuoso callback calls `mgc_rve_load_setup_file` with that setup file and
verifies the resulting OA cellview. The Calibre/Virtuoso integration that
provides `mgc_rve_load_setup_file` must therefore be loaded in the session.

The import requires the foundry Calibre View cellmap. Set
`extract.view.cellmap_file`, or place `calview.cellmap` in the effective
`extract.tech_dir/extract.corner` directory. `extract.view.schematic_library`
can override the setup's schematic reference library; otherwise RCE uses
`input.schematic.lib`, then the output library. Calibre View supports both
`extract.name_source = "schematic"` (`SOURCENAMES`) and `"layout"`
(`LAYOUTNAMES`). The implicit target follows that choice and must be the
corresponding source or layout top cell. Text-netlist hierarchy delimiter and
bracket replacement controls are not applicable because OA names are handled
natively.

```toml
[extract.view]
kind = "calibre"
library = "design_lib"
cell = "top"
name = "calibre"
schematic_library = "design_lib"
cellmap_file = "${RCE_CALIBRE_VIEW_CELLMAP}"
```

The GUI uses `RCE_CALIBRE_VIEW_CELLMAP` as the first cellmap default. Leave the
field unset in hand-written TOML to use corner-local `calview.cellmap`; do not
reference the environment variable unless it is defined.

The Python CLI generates and validates the CALIBREVIEW netlist and setup file,
but it cannot perform the Virtuoso-only import. Run from the RCE form, or load
`run_dir/log/calibreview.setup` through the Calibre integration after a CLI
run. Paths written into the setup file cannot contain spaces, newlines, or
`:`.

### StarRC OpenAccess Parasitic View

StarRC creates the OA parasitic view directly. RCE emits `OA_LIB_DEF`,
`OA_LIB_NAME`, `OA_CELL_NAME`, `OA_VIEW_NAME`, and the foundry OA device/layer
mapping commands. `run.cds_lib` must name an accessible library-definitions
file. The two mappings are dedicated StarRC OA writer mappings; a stream-out
layer map is not interchangeable with either one.

For `OA` and `SCH+GDS` input, RCE also emits `OA_CDLOUT_RUNDIR` pointing to
`run_dir/db/cdl`, the parent of the generated `ihnl` directory. StarRC uses
those CDLout mapping files to recover the original schematic net and hierarchy
names. The final corner `common.opt` may repeat generated settings with
equivalent values, but it cannot override `BLOCK`, `XREF`, `CELL_TYPE`, the OA
target/mapping settings, or the required `|` hierarchy separator.

Set `extract.view.device_mapping_file` and
`extract.view.layer_mapping_file`, or install files under the effective corner
using one of the discovered names (`OA_DEVICE_MAP`, `oa_device_map`,
`oa_device_mapping_file`, `device_mapping_file`, or `DFII_DEVICE_MAP`, and the
corresponding `OA_LAYER_MAP` names). StarRC OA output fixes
`HIERARCHICAL_SEPARATOR: |` and stores parasitic geometry natively, so the
ASCII netlist customization and `More Netlist Options` controls do not apply.

```toml
[extract.view]
kind = "oa"
library = "design_lib"
cell = "top"
name = "starrc"
device_mapping_file = "${RCE_STARRC_OA_DEVICE_MAP}"
layer_mapping_file = "${RCE_STARRC_OA_LAYER_MAP}"
```

The GUI gives `RCE_STARRC_OA_DEVICE_MAP` and `RCE_STARRC_OA_LAYER_MAP`
priority over corner-local discovery. For a CLI configuration, omit the fields
to discover corner-local files, or ensure referenced variables are defined.

With schematic names, StarRC targets the source OA cell and also writes the
source schematic as its port and property annotation view. With layout names,
it targets the layout OA cell, uses the layout view for port annotation, and
omits property annotation. The selected side must be OA-backed; for example,
`SCH+GDS` supports the schematic target but not a layout target, while
`CDL+LAY` supports the layout target but not a schematic target. Any
selected-corner `common.opt` must preserve the generated OA output settings;
incompatible overrides are rejected before `StarXtract` starts.

`calibreview` and `starrcview` remain accepted as tool-specific compatibility
output types. Like `extview` and `smartview`, they should not be used in new
TOML; the portable spelling is `view` with an engine-appropriate
`extract.view.kind`.

## Calibre XRC

Select XRC with `extract.tool = "CalXRC"` (aliases `XRC`,
`CALIBRE-XRC`, and `CALIBRE_XRC` are also accepted). XRC owns its LVS
database, so it replaces the generic `lvs -> query -> extract` tail and never
runs the generic Calibre query stage:

| Input type | XRC stages |
| --- | --- |
| `OA` | `cdl -> gds -> xrc_lvs -> xrc_pdb -> xrc_fmt` |
| `SCH+GDS` | `cdl -> xrc_lvs -> xrc_pdb -> xrc_fmt` |
| `CDL+LAY` | `gds -> xrc_lvs -> xrc_pdb -> xrc_fmt` |
| `CDL+GDS` | `xrc_lvs -> xrc_pdb -> xrc_fmt` |

`SVDB` and `CCI` inputs are intentionally rejected for XRC. This flow creates
the matching PHDB/XDB itself; restarting from an external PHDB requires the
original, unchanged SVRF control file and is a separate reuse workflow.

For Calibre XRC, `extract.tech_dir` names the RC technology root and
`extract.corner` selects a directory below it. The default rule path is
therefore `tech_dir/corner/xrc.cal`. `rule_file` is preferred over its
compatibility alias `deck_file` if both are present.

The XRC LVS stage requires the RCE-specific Calibre LVS deck. The shared LVS
options also control the optional hcell correspondence file:

```toml
[lvs]
runset_file = "${PDK_ROOT}/pv/rce.lvs"
hcell_enable = true
hcell_file = "${PDK_ROOT}/pv/hcell_list"
```

`lvs.runset_file` must resolve to an accessible rule file and must be different
from the XRC rule file. Use the RCE/XRC LVS deck when the PDK distinguishes it
from the standalone LVS deck. When hcell is enabled, its resolved file is passed
to Calibre as `-hcell <file>`; when disabled, the path is ignored and `-hcell`
is omitted. For XRC compatibility, an empty canonical hcell path falls back to
`extract.xrc.hcell_file`, then to the selected `tech_dir/corner/hcell_list`.
Older XRC TOML files without `lvs.hcell_enable` keep that same enabled fallback
behavior.

```toml
[extract.xrc]
rule_file = "xrc.cal"
# deck_file = "calibre.xrc"
# hcell_file = "hcell_list" # legacy XRC compatibility only
# xcell_file = "xcell_list" # optional explicit foundry xcell base
```

The generated `_xrc.cal_` is shared by all three stages. It declares the
layout/source inputs, `PEX NETLIST`, `MASK SVDB DIRECTORY ... QUERY XRC`, and
the LVS report, then includes `lvs.runset_file` followed by the selected XRC
rule file. This keeps device recognition/LVS rules ahead of `rules.C/R/S` even
when a normal `xrc.cal` does not include an LVS deck itself. It also translates
these TOML controls into SVRF:

- `extract.temperature` -> `PEX EXTRACT TEMPERATURE`
- `filter.cap_value` / `filter.cap_percentage` -> `PEX REDUCE CC`
- `filter.res_value` -> `PEX REDUCE MINRES`
- included/excluded `selection.nets` -> `PEX EXTRACT INCLUDE/EXCLUDE`; coupled
  `-c`/`-rcc` modes use `-cselect` so selected-net Cc remains distinct
- `netlist.pin_order_enable` -> `PEX PIN ORDER SOURCE` for `CDL Netlist File`,
  or `PEX PIN ORDER FILE` for a user-defined simulator netlist

RCE always supplies the resolved base xcell to the PDB stage: an explicit
`extract.xrc.xcell_file` takes priority, otherwise it uses the selected corner's
`xcell_list` when present. When Block Cells is enabled, RCE merges user cells
into run-local hcell/xcell files as described under More Extraction Options.
The foundry files are never modified.

The runner resolves Calibre as `$MGC_HOME/bin/calibre` when `MGC_HOME` is
set, otherwise as `calibre` from `PATH`. Its XRC commands are:

```text
calibre -lvs -hier -spice <svdb>/<layout>.sp [-hcell <hcell_file>] -nowait _xrc.cal_
calibre -xrc -pdb <pdb-mode> [-xcell <xcell_file>] -turbo <runtime.ext_cpus> -nowait _xrc.cal_
calibre -xrc -fmt <fmt-mode> -nowait _xrc.cal_
```

For `CalXRC`, the Virtuoso form shows `Start RVE` beside `Create Parasitic
Netlist View`. Enabling it writes `extract.start_rve = true`. After the complete
XRC flow and netlist postprocessing succeed, RCE starts a detached command:

```text
calibre -rve <run_dir>/db/svdb.<layout_cell>
```

The executable follows the same `$MGC_HOME/bin/calibre` resolution as the XRC
stages. RVE does not inherit the RCE IPC pipes, so the Python process can exit
and the Virtuoso completion summary appears without waiting for RVE to close.
The option defaults off and is ignored for non-XRC tools, generate-only runs,
dry runs, and runs stopped before `xrc_fmt`.

`extract.rc_type` controls both modes:

| `rc_type` | PDB mode | FMT mode |
| --- | --- | --- |
| `RCC`, `R+Cg+Cc` | `-rcc` | `-all` |
| `RC`, `R+C`, `R+Cg` | `-rc` | `-all` |
| `R` | `-r` | `-r` |
| `C`, `CC`, `Cg+Cc` | `-c` | `-c` |
| `Cg` | `-c` | `-c -g` |
| `NONE`, `NO-RC`, `NORC` | skipped | `-simple` |

Pure-capacitance PDBs must use formatter `-c`; formatter `-all` requires
distributed resistance data. `-g` grounds coupling capacitance for `Cg`.
`NONE`/noRC keeps the LVS/PHDB stage, writes `PEX NETLIST SIMPLE`, skips PDB,
and invokes `calibre -xrc -fmt -simple` to create an ideal netlist without
parasitic R/C.

For text output, `netlist.output_path` can select an explicit destination;
otherwise the backend writes below `run_dir/db`. Calibre View uses the same
field only for its intermediate CALIBREVIEW netlist. Supported
`extract.output_type` values are:

| TOML value | `PEX NETLIST` format |
| --- | --- |
| `dspf` | `DSPF` |
| `spice` (legacy alias `sp`) | `HSPICE` |
| `view` | `CALIBREVIEW` plus Virtuoso import |

These are the only three public RCE output formats for Calibre XRC. The
historical `calibreview` spelling remains an input alias for `view`.

Each parasitic run writes `_xrc.cal_`, `log/xrc_lvs.log`, `log/xrc_pdb.log`,
`log/xrc_fmt.log`, `log/xrc_lvs.report`, and `log/rce.manifest`; noRC omits
`log/xrc_pdb.log`. View output
also writes the CALIBREVIEW intermediate netlist and `log/calibreview.setup`;
the Virtuoso import writes `log/calview.log`. The LVS stage
must freshly create the layout SPICE netlist, `<layout>.phdb`, and `<layout>.xdb`;
PDB must create a non-empty `pex.db`; FMT must create
`netlist.output_path`. A stage fails on a nonzero exit code, a non-clean LVS
result, a missing/nonzero `xRC Errors` summary, or a missing, empty, or stale
final netlist. The sole LVS exception is an explicit `LVS completed. INCORRECT.`
with `lvs.ignore_error = true`; it is recorded in `log/lvs-ignored-mismatch` and
does not suppress PEX/xRC errors. Failure details are written to
`log/exit-abnormally`.

See [examples/smic28_bus_test_xrc.toml](examples/smic28_bus_test_xrc.toml) for
a complete `CDL+GDS` XRC run. It uses `SMIC28_ROOT` for both the test design
and its adjacent PDK tree, and `RCE_RUN_ROOT` for generated data.

## StarRC Technology Inputs

The normal single-corner setup uses the shared technology root and corner:

```toml
[extract]
tool = "StarRC"
tech_dir = "${PDK_ROOT}/rc/StarRC"
corner = "RCmax"
```

For `SVDB` and `CCI` input, RCE does not generate a local LVS control file.
The generated `star.cmd` therefore sets `CALIBRE_RUNSET` to
`lvs.runset_file`, which is the resolved value selected from
`RCE_LVS_FILE` by the Virtuoso form. The configured runset must be a
readable, non-empty Calibre LVS/SVRF rule deck.

`CALIBRE_QUERY_FILE` is a separate Calibre query command file and must not be
set to the LVS rule deck. It remains `run_dir/log/star.query.cmd`: the normal
query stage generates it for `SVDB`, while RCE generates it before extraction
for `CCI`, whose external database deliberately skips the executable query
stage. Paired layout/source inputs continue to use the generated
`run_dir/log/lvs.cal` and `run_dir/log/star.query.cmd` files.

The backend resolves that example below `${PDK_ROOT}/rc/StarRC/RCmax`. It
prefers `nxtgrd` for the TCAD grid, otherwise the only `*.nxtgrd` file. It
accepts `tran.map`, `map`, `MAPPING_FILE`, or `mapping_file` for layer mapping,
otherwise the only `*.map` file. If the corner has no mapping, a shared
`MAPPING_FILE` or `mapping_file` at the technology root is also accepted.
Missing or ambiguous inputs are reported before `StarXtract` starts. For a
single selected corner this is StarRC's normal NXTGRD flow and does not
generate `CORNERS_FILE` or `SELECTED_CORNERS`. Selecting multiple corners uses
StarRC simultaneous multi-corner mode and resolves the grid and mapping file
independently for every selected corner.
When `extract.temperature` is non-empty, it is emitted as
`OPERATING_TEMPERATURE` for the selected corner.

If the selected `tech_dir/corner` contains `common.opt`, its StarRC commands
are appended in their original line order at the end of the generated
`star.cmd`. A missing `common.opt` is optional and does not change generation.

PDKs with a different directory layout can override either input explicitly:

```toml
[extract.starrc]
tcad_grd_file = "${PDK_ROOT}/rc/StarRC/file.nxtgrd"
mapping_file = "${PDK_ROOT}/rc/StarRC/mapping_file"
three_d_ic = false
```

Non-GUI StarRC behavior defaults now live in the installation's
`etc/flow-defaults/starrc.options`. Old TOML behavior fields such as `mode` and
`reduction` report a migration error naming the replacement command. See
[flow defaults maintenance](../../../docs/html/flows.html).

For QRC native-view output with CDL+GDS, CDL+LAY, SVDB, or CCI input,
set **More Input Options → CDL Run Directory** to the existing CDL export
directory containing `si.env`. Run and Run+Close reject an empty or invalid
directory. The absolute path is stored as `input.cdl.run_directory` and emitted
as `output_db -cdl_out_map_directory` in `qrc.ccl`. OA and SCH+GDS use the
CDL directory generated by the flow. This field is saved in configuration profiles.
