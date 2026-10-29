# SPDX-License-Identifier: GPL-2.0
"""Resolve the user's choices into a full configuration, the same way every time.

The user's choices are assumptions. The preferences of the tree are then
tried in its order, each kept if everything kept so far can still hold;
an option the user set has its default skipped, as its value is the
user's.
Last, every bool and tristate option, in the order of the tree, is made
as small as it can be. Each step fixes something that a later step
cannot undo, so the result is the same whatever model a solver returns
on the way.

Options of type ``int``, ``hex`` and ``string`` are not part of the
clauses. Once the others are fixed, each whose dependencies hold takes
the user's value, or else its first default whose condition holds, and
must lie in its first range whose condition holds.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from .errors import ConflictError
from .language.checker import parse_number
from .logic.encode import Encoding, encode
from .logic.expr import Expr, Text, conj, evaluate
from .models import Assignment, Configuration, Conflict, OptionTree, Rule, Symbol
from .solver import Backend, Solver, make_solver

_LOGICAL = frozenset({"bool", "tristate"})


class Resolver:
    """Resolves choices against one tree, reusing a solver between calls."""

    def __init__(self, tree: OptionTree, backend: Backend = "builtin") -> None:
        """Encode ``tree`` and give its clauses, with every rule on, to a solver."""
        self.tree = tree
        self.backend: Backend = backend
        self.encoding: Encoding = encode(tree)
        self.solver: Solver = make_solver(self.encoding.cnf, backend)
        for guard in self.encoding.rules:
            self.solver.add_clause([guard])

    def assumptions(self, choices: Sequence[Assignment]) -> list[int]:
        """Return the literals of the bool and tristate choices."""
        literals: list[int] = []
        for choice in choices:
            if self.tree.symbols[choice.symbol].type in _LOGICAL:
                literals += self.encoding.literals(choice.symbol, choice.value)
        return literals

    def model(self, choices: Sequence[Assignment]) -> set[int] | None:
        """Return the resolved assignment, or None if the choices conflict."""
        solver, encoding = self.solver, self.encoding
        kept = self.assumptions(choices)
        if not solver.solve(kept):
            return None
        model = solver.model()
        given = {choice.symbol for choice in choices}
        literals = [p.literal for p in encoding.preferences if p.option not in given]
        kept, model = _try_all(solver, kept, model, literals)
        for name, symbol in self.tree.symbols.items():
            if symbol.type not in _LOGICAL:
                continue
            low, high = encoding.pairs[name]
            kept, model = _try(solver, kept, model, -low)
            if low != high and high not in kept and -low not in kept:
                kept, model = _try(solver, kept, model, -high)
        return model

    def resolve(self, choices: Sequence[Assignment]) -> Configuration:
        """Return the configuration for ``choices``; raise ConflictError if they conflict."""
        start = time.perf_counter()
        model = self.model(choices)
        if model is None:
            from .explain import Explainer

            conflict = Explainer(self.encoding, self.backend).conflict(choices)
            raise ConflictError(conflict or Conflict(choices=tuple(choices)))
        values: dict[str, str] = {}
        for name, symbol in self.tree.symbols.items():
            if symbol.type in _LOGICAL:
                values[name] = self.encoding.value(name, model)
        given = {c.symbol: c for c in choices}
        for name, symbol in self.tree.symbols.items():
            if symbol.type not in _LOGICAL and evaluate(symbol.depends, values) > 0:
                values[name] = _plain_value(symbol, given.get(name), values)
        return Configuration(
            values=values,
            prompts=tuple(n for n, s in self.tree.symbols.items() if self.shown(s, values)),
            user=tuple(choices),
            solve_seconds=time.perf_counter() - start,
        )

    def shown(self, symbol: Symbol, values: dict[str, str]) -> bool:
        """Whether the user can set ``symbol`` under ``values``."""
        if symbol.prompt is None:
            return False
        condition = conj(conj(symbol.prompt_if, symbol.visible), symbol.depends)
        if symbol.choice is not None:
            choice = self.tree.choices[symbol.choice]
            if choice.prompt is None:
                return False
            condition = conj(
                condition, conj(conj(choice.prompt_if, choice.visible), choice.depends)
            )
        return evaluate(condition, values) > 0


def _try(
    solver: Solver, kept: list[int], model: set[int], literal: int
) -> tuple[list[int], set[int]]:
    """Keep ``literal`` if it can hold with ``kept``, else keep its opposite."""
    if literal in model:
        return [*kept, literal], model
    if solver.solve([*kept, literal]):
        return [*kept, literal], solver.model()
    return [*kept, -literal], model


def _try_all(
    solver: Solver, kept: list[int], model: set[int], literals: list[int]
) -> tuple[list[int], set[int]]:
    """Apply ``_try`` to each of ``literals`` in order, with fewer solver calls.

    When they can all hold with ``kept``, trying them one by one keeps every
    one, so a single call decides them all; otherwise each half is tried
    the same way, the first half before the second.
    """
    if len(literals) <= 1:
        for literal in literals:
            kept, model = _try(solver, kept, model, literal)
        return kept, model
    if all(literal in model for literal in literals):
        return [*kept, *literals], model
    if solver.solve([*kept, *literals]):
        return [*kept, *literals], solver.model()
    half = len(literals) // 2
    kept, model = _try_all(solver, kept, model, literals[:half])
    return _try_all(solver, kept, model, literals[half:])


def _plain_value(symbol: Symbol, given: Assignment | None, values: dict[str, str]) -> str:
    """Return the value of an ``int``, ``hex`` or ``string`` option whose dependencies hold."""
    bounds = next((r for r in symbol.ranges if evaluate(r.condition, values) > 0), None)
    if given is not None:
        value = given.value
        if bounds is not None and not (
            parse_number(symbol.type, bounds.low)
            <= parse_number(symbol.type, value)
            <= parse_number(symbol.type, bounds.high)
        ):
            rule = Rule(
                kind="range",
                symbol=symbol.name,
                text=f"{symbol.name} must be between {bounds.low} and {bounds.high}",
                location=symbol.location,
            )
            raise ConflictError(Conflict(choices=(given,), rules=(rule,)))
        return value
    default = next((d for d in symbol.defaults if evaluate(d.condition, values) > 0), None)
    if symbol.type == "string":
        return _text(default.value) if default is not None else ""
    if default is not None:
        text = _text(default.value)
    else:
        text = bounds.low if bounds is not None else ("0x0" if symbol.type == "hex" else "0")
    if bounds is not None:
        number = parse_number(symbol.type, text)
        low, high = parse_number(symbol.type, bounds.low), parse_number(symbol.type, bounds.high)
        if not low <= number <= high:
            text = bounds.low if number < low else bounds.high
    return text


def _text(expr: Expr) -> str:
    return expr.value if isinstance(expr, Text) else ""


__all__ = ["Resolver"]
