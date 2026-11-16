"""Command line interface for the dummy-fill geometry backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .deck import DeckError, DeckRequest, rewrite_deck
from .discover import DiscoveryError, describe
from .layer_map import LayerMapError
from .plan import PlanError, build_shapes, write_plan
from .profile import ProfileError, load_profile
from .rdb import RdbError, parse_rdb
from .rule_fill import RuleFillError
from .rule_source import resolve_fill
from .rules import RuleError
from .runner import run_fill, run_rule_fill


def _layers(value: str) -> tuple[str, ...]:
    layers = tuple(part.strip() for part in value.split(",") if part.strip())
    if not layers:
        raise argparse.ArgumentTypeError("at least one layer is required")
    return layers


def _region(values: list[float] | None) -> tuple[float, float, float, float] | None:
    if values is None:
        return None
    return tuple(values)  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dmyfill")
    sub = parser.add_subparsers(dest="command", required=True)

    deck = sub.add_parser("deck", help="generate an ASCII-RDB Calibre fill deck")
    deck.add_argument("--profile", required=True)
    deck.add_argument("--deck", help="source deck; defaults to profile.deck")
    deck.add_argument("--output", required=True)
    deck.add_argument("--layout", required=True)
    deck.add_argument("--top-cell", required=True)
    deck.add_argument("--results", required=True)
    deck.add_argument("--summary", required=True)
    deck.add_argument("--layers", required=True, type=_layers)
    deck.add_argument("--region", nargs=4, type=float)
    deck.add_argument("--dm-on-corner", choices=("true", "false"))
    deck.add_argument("--with-sealring", choices=("true", "false"))

    plan = sub.add_parser("plan", help="convert a Calibre ASCII RDB to a SKILL apply plan")
    plan.add_argument("--profile", required=True)
    plan.add_argument("--rdb", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--run-id", required=True)
    plan.add_argument("--manifest")

    run = sub.add_parser("run", help="run Calibre and generate a shape plan")
    run.add_argument("--profile", help="profile; defaults to project/PDK discovery")
    run.add_argument("--toml", help="PDK rule TOML override")
    run.add_argument("--layout", required=True)
    run.add_argument("--top-cell", required=True)
    run.add_argument("--layers", required=True, type=_layers)
    run.add_argument("--run-dir", required=True)
    run.add_argument("--region", nargs=4, type=float)
    run.add_argument("--expand-region", action="store_true")
    run.add_argument("--calibre", default="calibre")
    run.add_argument("--calibre-arg", action="append", default=[])

    resolve = sub.add_parser(
        "resolve", help="resolve the project dummy-fill profile"
    )
    resolve.add_argument("--profile")
    resolve.add_argument("--deck")
    resolve.add_argument("--toml")
    resolve.add_argument("--layer-map")
    resolve.add_argument("--output", required=True)
    return parser


def _bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "true"


def _deck_command(args) -> int:
    profile = load_profile(args.profile)
    source_path = Path(args.deck).expanduser().resolve() if args.deck else profile.deck
    try:
        source = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DeckError(f"cannot read fill deck {source_path}: {exc}") from exc
    result = rewrite_deck(
        profile,
        source,
        DeckRequest(
            layout_path=Path(args.layout).expanduser().resolve(),
            top_cell=args.top_cell,
            output_rdb=Path(args.results).expanduser().resolve(),
            summary_path=Path(args.summary).expanduser().resolve(),
            selected_layers=tuple(args.layers),
            region=_region(args.region),
            dm_on_corner=_bool(args.dm_on_corner),
            with_sealring=_bool(args.with_sealring),
        ),
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.text, encoding="utf-8")
    print(
        json.dumps(
            {
                "deck": str(output),
                "encrypted_sha256": result.encrypted_sha256,
                "removed_check_maps": result.removed_check_maps,
                "layers": list(args.layers),
            },
            ensure_ascii=True,
        )
    )
    return 0


def _plan_command(args) -> int:
    profile = load_profile(args.profile)
    document = parse_rdb(args.rdb, profile.rule_names)
    shapes = build_shapes(document, profile)
    output = write_plan(
        args.output,
        shapes,
        run_id=args.run_id,
        top_cell=document.top_cell,
    )
    manifest = {
        "profile": str(profile.path),
        "rdb": str(Path(args.rdb).expanduser().resolve()),
        "plan": str(output),
        "top_cell": document.top_cell,
        "precision": document.precision,
        "shape_count": len(shapes),
        "rule_count": len({shape.rule for shape in shapes}),
    }
    if args.manifest:
        path = Path(args.manifest).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n")
    print(json.dumps(manifest, ensure_ascii=True))
    return 0


def _run_command(args) -> int:
    if args.profile:
        result = run_fill(
            load_profile(args.profile),
            layout_path=args.layout,
            top_cell=args.top_cell,
            layers=tuple(args.layers),
            run_dir=args.run_dir,
            region=_region(args.region),
            expand_region=bool(args.expand_region),
            calibre=args.calibre,
            calibre_args=tuple(args.calibre_arg),
        )
    else:
        found = resolve_fill(toml=args.toml)
        for warning in found.warnings:
            print(f"[MDF] warning: {warning}", flush=True)
        if found.rule is not None:
            result = run_rule_fill(
                found.rule,
                layout_path=args.layout,
                top_cell=args.top_cell,
                layers=tuple(args.layers),
                run_dir=args.run_dir,
                region=_region(args.region),
                expand_region=bool(args.expand_region),
                calibre=args.calibre,
                calibre_args=tuple(args.calibre_arg),
            )
        else:
            result = run_fill(
                found.profile,
                layout_path=args.layout,
                top_cell=args.top_cell,
                layers=tuple(args.layers),
                run_dir=args.run_dir,
                region=_region(args.region),
                expand_region=bool(args.expand_region),
                calibre=args.calibre,
                calibre_args=tuple(args.calibre_arg),
            )
    print(
        json.dumps(
            {
                "run_dir": str(result.run_dir),
                "deck": str(result.deck),
                "rdb": str(result.rdb),
                "plan": str(result.plan),
                "manifest": str(result.manifest),
                "shape_count": result.shape_count,
                "rule_count": result.rule_count,
            },
            ensure_ascii=True,
        )
    )
    return 0


def _resolve_command(args) -> int:
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        found = resolve_fill(
            explicit=args.profile,
            deck=args.deck,
            toml=args.toml,
            layer_map=args.layer_map,
        )
        fields = describe(found)
        lines = [
            "schema=sico.dmyfill.resolve.v1",
            f"profile={fields['profile']}",
            f"rule={fields['rule']}",
            f"mode={'rule' if found.rule is not None else 'deck'}",
            f"source={fields['source']}",
            f"deck={fields['deck']}",
            f"deck_source={fields['deck_source']}",
            f"rule_source={fields.get('rule_source', '')}",
            f"fill_source={fields['fill_source']}",
            f"fill_source_path={fields['fill_source_path']}",
            f"layer_map={fields['layer_map']}",
            f"region_margin={fields['region_margin']}",
            f"note={fields['note']}",
        ]
        lines.extend(f"layer={name}" for name in fields["layers"])
        lines.extend(f"warning={text}" for text in fields["warnings"])
        code = 0
    except (DiscoveryError, ProfileError, OSError) as exc:
        lines = ["schema=sico.dmyfill.resolve.v1", f"error={exc}"]
        code = 1
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "ok": code == 0}, ensure_ascii=True))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "deck":
            return _deck_command(args)
        if args.command == "plan":
            return _plan_command(args)
        if args.command == "resolve":
            return _resolve_command(args)
        return _run_command(args)
    except (
        DeckError,
        DiscoveryError,
        LayerMapError,
        PlanError,
        ProfileError,
        RdbError,
        RuleError,
        RuleFillError,
        OSError,
    ) as exc:
        print(f"dmyfill: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
