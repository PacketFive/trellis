# SPDX-License-Identifier: GPL-2.0
"""Kconfig expressions and their tristate logic.

A tristate value is ``n``, ``m`` or ``y``, ordered n < m < y and held as
the integers 0, 1 and 2. ``!E`` is ``2 - E``, ``A && B`` the smaller and
``A || B`` the larger of two values, and a comparison is ``y`` or ``n``.
A bool option only takes ``n`` and ``y``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

TRISTATE = {"n": 0, "m": 1, "y": 2}
VALUE_OF = ("n", "m", "y")


class _Node(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Const(_Node):
    """One of the constants ``n``, ``m`` and ``y``."""

    kind: Literal["const"] = "const"
    value: Literal["n", "m", "y"]


class Sym(_Node):
    """The value of an option."""

    kind: Literal["sym"] = "sym"
    name: str


class Text(_Node):
    """A quoted string or a number, as written."""

    kind: Literal["text"] = "text"
    value: str


class Not(_Node):
    """``!operand``."""

    kind: Literal["not"] = "not"
    operand: Expr


class And(_Node):
    """``left && right``."""

    kind: Literal["and"] = "and"
    left: Expr
    right: Expr


class Or(_Node):
    """``left || right``."""

    kind: Literal["or"] = "or"
    left: Expr
    right: Expr


class Compare(_Node):
    """``left = right`` or ``left != right``."""

    kind: Literal["cmp"] = "cmp"
    op: Literal["=", "!="]
    left: Expr
    right: Expr


Expr = Annotated[Const | Sym | Text | Not | And | Or | Compare, Field(discriminator="kind")]

for _model in (Not, And, Or, Compare):
    _model.model_rebuild()

YES = Const(value="y")
NO = Const(value="n")


def conj(left: Expr, right: Expr) -> Expr:
    """``left && right``, leaving out a side that is the constant ``y``."""
    if left == YES:
        return right
    if right == YES or right == left:
        return left
    if NO in (left, right):
        return NO
    return And(left=left, right=right)


def disj(left: Expr, right: Expr) -> Expr:
    """``left || right``, leaving out a side that is the constant ``n``."""
    if left == NO:
        return right
    if right == NO or right == left:
        return left
    if YES in (left, right):
        return YES
    return Or(left=left, right=right)


def walk(expr: Expr) -> Iterator[Expr]:
    """Every node of ``expr``, the root first, then left before right."""
    stack = [expr]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, Not):
            stack.append(node.operand)
        elif isinstance(node, And | Or | Compare):
            stack.append(node.right)
            stack.append(node.left)


def depth(expr: Expr) -> int:
    """Return the number of nodes on the longest path from the root of ``expr``."""
    deepest = 0
    stack = [(expr, 1)]
    while stack:
        node, level = stack.pop()
        deepest = max(deepest, level)
        if isinstance(node, Not):
            stack.append((node.operand, level + 1))
        elif isinstance(node, And | Or | Compare):
            stack.append((node.left, level + 1))
            stack.append((node.right, level + 1))
    return deepest


def symbols(expr: Expr) -> list[str]:
    """Return the option names in ``expr``, each once, in the order written."""
    names: dict[str, None] = {}
    for node in walk(expr):
        if isinstance(node, Sym):
            names[node.name] = None
    return list(names)


def conjuncts(expr: Expr) -> list[Expr]:
    """Return the terms of ``expr`` joined by ``&&`` at the top."""
    if isinstance(expr, And):
        return conjuncts(expr.left) + conjuncts(expr.right)
    return [expr]


_PRECEDENCE = {"or": 1, "and": 2, "not": 3, "cmp": 4}


def render(expr: Expr, outer: int = 0) -> str:
    """``expr`` in Kconfig syntax, with only the parentheses it needs."""
    if isinstance(expr, Const):
        return expr.value
    if isinstance(expr, Sym):
        return expr.name
    if isinstance(expr, Text):
        return '"' + expr.value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    level = _PRECEDENCE[expr.kind]
    if isinstance(expr, Not):
        text = "!" + render(expr.operand, level)
    elif isinstance(expr, Compare):
        text = f"{render(expr.left, level + 1)} {expr.op} {render(expr.right, level + 1)}"
    else:
        op = " && " if isinstance(expr, And) else " || "
        text = render(expr.left, level) + op + render(expr.right, level)
    return f"({text})" if level < outer else text


def evaluate(expr: Expr, values: Mapping[str, str]) -> int:
    """Return the tristate value of ``expr``, 0 to 2, given option values.

    An option missing from ``values``, or with a value that is not a
    tristate, counts as ``n``.
    """
    if isinstance(expr, Const):
        return TRISTATE[expr.value]
    if isinstance(expr, Sym):
        return TRISTATE.get(values.get(expr.name, "n"), 0)
    if isinstance(expr, Text):
        return 0
    if isinstance(expr, Not):
        return 2 - evaluate(expr.operand, values)
    if isinstance(expr, And):
        return min(evaluate(expr.left, values), evaluate(expr.right, values))
    if isinstance(expr, Or):
        return max(evaluate(expr.left, values), evaluate(expr.right, values))
    same = _operand(expr.left, values) == _operand(expr.right, values)
    return 2 if same == (expr.op == "=") else 0


def _operand(expr: Expr, values: Mapping[str, str]) -> str:
    if isinstance(expr, Sym):
        return values.get(expr.name, "n")
    if isinstance(expr, Const | Text):
        return expr.value
    return VALUE_OF[evaluate(expr, values)]


__all__ = [
    "NO",
    "TRISTATE",
    "VALUE_OF",
    "YES",
    "And",
    "Compare",
    "Const",
    "Expr",
    "Not",
    "Or",
    "Sym",
    "Text",
    "conj",
    "conjuncts",
    "disj",
    "evaluate",
    "render",
    "symbols",
    "walk",
]
