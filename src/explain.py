# SPDX-License-Identifier: GPL-2.0
"""Explain why choices conflict and what could change.

Every rule of the tree and every user choice gets a literal that turns
it on, and the solver is asked to satisfy them all as assumptions. When
it cannot, its core names the rules and choices it needed; removing one
at a time those that are not needed leaves a minimal conflict.

Fixes are minimal correction sets: smallest groups of the user's choices
whose removal lets a wanted value hold, as ConfigFix proposes for the
Linux kernel. Each is found by keeping the choices one by one, in order,
while the wanted value can still hold; those left out are a correction
set, and a clause that keeps at least one of them leads to the next.
"""

from __future__ import annotations

from collections.abc import Sequence

from .logic.encode import Encoding
from .models import Assignment, Conflict
from .solver import Backend, Solver, make_solver


class Explainer:
    """Answers conflict and fix questions about one encoded tree."""

    def __init__(self, encoding: Encoding, backend: Backend = "builtin") -> None:
        """Give the clauses to a solver with every rule left as an assumption."""
        self.encoding = encoding
        self.solver: Solver = make_solver(encoding.cnf, backend)
        self.next = encoding.cnf.variables
        self.switches: dict[tuple[str, str], int] = {}
        self.guards = list(encoding.rules)

    def _fresh(self) -> int:
        self.next += 1
        return self.next

    def switch(self, choice: Assignment) -> int:
        """Return a literal that, when assumed, makes ``choice`` hold."""
        key = (choice.symbol, choice.value)
        if key not in self.switches:
            literal = self._fresh()
            for target in self.encoding.literals(choice.symbol, choice.value):
                self.solver.add_clause([-literal, target])
            self.switches[key] = literal
        return self.switches[key]

    def conflict(
        self, choices: Sequence[Assignment], wanted: Sequence[Assignment] = ()
    ) -> Conflict | None:
        """Return a minimal conflict among the rules, ``choices`` and ``wanted``, or None.

        ``wanted`` always holds and is not listed in the result.
        """
        logical = [c for c in choices if c.symbol in self.encoding.pairs]
        fixed = [self.switch(w) for w in wanted]
        by_switch = {self.switch(c): c for c in logical}
        items = [*by_switch, *self.guards]
        if self.solver.solve([*fixed, *items]):
            return None
        needed = set(self.solver.core())
        items = [x for x in items if x in needed]
        for item in list(items):
            trial = [x for x in items if x != item]
            if not self.solver.solve([*fixed, *trial]):
                core = set(self.solver.core())
                items = [x for x in trial if x in core]
        return Conflict(
            choices=tuple(by_switch[x] for x in items if x in by_switch),
            rules=tuple(self.encoding.rules[x] for x in items if x in self.encoding.rules),
        )

    def corrections(
        self, choices: Sequence[Assignment], wanted: Sequence[Assignment], limit: int = 3
    ) -> list[list[Assignment]]:
        """Up to ``limit`` minimal sets of ``choices`` to drop so ``wanted`` can hold.

        An empty list means ``wanted`` cannot hold even without choices; a
        list holding one empty set means it already can.
        """
        hard = [*self.guards, *(self.switch(w) for w in wanted)]
        softs = [self.switch(c) for c in choices if c.symbol in self.encoding.pairs]
        by_switch = {self.switch(c): c for c in choices if c.symbol in self.encoding.pairs}
        session = self._fresh()
        found: list[list[int]] = []
        while len(found) < limit and self.solver.solve([*hard, session]):
            kept: list[int] = []
            for soft in softs:
                if self.solver.solve([*hard, session, *kept, soft]):
                    kept.append(soft)
            dropped = [s for s in softs if s not in kept]
            found.append(dropped)
            if not dropped:
                break
            self.solver.add_clause([-session, *dropped])
        found.sort(key=len)
        return [[by_switch[s] for s in group] for group in found]


__all__ = ["Explainer"]
