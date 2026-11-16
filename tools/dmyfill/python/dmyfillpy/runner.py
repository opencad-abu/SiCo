"""Run one Calibre ASCII-RDB fill deck and produce a SKILL shape plan."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess

from .deck import DeckError, DeckRequest, rewrite_deck
from .plan import build_shapes, write_plan
from .profile import FillProfile
from .rdb import parse_rdb
from .rule_fill import RuleFillError, generate_rule_deck
from .rules import RuleFillSpec


@dataclass(frozen=True)
class RunResult:
    """Artifacts and counts from one completed Calibre fill run."""

    run_dir: Path
    deck: Path
    rdb: Path
    plan: Path
    manifest: Path
    shape_count: int
    rule_count: int


def _run_calibre(
    deck_path: Path,
    root: Path,
    *,
    calibre: str,
    calibre_args: tuple[str, ...],
    log_path: Path,
    rdb_path: Path,
) -> str:
    executable = shutil.which(calibre)
    if executable is None:
        raise FileNotFoundError(f"Calibre executable is unavailable: {calibre}")
    command = [executable, "-drc", *calibre_args, str(deck_path)]
    print("[MDF] starting Calibre ...", flush=True)
    process = subprocess.Popen(
        command,
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            print(line, end="", flush=True)
        returncode = process.wait()
    if returncode != 0:
        raise RuntimeError(
            f"Calibre fill failed with exit code {returncode}; see {log_path}"
        )
    if not rdb_path.is_file() or rdb_path.stat().st_size == 0:
        raise RuntimeError(f"Calibre did not create the ASCII RDB: {rdb_path}")
    return executable


def run_fill(
    profile: FillProfile,
    *,
    layout_path: str | Path,
    top_cell: str,
    layers: tuple[str, ...],
    run_dir: str | Path,
    region: tuple[float, float, float, float] | None = None,
    expand_region: bool = False,
    calibre: str = "calibre",
    calibre_args: tuple[str, ...] = (),
) -> RunResult:
    """Generate the deck, run Calibre, parse the RDB, and write a data plan."""
    root = Path(run_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    print(f"[MDF] run directory: {root}", flush=True)
    print(f"[MDF] layers: {', '.join(layers)}", flush=True)
    deck_path = root / "fill.cal"
    rdb_path = root / "fill.rdb"
    summary_path = root / "fill.sum"
    log_path = root / "calibre.log"
    plan_path = root / "fill.plan"
    manifest_path = root / "fill.manifest.json"
    effective_region = region
    if region is not None and expand_region and profile.region_margin > 0:
        x1, y1, x2, y2 = region
        margin = profile.region_margin
        effective_region = (x1 - margin, y1 - margin, x2 + margin, y2 + margin)
    source_path = profile.deck
    print(f"[MDF] rule: {source_path}", flush=True)
    try:
        source = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DeckError(f"cannot read fill deck {source_path}: {exc}") from exc
    print("[MDF] generating Calibre deck ...", flush=True)
    generated = rewrite_deck(
        profile,
        source,
        DeckRequest(
            layout_path=Path(layout_path).expanduser().resolve(),
            top_cell=top_cell,
            output_rdb=rdb_path,
            summary_path=summary_path,
            selected_layers=layers,
            region=effective_region,
        ),
    )
    deck_path.write_text(generated.text, encoding="utf-8")
    executable = _run_calibre(
        deck_path,
        root,
        calibre=calibre,
        calibre_args=calibre_args,
        log_path=log_path,
        rdb_path=rdb_path,
    )
    print("[MDF] parsing ASCII RDB ...", flush=True)
    document = parse_rdb(rdb_path, profile.rule_names)
    shapes = build_shapes(document, profile)
    write_plan(plan_path, shapes, run_id=root.name, top_cell=document.top_cell)
    print(
        f"[MDF] plan ready: {plan_path} ({len(shapes)} shapes)",
        flush=True,
    )
    manifest = {
        "schema": "sico.dmyfill.run.v1",
        "profile": str(profile.path),
        "deck": str(deck_path),
        "encrypted_sha256": generated.encrypted_sha256,
        "removed_check_maps": generated.removed_check_maps,
        "layout": str(Path(layout_path).expanduser().resolve()),
        "top_cell": document.top_cell,
        "layers": list(layers),
        "region": list(effective_region) if effective_region is not None else None,
        "requested_region": list(region) if region is not None else None,
        "calibre": executable,
        "calibre_args": list(calibre_args),
        "rdb": str(rdb_path),
        "summary": str(summary_path),
        "log": str(log_path),
        "plan": str(plan_path),
        "shape_count": len(shapes),
        "rule_count": len({shape.rule for shape in shapes}),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return RunResult(
        root,
        deck_path,
        rdb_path,
        plan_path,
        manifest_path,
        len(shapes),
        len({shape.rule for shape in shapes}),
    )


def run_rule_fill(
    spec: RuleFillSpec,
    *,
    layout_path: str | Path,
    top_cell: str,
    layers: tuple[str, ...],
    run_dir: str | Path,
    region: tuple[float, float, float, float] | None = None,
    expand_region: bool = False,
    calibre: str = "calibre",
    calibre_args: tuple[str, ...] = (),
) -> RunResult:
    """Generate a rule-driven deck and apply its bbox plan."""
    if region is None:
        raise RuleFillError("rule fill requires an explicit region")
    root = Path(run_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    print(f"[MDF] run directory: {root}", flush=True)
    print(f"[MDF] layers: {', '.join(layers)}", flush=True)
    print(f"[MDF] source: rule ({spec.source})", flush=True)
    deck_path = root / "fill.cal"
    rdb_path = root / "fill.rdb"
    summary_path = root / "fill.sum"
    log_path = root / "calibre.log"
    plan_path = root / "fill.plan"
    manifest_path = root / "fill.manifest.json"
    generated = generate_rule_deck(
        spec,
        layout_path=layout_path,
        top_cell=top_cell,
        output_rdb=rdb_path,
        summary_path=summary_path,
        layers=layers,
        region=region,
    )
    deck_path.write_text(generated.text, encoding="utf-8")
    executable = _run_calibre(
        deck_path,
        root,
        calibre=calibre,
        calibre_args=calibre_args,
        log_path=log_path,
        rdb_path=rdb_path,
    )
    print("[MDF] parsing ASCII RDB bbox ...", flush=True)
    document = parse_rdb(rdb_path, generated.rule_names)
    shapes = build_shapes(document, spec.rules)
    write_plan(plan_path, shapes, run_id=root.name, top_cell=document.top_cell)
    print(f"[MDF] plan ready: {plan_path} ({len(shapes)} shapes)", flush=True)
    manifest = {
        "schema": "sico.dmyfill.run.v1",
        "source": "rule",
        "rule": str(spec.path),
        "rule_source": spec.source,
        "deck": str(deck_path),
        "layout": str(Path(layout_path).expanduser().resolve()),
        "top_cell": document.top_cell,
        "layers": list(layers),
        "region": list(region),
        "requested_region": list(region),
        "calibre": executable,
        "calibre_args": list(calibre_args),
        "rdb": str(rdb_path),
        "summary": str(summary_path),
        "log": str(log_path),
        "plan": str(plan_path),
        "shape_count": len(shapes),
        "rule_count": len({shape.rule for shape in shapes}),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return RunResult(
        root,
        deck_path,
        rdb_path,
        plan_path,
        manifest_path,
        len(shapes),
        len({shape.rule for shape in shapes}),
    )
