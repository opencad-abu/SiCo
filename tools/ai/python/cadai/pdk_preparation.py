"""PDK preparation lifecycle with compatibility exports for selection policy."""

from __future__ import annotations

import time
from copy import deepcopy
from threading import Event, RLock

from . import pdk_capture, pdk_collection, pdk_quality, pdk_selection
from .pdk_builtin import LIBRARIES as BUILTIN_LIBRARIES
from .pdk_data import PdkData, binding
from .pdk_init import PdkInit, guard, with_init
from .pdk_normalize import context, now
from .pdk_schema import PdkUnavailable

# Compatibility exports; selection policy has one owner in pdk_selection.
BASE_LIBRARIES = pdk_selection.BASE_LIBRARIES
DESIGN_TOOLS = pdk_selection.DESIGN_TOOLS
INSTRUCTIONS = pdk_selection.INSTRUCTIONS


class PdkPreparation:
    BASE_LIBRARIES, BUILTIN_LIBRARIES = BASE_LIBRARIES, BUILTIN_LIBRARIES

    def __init__(self, bridge, *, workspace=None, environment=None, entry=None, pool_factory=None):
        self.bridge = bridge
        self.init = PdkInit(bridge)
        self.data = PdkData(workspace, environment)
        self.entry = entry
        self.ready, self.jobs = {}, {}
        self.selected = self.raw_context = None
        self.catalogs = {}
        self.selected_scope = self.choice_validator = None
        self.selection_info = None
        self.selection_view = "symbol"
        self.file_layers = {}
        self.target_library = None
        self._collection_lock, self._publication_lock = RLock(), RLock()
        self._cancels, self._pools = {}, {}
        if pool_factory is None and workspace:
            from .pdk_bridge import PdkBridge

            if isinstance(bridge, PdkBridge):
                from .pdk_pool import PdkPool

                pool_factory = PdkPool
        self.pool_factory = pool_factory

    _selection_prompt = staticmethod(pdk_selection.selection_prompt)

    def require_selection(self, info):
        return pdk_selection.require_selection(info, self._selection_prompt)

    def guard(self, name):
        return guard(self, name)

    def _file_layer(self, ctx, library):
        """Lazily index the install directory once per (root, library) for this service."""
        return pdk_quality.file_layer(self.file_layers, ctx, library)
    def _categories(self, job, ctx):
        return pdk_quality.categories(self.bridge.capture, job["library"], ctx)

    @staticmethod
    def _model_name(value):
        return pdk_quality.model_name(value)

    @staticmethod
    def _quality(index, job, items, categories):
        return pdk_quality.quality(index, job.get("excluded"), job["view"], items, categories)

    def inspect(self, args):
        if "target_library" in args:
            self.target_library = args["target_library"]
        self.selection_view = args.get("view", "symbol")
        raw = self.bridge.capture("libraries", {})["context"]
        ctx = context(raw)
        catalogs, issues = self.data.discover(ctx, args.get("view", "symbol"))
        for key, prepared in list(self.ready.items()):
            try:
                cached = catalogs.get(key[0])
                matches = prepared["binding"] == binding(ctx, key[0]) and (
                    prepared["source"] in {"session", "published_standard"}
                    or bool(
                        cached and cached["complete"] and cached["digest"] == prepared["digest"]
                    )
                )
            except PdkUnavailable:
                matches = False
            if not matches:
                del self.ready[key]
                if not (cached and cached["complete"]):
                    issues.append({"library": key[0], "code": "stale_pdk_data"})
        recommended = pdk_selection.entry_recommendation(self.entry)
        result = pdk_selection.describe(
            ctx,
            catalogs,
            issues,
            self.selection_view,
            self.target_library,
            recommended,
            self._data_summary,
        )
        from .pdk_standard.catalog import annotate_candidates
        result = annotate_candidates(result, self.data.workspace, self.data.environment)
        self.raw_context, self.catalogs = raw, catalogs
        scope = result["selection_ref"]
        candidates, bound_library = result["candidates"], result["bound_library"]
        if self.selected_scope != scope:
            self.selected = None
        result["choice_confirmed"] = self.selected is not None
        if bound_library in {row["library"] for row in candidates}:
            self.selected, self.selected_scope = bound_library, scope
            result.update(
                choice_confirmed=True,
                selected_library=bound_library,
                selection_source="technology_binding",
            )
            result = with_init(self, result)
            self.selection_info = deepcopy(result)
            return result
        if self.choice_validator:
            authorized = [
                row["library"]
                for row in candidates
                if self.choice_validator(row["library"], result)
            ]
            if len(authorized) == 1:
                self.selected, self.selected_scope = authorized[0], scope
                result["selection_source"] = "confirmed_choice"
        if len(candidates) == 1 and self.selected is None:
            # A single session process is unambiguous; do not create a review
            # event merely to repeat the only available choice.
            self.selected, self.selected_scope = candidates[0]["library"], scope
            result["selection_source"] = "single_candidate"
        result.update(choice_confirmed=self.selected is not None, selected_library=self.selected)
        if len(candidates) != 1 and (
            not self.selected
            or (self.choice_validator and not self.choice_validator(self.selected, result))
        ):
            self.selection_info = deepcopy(result)
            return self.require_selection(result)
        result = with_init(self, result)
        self.selection_info = deepcopy(result)
        return result

    def current_selection(self, library):
        """Return the pending selection response without a live directory scan."""
        info = self.selection_info
        if info is None:
            return None
        if self.choice_validator and not self.choice_validator(library, info):
            return self.require_selection(info)
        if self.selected != library:
            return self.require_selection(info)
        return None

    @staticmethod
    def _data_summary(catalog):
        return pdk_quality.data_summary(catalog)

    def prepare(self, args):
        with self._collection_lock:
            return self._prepare(args)

    def _prepare(self, args):
        try:
            info = self.inspect(args)
        except Exception:
            self.close()
            raise
        for active in self.jobs.values():
            if active["status"] in {"collecting", "capturing"}:
                try:
                    self._check_origin(active)
                    pool = self._pools.get(active["collection_ref"])
                    if pool:
                        pool.check()
                except PdkUnavailable as exc:
                    self._stop_pool(active)
                    active.update(
                        status="cancelled" if exc.code == "collection_cancelled" else "failed",
                        code=exc.code,
                        message=str(exc),
                        updated_at=now(),
                    )
                    self.data.progress(active)
        library, needs_prompt = pdk_selection.choose_library(
            args,
            info,
            self.selected,
            self.choice_validator,
            self.BASE_LIBRARIES,
            self.BUILTIN_LIBRARIES,
        )
        if library is None:
            return self.require_selection(info) if needs_prompt else info
        support = library in self.BASE_LIBRARIES
        view = args.get("view", "symbol")
        expected = binding(context(self.raw_context), library)
        key = (library, view)
        if not support:
            self.selected = library
            self.selected_scope = info["selection_ref"]
        if not args.get('refresh') or pdk_selection.standard_reuse_allowed(self.data.environment):
            from .pdk_standard.catalog import prepared
            try:
                standard = prepared(self.data, library, self.raw_context)
            except PdkUnavailable as exc:
                # A bundled base-library baseline may be re-pointed at a moved
                # installation; any other source change stays a hard error.
                if exc.code != 'pdk_source_changed' or library not in BUILTIN_LIBRARIES:
                    raise
                standard = None
            if standard and (
                not args.get('refresh') or (standard.get('standard') or {}).get('design_ready') is True):
                self.ready[key] = standard
                return self._ready_result(key)
        builtin = self.catalogs.get(library, {}).get("source") == "builtin"
        if not args.get("refresh") or builtin:
            if key in self.ready:
                return self._ready_result(key)
            cached = self.catalogs.get(library)
            if cached and cached["complete"]:
                self.ready[key] = pdk_collection.cached_ready(cached, expected, self.raw_context, self.data, library)
                return self._ready_result(key)
            if library in self.BUILTIN_LIBRARIES:
                raise PdkUnavailable(
                    "builtin_pdk_unavailable", "No built-in PDK data for the requested view"
                )
            job = next(
                (j for j in reversed(list(self.jobs.values())) if (j["library"], j["view"]) == key),
                None,
            )
            job = job or self.data.previous(library, view)
            if job:
                if job.get("status") == "cancelled":
                    return self.result(job)
                same = pdk_collection.checkpoint_matches(
                    job,
                    expected,
                    self.raw_context,
                    self.data.checkpoint_valid,
                )
                if (
                    same
                    and job["status"] in {"collecting", "capturing"}
                    and job["collection_ref"] in self.jobs
                ):
                    return self.result(job)
                if (
                    same
                    and job["status"] in {"interrupted", "failed"}
                    and job.get("retry_count", 0) < 2
                ):
                    self._stop_pool(job)
                    pdk_collection.resume_job(job)
                    self._cancels[job["collection_ref"]] = Event()
                    job.pop("code", None)
                    job.pop("message", None)
                    self.jobs[job["collection_ref"]] = job
                    self.data.progress(job)
                    return self.result(job)
                if same and job.get("retry_count", 0) >= 2:
                    return self.result(job)
        else:
            if library in self.BUILTIN_LIBRARIES:
                raise PdkUnavailable(
                    "builtin_pdk_unavailable", "No built-in PDK data for the requested view"
                )
            self.ready.pop(key, None)
            for job in self.jobs.values():
                if (job["library"], job["view"]) == key and job["status"] in {
                    "collecting",
                    "capturing",
                }:
                    raise PdkUnavailable(
                        "collection_active", "Cancel the active PDK collection before refresh"
                    )
        if len(self.jobs) >= 32:
            raise PdkUnavailable("collection_limit", "PDK collection task limit reached")
        job = pdk_collection.new_job(
            library, view, expected, self.raw_context, bool(self.pool_factory)
        )
        ref = job["collection_ref"]
        job["categories"] = self._categories(job, context(self.raw_context))
        self.jobs[ref] = job
        self._cancels[ref] = Event()
        self.data.progress(job)
        return self.result(job)

    def _ready_result(self, key):
        return pdk_collection.ready_result(key, self.ready[key])

    def result(self, job):
        return pdk_collection.result(job)

    def _job(self, ref):
        if ref not in self.jobs:
            raise PdkUnavailable(
                "collection_unavailable", "Resume the collection with prepare_pdk_data"
            )
        return self.jobs[ref]

    def advance(self, args):
        with self._collection_lock:
            return self._advance(args)

    def _advance(self, args):
        """Advance exactly one directory or detail batch."""
        job = self._job(args["collection_ref"])
        if job["status"] != "collecting" or args["next_batch"] < job["next_batch"]:
            return self.result(job)
        if args["next_batch"] != job["next_batch"]:
            raise PdkUnavailable("batch_mismatch", "Use the collection's exact next_batch token")
        started = time.monotonic()
        try:
            self._check_cancelled(job)
            info = self.inspect({"view": job["view"]})
            support = job["library"] in self.BASE_LIBRARIES
            if (info["selection_required"] and (not support or len(info["candidates"]) > 1)) or (
                not support and self.selected and self.selected != job["library"]
            ):
                self._stop_pool(job)
                job.update(status="interrupted", code="selection_required")
                return self.require_selection(info)
            self._check_origin(job)
            job["device_scan_started"] = True
            job["status"] = "capturing"
            self.data.progress(job)
            if job["phase"] == "details":
                self._details(job)
                job["next_batch"] += 1
                return self.result(job)
            capture = self.bridge.capture(
                "directory",
                {"library": job["library"], "view": job["view"], "offset": job["scanned_cells"]},
            )
            pdk_collection.record_directory(job, capture)
            if job["phase"] == "details":
                self._details(job)
        except PdkUnavailable as exc:
            self._stop_pool(job)
            job.update(
                status="cancelled" if exc.code == "collection_cancelled" else "failed",
                code=getattr(exc, "code", "collection_interrupted"),
                message=str(exc),
                automatic_resume_allowed=(
                    exc.code != "collection_cancelled" and job.get("retry_count", 0) < 2
                ),
            )
        except Exception:
            self._stop_pool(job)
            job.update(
                status="failed",
                code="collection_interrupted",
                message="Collection interrupted; resume the retained checkpoint",
                automatic_resume_allowed=job.get("retry_count", 0) < 2,
            )
            raise
        finally:
            job["elapsed_seconds"] += time.monotonic() - started
            job["updated_at"] = now()
            self.data.progress(job)
        return self.result(job)

    def _details(self, job):
        """Enrich captured devices and publish the completed directory."""
        items = job["capture"]["data"]["items"]
        original = context(job["capture"]["context"])
        index = self._file_layer(original, job["library"])
        categories_data = job.get("categories")
        if categories_data is None:
            categories_data = self._categories(job, original)
            job["categories"] = categories_data
        pdk_collection.detail_tasks(job, items)
        for row, capture in self._captures(job, items):
            self._check_cancelled(job)
            pdk_capture.record_device(
                job,
                row,
                capture,
                index,
                categories_data,
                pooled=bool(self.pool_factory),
                model_name=self._model_name,
                store_device=self.data.device,
                store_observation=self.data.observation,
            )
            self.data.progress(job)
        job["status"] = "collecting"
        if job["detailed_devices"] == len(items):
            self._stop_pool(job)
            if self.pool_factory:
                self.raw_context = self.bridge.capture("libraries", {})["context"]
                self._check_origin(job)
            quality_data = self._quality(index, job, items, categories_data)
            with self._publication_lock:
                self._check_cancelled(job)
                published = pdk_capture.publish_job(job, quality_data, self.data.publish)
                standard = self.data.publish_standard(published, job.get("standard_observations", {}), index)
                published["standard"] = standard
                coverage = published["payload"]["coverage"]
                self.ready[job["library"], job["view"]] = pdk_collection.published_ready(
                    job, published
                )
                job.update(
                    status="ready",
                    phase="complete",
                    path=published["path"],
                    coverage=coverage,
                    directory_digest=published["digest"],
                    quality=quality_data,
                    standard=standard,
                )

    def cancel(self, args):
        job = self._job(args["collection_ref"])
        with self._publication_lock:
            self._cancels.setdefault(job["collection_ref"], Event()).set()
        pool = self._pools.get(job["collection_ref"])
        if pool:
            pool.cancelled.set()
        with self._collection_lock:
            self._stop_pool(job)
            if job["status"] in {"collecting", "capturing", "failed", "interrupted"}:
                job.update(status="cancelled", updated_at=now(), automatic_resume_allowed=False)
                self.data.progress(job)
            return self.result(job)

    def _check_cancelled(self, job):
        from .pdk_pool import check_cancelled as pool_check_cancelled

        pool_check_cancelled()
        if self._cancels.setdefault(job["collection_ref"], Event()).is_set():
            raise PdkUnavailable("collection_cancelled", "PDK collection cancelled")

    def _check_origin(self, job):
        current, original = context(self.raw_context), context(job["capture"]["context"])
        if binding(current, job["library"]) != job["expected_binding"] or any(
            current[k] != original[k] for k in ("session_ref", "session_generation", "project_ref")
        ):
            raise PdkUnavailable(
                "collection_changed", "PDK session/library changed during collection"
            )

    def _stop_pool(self, job):
        pool = self._pools.get(job["collection_ref"])
        if pool:
            pool.close()
            del self._pools[job["collection_ref"]]

    def close(self):
        for cancel_event in tuple(self._cancels.values()):
            cancel_event.set()
        for pool in tuple(self._pools.values()):
            pool.cancelled.set()
        with self._collection_lock:
            for job in self.jobs.values():
                self._stop_pool(job)
                if job["status"] in {"collecting", "capturing"}:
                    job.update(status="interrupted", updated_at=now())
                    self.data.progress(job)

    def _captures(self, job, items):
        """Preparation hook; capture strategy shares the owned job/pool state."""
        yield from pdk_capture.captures(
            job, items, capture=self.bridge.capture, pool_factory=self.pool_factory,
            pools=self._pools, root=self.data.root, environment=self.data.environment,
        )

    def directory(self, args):
        # Refresh the cheap library/technology view before every unqualified
        # search so a stale selected PDK cannot bypass a changed session.
        info = self.inspect(args)
        library = args.get("library") or self.selected
        key = (library, args.get("view", "symbol"))
        prepared = self.ready.get(key)
        if library not in BASE_LIBRARIES and (
            info["selection_required"] or (self.selected and library != self.selected)
        ):
            return None, self.require_selection(info)
        if (
            library in BASE_LIBRARIES
            and library not in BUILTIN_LIBRARIES
            and len(info["candidates"]) > 1
            and info["selection_required"]
        ):
            return None, info
        if prepared:
            return deepcopy(prepared["capture"]), prepared["source"]
        result = self.prepare({"library": library, "view": args.get("view", "symbol")})
        prepared = self.ready.get((result.get("library"), args.get("view", "symbol")))
        if result.get("status") == "ready" and prepared:
            return deepcopy(prepared["capture"]), prepared["source"]
        return None, result
