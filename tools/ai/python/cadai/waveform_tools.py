"""Exact-history waveform artifacts and offline recipes; no current-GUI selection."""

import uuid

from .circuit_spec_schema import CircuitSpecError, validate
from .result_contract import ResultError, text
from .result_tools import ResultStore, _native, capture
from .waveform_measure import measure, validate_recipe, validate_samples
from .waveform_pair import pair_report
from .waveform_schema import MAX_SAMPLES, MEASUREMENT_SCHEMA, WAVE_SCHEMA, WAVEFORM_TOOLS


def capture_waveform(client, args, store, *, complex_ac=False):
    # Reuse the point schema's dimension and coordinate checks. Never guess a family member.
    point_results = capture(client, args["context_ref"], args["history"])
    key = {k: args[k] for k in ("test", "corner", "point")}
    tests = [r for r in point_results["tests"] if all(r[k] == v for k, v in key.items())]
    if len(tests) != 1 or tests[0]["status"] != "done":
        raise ResultError("exact completed test/corner/point required")
    coordinates = [
        r
        for r in point_results["coordinates"]
        if r["point"] == args["point"] and r["corner"] == args["corner"]
    ]
    if len(coordinates) != 1:
        raise ResultError("exact coordinate parameters required")
    token = "wave_" + uuid.uuid4().hex
    try:
        meta = _native(
            client,
            "aiAcCapture" if complex_ac else "aiWaveCapture",
            *(
                args[k]
                for k in ("context_ref", "history", "test", "corner", "point", "analysis", "signal")
            ),
            token,
        )
        if any(meta.get(k) != v for k, v in args.items()):
            raise ResultError("waveform capture source mismatch")
        if meta.get("capture_id") != token:
            raise ResultError("waveform capture token mismatch")
        text(meta.get("session"))
        text(meta.get("results_directory"), 4096)
        if meta.get("target") != point_results["target"]:
            raise ResultError("waveform target changed")
        pages, count = meta.get("pages"), meta.get("sample_count")
        if (
            type(pages) is not int
            or not 0 <= pages <= 256
            or type(count) is not int
            or not 0 <= count <= MAX_SAMPLES
        ):
            raise ResultError("invalid waveform page/sample count")
        samples = []
        for index in range(pages):
            batch = _native(client, "aiWavePage", token, index).get("samples")
            if not isinstance(batch, list) or not 1 <= len(batch) <= 256:
                raise ResultError("invalid frozen waveform page")
            samples.extend(batch)
        if len(samples) != count or pages != (count + 255) // 256:
            raise ResultError("waveform capture incomplete")
        if complex_ac:
            from .ac_measure import validate_ac_samples

            validate_ac_samples(samples)
        else:
            validate_samples(samples)
        status = meta.get("status")
        if status not in {"waveform", "scalar", "complex", "family", "missing", "error"}:
            raise ResultError("unsupported waveform status")
        if status != "waveform" and count:
            raise ResultError("non-waveform must not contain samples")
        for unit in (meta.get("x_unit"), meta.get("y_unit")):
            if unit is not None and (not isinstance(unit, str) or len(unit) > 32):
                raise ResultError("unsupported native unit")
        source_ref, _ = store.put("results", point_results)
        wave = dict(
            schema="cad.maestro.ac-waveform.v1" if complex_ac else WAVE_SCHEMA,
            source=dict(
                result_ref=source_ref,
                **{
                    k: meta[k]
                    for k in (
                        "target",
                        "session",
                        "history",
                        "test",
                        "corner",
                        "point",
                        "analysis",
                        "signal",
                        "results_directory",
                    )
                },
                library_path=point_results["library_path"],
                coordinate=coordinates[0],
                reader="maeReadResDB/getData/DR",
            ),
            status=status,
            x_unit=meta.get("x_unit"),
            y_unit=meta.get("y_unit"),
            unit_source="native_vector",
            samples=samples,
        )
        if complex_ac:
            native_type = meta.get("native_y_type")
            if status == "waveform" and native_type not in {"doublecomplex", "double", "intlong"}:
                raise ResultError("AC native vector type missing")
            wave.update(representation="cartesian_complex", native_y_type=native_type)
        return wave
    finally:
        try:
            _native(client, "aiWaveRelease", token)
        except Exception:
            pass


def call_waveform(name, args, *, workspace, client):
    schema = next(t["inputSchema"] for t in WAVEFORM_TOOLS if t["name"] == name)
    try:
        validate(args, schema)
    except CircuitSpecError as exc:
        raise ResultError(str(exc)) from exc
    store = ResultStore(workspace)
    if name == "measure_waveform_pair":
        return pair_report(store, args)
    if name == "read_maestro_waveform":
        wave = capture_waveform(client, args, store)
        ref, artifact = store.put("waveform", wave)
        return dict(
            ok=True,
            waveform_ref=ref,
            artifact=artifact,
            sample_count=len(wave["samples"]),
            **{k: v for k, v in wave.items() if k not in {"schema", "samples"}},
        )
    wave = store.get(args["waveform_ref"])
    if name == "query_waveform":
        start, limit = args.get("offset", 0), args.get("limit", 30)
        count = len(wave["samples"])
        return dict(
            ok=True,
            waveform_ref=args["waveform_ref"],
            source=wave["source"],
            status=wave["status"],
            x_unit=wave["x_unit"],
            y_unit=wave["y_unit"],
            total=count,
            samples=wave["samples"][start : start + limit],
            next_offset=start + limit if start + limit < count else None,
        )
    for recipe in args["recipes"]:
        validate_recipe(recipe)
    if len({r["id"] for r in args["recipes"]}) != len(args["recipes"]):
        raise ResultError("duplicate measurement recipe id")
    rows = measure(wave, args["recipes"])
    report = dict(
        schema=MEASUREMENT_SCHEMA,
        waveform_ref=args["waveform_ref"],
        source=wave["source"],
        recipes=args["recipes"],
        rows=rows,
        spec_qualified=None,
        all_measured=all(r["status"] == "scalar" for r in rows),
    )
    ref, artifact = store.put("wave_measurements", report)
    return dict(
        ok=True,
        measurement_ref=ref,
        artifact=artifact,
        **{k: v for k, v in report.items() if k not in {"schema", "recipes"}},
    )
