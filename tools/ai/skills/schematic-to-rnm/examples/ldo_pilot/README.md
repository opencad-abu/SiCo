# LDO Pilot

End-to-end pilot for the `schematic-to-rnm` skill, using the LDO IP from the
Cadence Mixed-Signal (DMS/AMS) RAK as ground truth. It exercises both port
topologies: `LDO_MASTER` (supply-only) and `LDO_AON` (with a digital enable).

## Files

| File | Role |
|------|------|
| `LDO_MASTER.spec.json`, `LDO_AON.spec.json` | Behavioral spec (input) |
| `LDO_MASTER.netlist.json`, `LDO_AON.netlist.json` | Parsed structure (generated) |
| `LDO_MASTER.rnm.sv`, `LDO_AON.rnm.sv` | Generated RNM (golden reference) |

The netlist JSON is produced from the RAK Spectre deck
`/tmp/DMS_AMS_FLOW/Lab2/src/ams/control_files/LDO.scs` (regenerate if that path
is unavailable; any `subckt LDO_MASTER`/`subckt LDO_AON` source works).

## Run it

```bash
S=cad/ai/skills/schematic-to-rnm
E=$S/examples/ldo_pilot

# 1. extract structure from the netlist
python3 $S/scripts/parse_netlist.py \
    /tmp/DMS_AMS_FLOW/Lab2/src/ams/control_files/LDO.scs \
    --cell LDO_MASTER > $E/LDO_MASTER.netlist.json

# 2. check the generated RNM against structure, spec, and the compiler
python3 $S/scripts/check_rnm.py $E/LDO_MASTER.rnm.sv \
    --netlist-json $E/LDO_MASTER.netlist.json --cell LDO_MASTER \
    --spec $E/LDO_MASTER.spec.json --compile
```

Expected: `0 error(s), 0 warning(s)` and `xrun -compile succeeded`.

## What it demonstrates

- The parser recovers the exact port list from the Spectre subckt
  (`LDO_MASTER` = VDD/VSS/VOUT; `LDO_AON` adds a digital EN).
- The generated model reconstructs *behavior* from the spec (VOUT = VDD/2,
  gated by supply window and, for AON, by EN) rather than transcribing the
  13-transistor connectivity the netlist actually contains.
- The checker confirms interface match, wreal typing by port kind, RNM style,
  and clean `xrun -compile` — the mechanical trust gate before any mixed-signal
  simulation.

## Golden comparison

The RAK ships hand-written behavioral models for these cells at
`/tmp/DMS_AMS_FLOW/Lab4/LDO_IP/amsLDO/<cell>/verilogams/verilog.vams`. The
generated RNMs match their behavior (VOUT = VDD/2, EN gating) while using the
pure-SystemVerilog style (`import cds_rnm_pkg::*;`, no `disciplines.vams`) so
they compile under `xrun -compile -sv`.
