# SPDX-License-Identifier: GPL-2.0
"""Clauses in conjunctive normal form, built with the Tseitin transformation.

Literals are DIMACS integers: variable ``v`` is ``v`` when true and
``-v`` when false. Variable 1 is the constant true, fixed by a unit
clause, so ``TRUE`` is 1 and ``FALSE`` is -1. Each gate gets a fresh
variable defined to be equal to its result, and the same gate on the
same inputs reuses its variable, so a formula grows linearly with the
expressions it encodes.
"""

from __future__ import annotations

from collections.abc import Iterable

from espalier import BaseModel, ConfigDict

TRUE = 1
FALSE = -1


class Cnf(BaseModel):
    """A finished set of clauses, with names for the variables that have them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    variables: int
    clauses: tuple[tuple[int, ...], ...]
    names: dict[int, str] = {}

    def to_dimacs(self) -> str:
        """Return the clauses in DIMACS format, names as ``c`` comment lines."""
        lines = [f"c {v} {name}" for v, name in sorted(self.names.items())]
        lines.append(f"p cnf {self.variables} {len(self.clauses)}")
        lines += [" ".join(map(str, clause)) + " 0" for clause in self.clauses]
        return "\n".join(lines) + "\n"


def from_dimacs(text: str) -> Cnf:
    """Read DIMACS text, such as written by Cnf.to_dimacs."""
    names: dict[int, str] = {}
    clauses: list[tuple[int, ...]] = []
    current: list[int] = []
    variables = 0
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "c":
            if len(parts) >= 3 and parts[1].isdigit():
                names[int(parts[1])] = " ".join(parts[2:])
            continue
        if parts[0] == "p":
            variables = int(parts[2])
            continue
        for number in map(int, parts):
            if number == 0:
                clauses.append(tuple(current))
                current = []
            else:
                current.append(number)
    return Cnf(variables=variables, clauses=tuple(clauses), names=names)


class Builder:
    """Collects clauses and defines gate variables."""

    def __init__(self) -> None:
        """Start with variable 1 fixed to true."""
        self.variables = 1
        self.clauses: list[tuple[int, ...]] = [(TRUE,)]
        self.names: dict[int, str] = {1: "true"}
        self._gates: dict[tuple[str, tuple[int, ...]], int] = {}

    def new(self, name: str | None = None) -> int:
        """Return a fresh variable, named if ``name`` is given."""
        self.variables += 1
        if name is not None:
            self.names[self.variables] = name
        return self.variables

    def add(self, *literals: int) -> None:
        """Add a clause, dropping false literals and clauses that always hold."""
        kept: list[int] = []
        for literal in literals:
            if literal == TRUE or -literal in kept:
                return
            if literal != FALSE and literal not in kept:
                kept.append(literal)
        self.clauses.append(tuple(kept))

    def all_of(self, literals: Iterable[int]) -> int:
        """Return a literal true exactly when every one of ``literals`` is."""
        inputs: list[int] = []
        for literal in literals:
            if literal == FALSE or -literal in inputs:
                return FALSE
            if literal != TRUE and literal not in inputs:
                inputs.append(literal)
        if not inputs:
            return TRUE
        if len(inputs) == 1:
            return inputs[0]
        key = ("and", tuple(sorted(inputs)))
        if key in self._gates:
            return self._gates[key]
        gate = self.new()
        for literal in inputs:
            self.add(-gate, literal)
        self.add(gate, *(-literal for literal in inputs))
        self._gates[key] = gate
        return gate

    def any_of(self, literals: Iterable[int]) -> int:
        """Return a literal true exactly when at least one of ``literals`` is."""
        return -self.all_of(-literal for literal in literals)

    def same(self, left: int, right: int) -> int:
        """Return a literal true exactly when ``left`` and ``right`` are equal."""
        if left == right:
            return TRUE
        if left == -right:
            return FALSE
        if left in (TRUE, FALSE):
            return right if left == TRUE else -right
        if right in (TRUE, FALSE):
            return left if right == TRUE else -left
        key = ("same", tuple(sorted((left, right))))
        if key in self._gates:
            return self._gates[key]
        gate = self.new()
        self.add(-gate, -left, right)
        self.add(-gate, left, -right)
        self.add(gate, left, right)
        self.add(gate, -left, -right)
        self._gates[key] = gate
        return gate

    def finish(self) -> Cnf:
        """Return the clauses collected so far."""
        return Cnf(variables=self.variables, clauses=tuple(self.clauses), names=dict(self.names))


__all__ = ["FALSE", "TRUE", "Builder", "Cnf", "from_dimacs"]
