"""Adapt foundry LVS examples to run-owned paths and design controls."""

from __future__ import annotations

import re
from pathlib import Path

from .config import DesignContext, RceConfig
from .process_inputs import optional_input_file
from .textutil import q, write_text


_CONTROL = re.compile(
    r"^\s*(?:(?:LAYOUT|SOURCE)\s+(?:PATH|PRIMARY|SYSTEM)|"
    r"MASK\s+SVDB\s+DIRECTORY|ERC\s+RESULTS\s+DATABASE|"
    r"LVS\s+REPORT(?!\s+(?:MAXIMUM|OPTION)\b))\b", re.I,
)
_INCLUDE = re.compile(r'^\s*INCLUDE\s+(?:"([^"\n]+)"|([^\s]+))(.*)$', re.I)
_COMMENTS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?(?:\*/|\Z)', re.S)


def _active_text(path: Path) -> str:
    def replace(match: re.Match) -> str:
        token = match[0]
        if token.startswith('"'):
            return token
        if token.startswith("/*") and not token.endswith("*/"):
            raise ValueError(f"Unclosed SVRF comment in {path}")
        return " " + "\n" * token.count("\n")
    return _COMMENTS.sub(replace, path.read_text(encoding="utf-8"))


def finfet_lvs_deck(cfg: RceConfig, ctx: DesignContext) -> str:
    return _adapt_deck(cfg, ctx, write_files=True)[0]


def finfet_lvs_files(cfg: RceConfig, ctx: DesignContext) -> tuple[Path, ...]:
    return _adapt_deck(cfg, ctx, write_files=False)[1]


def _adapt_deck(
    cfg: RceConfig, ctx: DesignContext, *, write_files: bool,
) -> tuple[str, tuple[Path, ...]]:
    runset = optional_input_file(cfg, "lvs", "runset_file")
    if not runset or not cfg.flag("extract", "finfet_flow"):
        return runset, ()
    root = Path(runset)
    include_dir = cfg.path("lvs", "include_dir")
    if include_dir and not Path(include_dir).is_dir():
        raise FileNotFoundError(f"Cannot access lvs.include_dir: {include_dir}")
    search = [root.parent, root.parent / ".." / "common_file"]
    if include_dir:
        search.insert(0, Path(include_dir))
    outputs: dict[Path, Path] = {}
    stack: set[Path] = set()
    inputs: set[Path] = set()

    def adapt(path: Path, *, top: bool = False) -> Path:
        path = path.resolve()
        inputs.add(path)
        if path in stack:
            raise ValueError(f"Cyclic FinFET LVS INCLUDE: {path}")
        if path in outputs:
            return outputs[path]
        if path.suffix.lower() in {".encrypt", ".enc"}:
            return path
        output = ctx.log_dir / "lvs_process" / f"{len(outputs):03d}_{path.name}"
        outputs[path] = output
        stack.add(path)
        lines = []
        for line in _active_text(path).splitlines():
            active = line
            if top and _CONTROL.match(active):
                # Preserve foundry QUERY modifiers (notably SI for multi-device
                # extraction) and their conditional branches, replacing only
                # the example database location.
                svdb = re.match(r'^\s*MASK\s+SVDB\s+DIRECTORY\s+(?:"[^"]*"|\S+)(.*)$', active, re.I)
                if svdb and ctx.svdb_dir:
                    lines.append(f'MASK SVDB DIRECTORY "{q(ctx.svdb_dir)}"{svdb[1]}')
                continue
            match = _INCLUDE.match(active)
            if match:
                raw = match[1] or match[2]
                target = cfg.resolve_path(raw, base=path.parent)
                if not target.is_file():
                    candidates = [base / raw for base in search]
                    target = next((p.resolve() for p in candidates if p.is_file()), target)
                if not target.is_file():
                    raise FileNotFoundError(
                        f"Cannot resolve LVS INCLUDE {raw!r} from {path}; "
                        "set RCE_LVS_INCLUDE_DIR to the directory containing its dependencies"
                    )
                line = f'INCLUDE "{q(str(adapt(target)))}"{match[3]}'
            lines.append(line)
        stack.remove(path)
        if write_files:
            write_text(output, "\n".join(lines) + "\n")
        return output

    adapted = adapt(root, top=True)
    return str(adapted), tuple(sorted(inputs))
