"""Bound methods connected to Qt signals must absorb the signal's arguments.

PyQt truncates arguments for plain Python callables it can inspect, so the source
tree tolerates a zero-argument method connected to ``clicked(bool)``. The shipped
Cython build forwards the argument anyway, and an unhandled TypeError in a slot
calls ``qFatal``/``abort`` (the 2026-09-17 audit-answer crash). Every method
connected to a signal therefore accepts that signal's arguments, optionally.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).absolute().parents[3] / "sico/python/sico_ui"
# Arguments carried by the signals this UI connects. Custom pyqtSignal
# declarations found in the scanned module override/extend this table.
SIGNALS = {
    "clicked": 1,
    "toggled": 1,
    "triggered": 1,
    "textChanged": 1,
    "textEdited": 1,
    "currentIndexChanged": 1,
    "currentChanged": 1,
    "currentItemChanged": 2,
    "itemClicked": 2,
    "itemActivated": 1,
    "itemDoubleClicked": 2,
    "doubleClicked": 2,
    "activated": 1,
    "customContextMenuRequested": 1,
    "sectionClicked": 1,
    "valueChanged": 1,
    "stateChanged": 1,
    "finished": 1,
    "opened": 1,
    "linkActivated": 1,
    "anchorClicked": 1,
    "rangeChanged": 2,
    "editingFinished": 0,
    "returnPressed": 0,
    "rejected": 0,
    "canceled": 0,
    "destroyed": 0,
    "timeout": 0,
    "aboutToHide": 0,
    "aboutToQuit": 0,
}


def _accepts(node, count):
    """Can this function body take ``count`` signal arguments?"""
    args = node.args
    names = [item.arg for item in args.posonlyargs + args.args if item.arg not in ("self", "cls")]
    required = len(names) - len(args.defaults)
    optional = len(names) - required
    if args.vararg:
        return True
    if required > count:
        return False
    return required == count or optional > 0


def _methods(tree):
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(node.name, []).append(node)
    return found


def _custom_signals(tree):
    signals = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if ((isinstance(func, ast.Attribute) and func.attr == "pyqtSignal")
                or (isinstance(func, ast.Name) and func.id == "pyqtSignal")):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    signals[target.id] = len(node.value.args)
    return signals


def _loop_callbacks(tree):
    """Methods bound to a loop variable, for example duplicated action rows."""
    bound = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, (ast.Tuple, ast.List)):
            continue
        if not isinstance(node.target, (ast.Tuple, ast.List)):
            continue
        names = [item.id for item in node.target.elts if isinstance(item, ast.Name)]
        for row in node.iter.elts:
            if not isinstance(row, (ast.Tuple, ast.List)) or len(row.elts) != len(names):
                continue
            for name, value in zip(names, row.elts):
                if (
                    isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "self"
                ):
                    bound.setdefault(name, []).append(value.attr)
    return bound


def _offenders(path):
    tree = ast.parse(path.read_text())
    signals = {**SIGNALS, **_custom_signals(tree)}
    page_signals = _custom_signals(ast.parse((ROOT / "session_activation.py").read_text()))
    methods = _methods(tree)
    loops = _loop_callbacks(tree)
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "connect" and node.args):
            continue
        if not isinstance(func.value, ast.Attribute):
            continue
        count = signals.get(func.value.attr)
        receiver = func.value.value
        if ((isinstance(receiver, ast.Name) and receiver.id == "page")
                or (isinstance(receiver, ast.Attribute) and receiver.attr == "page")):
            count = page_signals.get(func.value.attr, count)
        if count is None:
            continue
        target = node.args[0]
        names = []
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            names = [target.attr]
        elif isinstance(target, ast.Name):
            names = loops.get(target.id, [])
        for name in names:
            definitions = methods.get(name)
            if not definitions:
                continue  # Qt's own slot (accept/reject/close/showMinimized)
            if not any(_accepts(definition, count) for definition in definitions):
                problems.append(
                    f"{path.name}:{node.lineno} {func.value.attr}({count}) -> self.{name} "
                    f"defs={[ast.unparse(definition.args) for definition in definitions]}"
                )
    return problems


def test_custom_page_signals_keep_their_declared_argument_count(tmp_path):
    path = tmp_path / "presenter.py"
    path.write_text("class Presenter:\n"
                    "    def __init__(self, page):\n"
                    "        page.activated.connect(self.render)\n"
                    "    def render(self):\n"
                    "        pass\n")
    assert any("activated(2)" in problem for problem in _offenders(path))


def test_python_slots_connected_to_signals_accept_signal_arguments():
    problems = []
    for path in sorted(ROOT.glob("*.py")):
        problems.extend(_offenders(path))
    assert not problems, (
        "Methods connected to Qt signals must tolerate signal arguments because the "
        "native build forwards them; add an ignored parameter (for example "
        "'_checked=False') or '*_args'\n" + "\n".join(problems)
    )
