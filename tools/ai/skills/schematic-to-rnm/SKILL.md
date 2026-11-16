---
name: schematic-to-rnm
description: "Translate an analog block's structural netlist into a behavioral SystemVerilog real-number model (RNM) for mixed-signal functional verification with Xcelium and Spectre. Use when a schematic (or its Spectre/SPICE/DSPF netlist) plus a block spec must become a wreal behavioral model, not a device-by-device structural translation. Reads netlists and specs, generates a candidate .sv, and checks it against the netlist ports, the spec, and xrun -compile. Does not run mixed-signal simulation, edit the Virtuoso database, or invoke Spectre."
---

# Schematic to Real-Number Model

Turn the *structure* of an analog block into a *behavioral* SystemVerilog
real-number model. Mechanical netlist-to-Verilog tools (Verilog Out / NC-Verilog)
copy SPICE connectivity device by device; for a complex analog block that is not
a usable simulation model. The behavior must be reconstructed from the circuit's
intent. This skill supplies that intent from a **spec plus the netlist**, and
verifies the result mechanically before it is trusted.

The model produced here is the digital-friendly stand-in used in Xcelium+Spectre
mixed-signal runs: continuous voltages carried on `wreal` nets, behavior in
`always`/`assign`, no transistors.

## Inputs

1. **Structural netlist** — a Spectre `subckt`, a SPICE `.subckt`, or an
   extracted DSPF. Gives the authoritative port list, device inventory, and
   connectivity. It does **not** give behavior.
2. **Block spec (JSON)** — the functional intent: block type, port semantics
   (power / ground / analog / digital), the governing equation or description,
   and metrics (nominal output, supply range, dropout, startup time). See
   `references/spec-schema.md`.
3. **Golden behavioral model, if one exists** — a hand-written `verilog.vams`
   or `.sv` for the same cell. Use it only to check the generated model's
   behavior, never copy it blindly.

Treat the netlist as ground truth for the interface and the spec as ground truth
for behavior. When they disagree about a port, stop and report it — do not guess.

## M0 Virtuoso inspection normalization

The read-only MCP calls return bounded JSON. Capture one complete
`inspect_schematic` response and one complete `inspect_symbol_ports` response,
then normalize them before asking AI to generate a model:

```bash
python3 skills/schematic-to-rnm/scripts/normalize_manifest.py \
    --schematic-json run/raw/inspect_schematic.json \
    --symbol-json run/raw/inspect_symbol_ports.json \
    --output run/normalized-netlist.json \
    --manifest-output run/input-manifest.json
```

The normalizer requires `truncated=false`, complete instance
terminal-to-net records, complete top-level pin-to-net records, and matching
schematic/symbol identities. It emits `BLOCKED_INPUT` and a non-zero exit code
when the old bounded response shape is insufficient. This is a normalization
and provenance step, not a replacement for the official `si` hierarchy
netlist/map/globalmap baseline required by M0.

## Workflow

1. **Extract structure.** Run the parser on a readable local netlist:

   ```bash
   python3 skills/schematic-to-rnm/scripts/parse_netlist.py path/to/deck.scs --cell CELL --format json
   ```

   It infers the dialect (`spectre`/`spice`) from the subckt syntax. For a large
   extracted deck the per-device list is omitted above `--max-devices`; the port
   list, device summary, submasters, and internal nets are always present. Read
   the ports and device summary before assigning any circuit meaning.

2. **Read the spec.** Confirm every netlist port has a declared semantic in the
   spec (kind + direction). A port present in the netlist but missing from the
   spec is an open question to raise, not a detail to invent.

3. **Reconstruct behavior, do not transcribe connectivity.** Write a module
   whose interface matches the netlist ports exactly (names, order, direction)
   and whose body expresses the spec's function:
   - Power/ground/analog ports are `wreal` (or `wreal4state` with
     `import cds_rnm_pkg::*;` when X/Z is needed). Digital ports are `logic`.
   - Compute the output with the spec equation in an `always @(*)` or
     continuous assignment over `real` intermediates.
   - Add supply/enable checks and boundary conditions the spec calls for
     (e.g. output collapses outside the valid supply window, dropout behavior,
     startup delay).
   - Expose spec metrics as `parameter real` so the model is configurable.
   - Never emit `nmos`/`pmos`/`cmos`/`tran` primitives or per-transistor
     structure. If you find yourself mirroring the netlist device by device,
     stop — that is the failure mode this skill exists to avoid.
   See `references/rnm-style.md` for the target module shape and idioms.

4. **Check the candidate** against structure, spec, and the compiler:

   ```bash
   python3 skills/schematic-to-rnm/scripts/check_rnm.py candidate.sv \
       --netlist-json parsed.json --cell CELL --spec block.json --compile
   ```

   The checker verifies port name/direction/order against the netlist, wreal
   typing against the spec's port kinds, RNM modeling style (behavior present,
   no switch primitives, outputs driven), and — with `--compile` — syntax via
   `xrun -compile` in a scratch directory. It never modifies the design.

5. **Iterate on findings.** Every `error` must be resolved. Feed the checker's
   messages back into a revised model and re-check. A model that imports
   `disciplines.vams` cannot be compile-checked in pure SV mode; generated
   output should `import cds_rnm_pkg::*;` and drop that include (the checker
   reports this as a single warning rather than a cascade).

6. **Report** the ports, the behavioral mapping (which spec clause drove which
   assignment), remaining warnings, and any netlist/spec disagreement. State
   what was verified (ports, compile) separately from what was assumed
   (behavior). Do not claim simulation equivalence — that requires an approved
   mixed-signal run, which is out of scope here.

## Checker Contract

Both scripts use only the Python 3.9 standard library. `parse_netlist.py` reads
one netlist and prints JSON or text; it never executes or rewrites it.
`check_rnm.py` reads the candidate module, optional netlist JSON, and optional
spec, and — only with `--compile` — invokes `xrun -compile` against a temporary
library in a scratch directory. Neither script touches the Virtuoso database,
starts Spectre, or runs a mixed-signal simulation.

A clean checker result means the interface matches and the file compiles. It
does **not** prove the behavior is correct. Behavioral confidence comes from
comparison against a golden model or an approved simulation, reported separately.

## Boundaries

- Read-only with respect to the source design and the netlist. The only file
  this skill writes is the candidate model it is asked to generate.
- No Spectre, no Maestro, no mixed-signal run, no Virtuoso database edit.
- Do not infer a port's function from instance order or a single adjacent node.
  Preserve unknown names and record missing evidence.
- Keep transistor-level parameters out of the behavioral model; they belong to
  the SPICE view that the RNM stands in for.
