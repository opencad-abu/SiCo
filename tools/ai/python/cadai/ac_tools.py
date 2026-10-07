"""AC artifact transport and offline point phasor measurements."""

from .ac_measure import measure_ac
from .ac_schema import AC_MEASURE_SCHEMA, AC_TOOLS
from .circuit_spec_schema import CircuitSpecError, validate
from .result_contract import ResultError
from .result_tools import ResultStore
from .waveform_tools import capture_waveform


def call_ac(name, args, *, workspace, client):
    schema = next(t["inputSchema"] for t in AC_TOOLS if t["name"] == name)
    try:
        validate(args, schema)
    except CircuitSpecError as exc:
        raise ResultError(str(exc)) from exc
    store = ResultStore(workspace)
    if name == "read_maestro_ac_waveform":
        wave = capture_waveform(client, args, store, complex_ac=True)
        ref, artifact = store.put("ac_waveform", wave)
        return dict(
            ok=True,
            waveform_ref=ref,
            artifact=artifact,
            sample_count=len(wave["samples"]),
            **{k: v for k, v in wave.items() if k not in {"schema", "samples"}},
        )
    if name == "query_ac_waveform":
        wave = store.get(args["waveform_ref"])
        start, limit = args.get("offset", 0), args.get("limit", 30)
        count = len(wave["samples"])
        return dict(
            ok=True,
            waveform_ref=args["waveform_ref"],
            total=count,
            samples=wave["samples"][start : start + limit],
            next_offset=start + limit if start + limit < count else None,
            **{k: v for k, v in wave.items() if k not in {"schema", "samples"}},
        )
    paired = name == "measure_ac_transfer"
    first = store.get(args["input_ref"] if paired else args["waveform_ref"])
    second = store.get(args["output_ref"]) if paired else None
    rows = measure_ac(
        first, args["recipes"], second=second, denominator_floor=args.get("denominator_floor")
    )
    sources = (
        dict(input=first["source"], output=second["source"])
        if paired
        else dict(signal=first["source"])
    )
    report = dict(
        schema=AC_MEASURE_SCHEMA,
        kind="transfer" if paired else "signal",
        sources=sources,
        waveform_refs={
            k: args[k] for k in ("waveform_ref", "input_ref", "output_ref") if k in args
        },
        recipes=args["recipes"],
        denominator_floor=args.get("denominator_floor"),
        rows=rows,
        interpolation="cartesian_linear_frequency",
        phase_interval="(-pi,pi]",
        db_convention="20_log10_magnitude_over_explicit_reference",
        all_measured=all(r["status"] == "scalar" for r in rows),
        spec_qualified=None,
    )
    ref, artifact = store.put("ac_measurements", report)
    return dict(
        ok=True,
        measurement_ref=ref,
        artifact=artifact,
        **{k: v for k, v in report.items() if k not in {"schema", "recipes"}},
    )
