"""Check the exact main commit being pushed in an isolated, disposable checkout."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE).decode().strip()


def check_updates(root, updates):
    root = root.resolve()
    common = Path(git(root, "rev-parse", "--git-common-dir"))
    common = (root / common).resolve() if not common.is_absolute() else common.resolve()
    evidence = common.parent / ".cad/engineering-push"
    evidence.mkdir(parents=True, exist_ok=True)
    for line in updates:
        local_ref, commit, remote_ref, base = line.split()
        if remote_ref != "refs/heads/main":
            continue
        if set(commit) == {"0"}:
            raise ValueError("the source-quality hook does not authorize deleting main")
        if set(base) == {"0"}:
            base = git(root, "rev-parse", commit + "^")
        git(root, "merge-base", "--is-ancestor", base, commit)
        run_dir = Path(tempfile.mkdtemp(prefix=commit[:12] + "-", dir=evidence))
        checkout = run_dir / "checkout"
        subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", "--quiet",
                        str(checkout), commit], check=True)
        try:
            gate = checkout / "tools/utility/engineering_gate.py"
            if not gate.is_file():
                raise ValueError(f"pushed commit lacks the engineering gate: {commit}")
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            command = [sys.executable, "-B", "-s", str(gate), "--base-ref", base,
                       "--output", str(run_dir / "report")]
            result = subprocess.run(command, cwd=checkout, env=env)
            if result.returncode:
                raise ValueError(f"main push rejected by source-quality gate; evidence: {run_dir}")
            print(f"Source-quality check accepted {commit[:12]}; evidence: {run_dir}", flush=True)
        finally:
            # Only this hook's new checkout is removed. Reports remain private;
            # no developer worktree, ignored draft or existing evidence is touched.
            subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force",
                            str(checkout)], check=True)


def main():
    try:
        check_updates(Path(git(Path.cwd(), "rev-parse", "--show-toplevel")), sys.stdin)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
