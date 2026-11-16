# Block Spec Schema

The block spec is the **behavioral** ground truth for a schematic-to-RNM
translation. The netlist supplies the interface (ports, devices, connectivity);
the spec supplies what the block is supposed to *do*. It is JSON so the checker
can read it with the Python 3.9 standard library (no `tomllib` on this host).

## Top-level keys

| Key        | Type   | Purpose                                                      |
|------------|--------|--------------------------------------------------------------|
| `cell`     | string | OA cell name; should match the generated module name.        |
| `block`    | object | `{ "type": "ldo", "cell": "LDO_MASTER" }` — coarse class.    |
| `behavior` | object | Human description and the governing equation.                |
| `metrics`  | object | Named real numbers; each becomes a `parameter real`.         |
| `ports`    | object | Per-port semantics keyed by port name.                       |

Either `cell` or `block.cell` must be present. The checker matches the module
name against it and warns on a mismatch.

## Port entry

Each `ports.<NAME>` object describes one interface signal:

| Field       | Values                                   | Meaning                              |
|-------------|------------------------------------------|--------------------------------------|
| `kind`      | `power` / `ground` / `analog` / `digital`| Drives the required SV net type.     |
| `direction` | `input` / `output` / `inout`             | Checked against the module.          |
| `nominal`   | real                                     | Expected steady value (optional).    |
| `range`     | `[min, max]`                             | Valid operating window (optional).   |

`power`, `ground`, and `analog` ports must be a `wreal` family net in the model;
`digital` ports must be `logic`/`wire`. The checker enforces this.

## Example (LDO_MASTER)

```json
{
  "cell": "LDO_MASTER",
  "block": { "type": "ldo", "cell": "LDO_MASTER" },
  "behavior": {
    "description": "Linear regulator; output regulates to half the input supply when the supply is in range.",
    "equation": "VOUT = (VDD in [VDD_MIN, VDD_MAX]) ? VDD/2.0 : 0.0"
  },
  "metrics": {
    "VDD_MIN": 4.5,
    "VDD_MAX": 5.5
  },
  "ports": {
    "VDD":  { "kind": "power",  "direction": "input",  "range": [4.5, 5.5] },
    "VSS":  { "kind": "ground", "direction": "input" },
    "VOUT": { "kind": "power",  "direction": "output", "nominal": 2.5 }
  }
}
```

## Example with an enable (LDO_AON)

```json
{
  "cell": "LDO_AON",
  "block": { "type": "ldo", "cell": "LDO_AON" },
  "behavior": {
    "description": "Always-on LDO; output is half the supply while enabled, zero when disabled or out of range.",
    "equation": "VOUT = (EN && VDD in [VDD_MIN, VDD_MAX]) ? VDD/2.0 : 0.0"
  },
  "metrics": { "VDD_MIN": 1.9, "VDD_MAX": 2.9 },
  "ports": {
    "VDD":  { "kind": "power",   "direction": "input", "range": [1.9, 2.9] },
    "VSS":  { "kind": "ground",  "direction": "input" },
    "EN":   { "kind": "digital", "direction": "input" },
    "VOUT": { "kind": "power",   "direction": "output", "nominal": 1.25 }
  }
}
```

## Guidance

- List **every** netlist port in `ports`. A port the netlist has but the spec
  omits is a gap to resolve with the designer, not a detail to invent.
- Keep metric names identical to the `parameter` names you want in the model;
  the checker reports metrics that never surface as parameters.
- The equation is prose for the model author, not executed. Make it precise
  enough that the `always`/`assign` body follows from it directly.
