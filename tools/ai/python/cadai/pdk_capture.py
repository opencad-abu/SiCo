"""PDK device capture validation, enrichment and worker results."""

from __future__ import annotations

from . import pdk_enrich, pdk_files
from .pdk_data import binding
from .pdk_normalize import context, detail
from .pdk_schema import PdkUnavailable


def record_device(job, row, capture, index, categories_data, *, pooled, model_name, store_device,
                  store_observation=None):
    """Validate one capture, persist its device and update that checkpoint only."""
    original = context(job["capture"]["context"])
    ddcat_cells = set(categories_data.get("ddcat_cells") or [])
    target = row["identity"]["target"]
    ctx = context(capture["context"])
    if (
        binding(ctx, job["library"]) != job["expected_binding"]
        or (
            not pooled
            and any(
                ctx[k] != original[k] for k in ("session_ref", "session_generation", "project_ref")
            )
        )
        or capture["data"]["identity"] != row["identity"]
    ):
        raise PdkUnavailable(
            "collection_changed", "Device or PDK session changed during collection"
        )
    value = detail(capture["data"], ctx)
    names = [p["name"] for p in value["parameters"]["items"]]
    if not all(isinstance(n, str) for n in names) or sorted(names) != sorted(
        row["cdf_summary"]["names"]
    ):
        raise PdkUnavailable("collection_changed", "Parameter directory changed during collection")
    resolution = pdk_files.resolve_model(index, model_name(value), target["cell"])
    registry = pdk_enrich.device_categories(categories_data, target["cell"], ddcat_cells)
    enriched = pdk_enrich.enrich(value, resolution, registry, index)
    # Model resolution is evidence, not eligibility. Keep every observed symbol cell.
    if store_observation:
        job.setdefault("standard_observations", {})[target["cell"]] = store_observation(enriched)
    entry = store_device(enriched)
    tiers = {
        tier: sorted(
            item["name"] for item in enriched["parameters"]["items"] if item.get("tier") == tier
        )[:256]
        for tier in ("interface", "derived", "auxiliary")
    }
    ranges = sorted(
        item["name"]
        for item in enriched["parameters"]["items"]
        if isinstance(item.get("range"), dict) and item["range"].get("status") != "unknown"
    )[:64]
    entry.update(
        {
            "enriched": True,
            "categories": registry["all"],
            "tiers": tiers,
            "ranges": ranges,
            "readiness": (enriched.get("readiness") or {}).get("overall"),
            "model": resolution.get("resolved") or resolution.get("queried"),
            "model_status": resolution.get("status"),
            "model_decks": len(enriched.get("model_decks") or []),
            "limits": bool(
                (enriched.get("limits") or {}).get("model_deck")
                or (enriched.get("limits") or {}).get("documented_minima")
            ),
        }
    )
    job["devices"][target["cell"]] = entry
    task = job["tasks"][target["cell"]]
    task.update(
        state="done",
        provenance={
            k: ctx.get(k)
            for k in (
                "session_ref",
                "process_id",
                "collector_revision",
                "virtuoso_version",
                "source",
            )
        },
    )
    job["detailed_devices"] += 1


def publish_job(job, quality_data, publish):
    """Write the completed catalog; the caller owns cancellation/publication locking."""
    original = context(job["capture"]["context"])
    return publish(
        job["capture"],
        job["library"],
        job["view"],
        job["devices"],
        excluded=job["excluded"],
        quality=quality_data,
        provenance={
            "execution": job.get("execution", "live_session"),
            "origin_session": original["session_ref"],
            "worker_configuration": job.get("worker_configuration"),
            "devices": {cell: task.get("provenance") for cell, task in job["tasks"].items()},
        },
    )


def create_pool(factory, root, environment, job, pending, probe):
    original, observed = context(job["capture"]["context"]), context(probe["context"])
    if (
        binding(observed, job["library"]) != job["expected_binding"]
        or any(
            observed[k] != original[k] for k in ("session_ref", "session_generation", "project_ref")
        )
        or probe["data"]["identity"] != pending[0]["identity"]
    ):
        raise PdkUnavailable("collection_changed", "PDK changed before starting workers")
    return factory(
        root,
        environment,
        job["capture"]["context"],
        job["library"],
        pending,
        probe,
    )


def pool_captures(pool, job, items):
    rows = {row["identity"]["target"]["cell"]: row for row in items}
    for state, cell, value in pool.drain():
        pool.check()
        if state == "failed":
            for task in job["tasks"].values():
                if task["state"] == "running":
                    task["state"] = "failed"
            raise value
        task = job["tasks"][cell]
        if state == "running":
            task.update(state="running", attempts=task["attempts"] + 1)
        elif state == "done" and task["state"] != "done":
            yield rows[cell], value
    pool.check()
    job["workers"] = {
        "limit": pool.worker_limit,
        "active": pool.active,
        "started": pool.started,
        "maximum": 32,
    }
