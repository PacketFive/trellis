# SPDX-License-Identifier: GPL-2.0
"""A small conflict-driven clause-learning SAT solver in pure Python.

It has two watched literals per clause, learns first-UIP clauses,
branches on the variable with the highest activity (the lowest number on
a tie), tries false first and then the value a variable last had, and
restarts on the Luby sequence.
Assumptions are decided first, one level each, and a call keeps the
levels of the assumptions it shares with the start of the last call's.
When they cannot all hold, ``core`` returns those that a proof of that
needed. There is no randomness, so the same clauses and the same calls
give the same answers.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence

_RESTART_BASE = 100
_DECAY = 0.95


def _luby(index: int) -> int:
    size, sequence = 1, 0
    while size < index + 1:
        sequence += 1
        size = 2 * size + 1
    while size - 1 != index:
        size = (size - 1) >> 1
        sequence -= 1
        index %= size
    return 1 << sequence


class CdclSolver:
    """The built-in solver; see the module docstring."""

    def __init__(self, variables: int = 0) -> None:
        """Start empty, with ``variables`` variables."""
        self.variables = 0
        self.assigns: list[int] = [0]
        self.level: list[int] = [0]
        self.reason: list[int | None] = [None]
        self.activity: list[float] = [0.0]
        self.phase: list[int] = [-1]
        self.watches: dict[int, list[int]] = {}
        self.clauses: list[list[int]] = []
        self.trail: list[int] = []
        self.limits: list[int] = []
        self.head = 0
        self.increment = 1.0
        self.heap: list[tuple[float, int]] = []
        self.inconsistent = False
        self._model: set[int] = set()
        self._core: list[int] = []
        self._assumed: list[int] = []
        self._failed: int | None = None
        self.conflicts = 0
        self.grow(variables)

    def grow(self, variables: int) -> None:
        """Make room for variables up to ``variables``."""
        while self.variables < variables:
            self.variables += 1
            self.assigns.append(0)
            self.level.append(0)
            self.reason.append(None)
            self.activity.append(0.0)
            self.phase.append(-1)
            heapq.heappush(self.heap, (0.0, self.variables))

    def value(self, literal: int) -> int:
        """1 if ``literal`` is true, -1 if false, 0 if unassigned."""
        assigned = self.assigns[abs(literal)]
        return assigned if literal > 0 else -assigned

    def add_clause(self, clause: Sequence[int]) -> None:
        """Add a clause; call only between solves."""
        self.grow(max((abs(x) for x in clause), default=0))
        self._settle_core()
        self._backtrack(0)
        kept: list[int] = []
        for literal in clause:
            state = self.value(literal)
            if state == 1 or -literal in kept:
                return
            if state == 0 and literal not in kept:
                kept.append(literal)
        if not kept:
            self.inconsistent = True
        elif len(kept) == 1:
            self._enqueue(kept[0], None)
            if self.propagate() is not None:
                self.inconsistent = True
        else:
            self._attach(kept)

    def _attach(self, clause: list[int]) -> int:
        index = len(self.clauses)
        self.clauses.append(clause)
        self.watches.setdefault(clause[0], []).append(index)
        self.watches.setdefault(clause[1], []).append(index)
        return index

    def _enqueue(self, literal: int, reason: int | None) -> None:
        variable = abs(literal)
        self.assigns[variable] = 1 if literal > 0 else -1
        self.level[variable] = len(self.limits)
        self.reason[variable] = reason
        self.trail.append(literal)

    def _backtrack(self, level: int) -> None:
        if len(self.limits) <= level:
            return
        for literal in self.trail[self.limits[level] :]:
            variable = abs(literal)
            self.phase[variable] = 1 if literal > 0 else -1
            self.assigns[variable] = 0
            self.reason[variable] = None
            heapq.heappush(self.heap, (-self.activity[variable], variable))
        del self.trail[self.limits[level] :]
        del self.limits[level:]
        self.head = len(self.trail)

    def propagate(self) -> int | None:
        """Assign implied literals; the index of a false clause, if one is found."""
        while self.head < len(self.trail):
            false = -self.trail[self.head]
            self.head += 1
            watching = self.watches.get(false, [])
            kept: list[int] = []
            for position, index in enumerate(watching):
                clause = self.clauses[index]
                if clause[0] == false:
                    clause[0], clause[1] = clause[1], clause[0]
                if self.value(clause[0]) == 1:
                    kept.append(index)
                    continue
                for k in range(2, len(clause)):
                    if self.value(clause[k]) != -1:
                        clause[1], clause[k] = clause[k], clause[1]
                        self.watches.setdefault(clause[1], []).append(index)
                        break
                else:
                    kept.append(index)
                    if self.value(clause[0]) == -1:
                        kept += watching[position + 1 :]
                        self.watches[false] = kept
                        self.head = len(self.trail)
                        return index
                    self._enqueue(clause[0], index)
            self.watches[false] = kept
        return None

    def _bump(self, variable: int) -> None:
        self.activity[variable] += self.increment
        if self.activity[variable] > 1e100:
            self.activity = [a * 1e-100 for a in self.activity]
            self.increment *= 1e-100
            self.heap = [(-self.activity[v], v) for v in range(1, self.variables + 1)]
            heapq.heapify(self.heap)
        elif self.assigns[variable] == 0:
            heapq.heappush(self.heap, (-self.activity[variable], variable))

    def analyze(self, conflict: int) -> tuple[list[int], int]:
        """Return the first-UIP clause learnt from a conflict, and the level to go back to."""
        seen: set[int] = set()
        learnt: list[int] = [0]
        pending = 0
        index = len(self.trail) - 1
        current = len(self.limits)
        literal = 0
        clause = self.clauses[conflict]
        while True:
            for other in clause:
                variable = abs(other)
                if other == literal or variable in seen or self.level[variable] == 0:
                    continue
                seen.add(variable)
                self._bump(variable)
                if self.level[variable] == current:
                    pending += 1
                else:
                    learnt.append(other)
            while abs(self.trail[index]) not in seen:
                index -= 1
            literal = self.trail[index]
            index -= 1
            seen.discard(abs(literal))
            pending -= 1
            if pending == 0:
                break
            reason = self.reason[abs(literal)]
            clause = self.clauses[reason] if reason is not None else []
        learnt[0] = -literal
        if len(learnt) == 1:
            return learnt, 0
        deepest = max(range(1, len(learnt)), key=lambda i: self.level[abs(learnt[i])])
        learnt[1], learnt[deepest] = learnt[deepest], learnt[1]
        return learnt, self.level[abs(learnt[1])]

    def analyze_final(self, failed: int) -> list[int]:
        """Return the assumptions that force the assumption ``failed`` to be false."""
        core = [failed]
        if not self.limits:
            return core
        seen = {abs(failed)}
        for literal in reversed(self.trail[self.limits[0] :]):
            variable = abs(literal)
            if variable not in seen:
                continue
            reason = self.reason[variable]
            if reason is None:
                core.append(literal)
            else:
                seen.update(abs(x) for x in self.clauses[reason] if self.level[abs(x)] > 0)
            seen.discard(variable)
        return core

    def _pick(self) -> int | None:
        while self.heap:
            activity, variable = heapq.heappop(self.heap)
            if self.assigns[variable] == 0 and -activity == self.activity[variable]:
                return variable
        for variable in range(1, self.variables + 1):
            if self.assigns[variable] == 0:
                return variable
        return None

    def solve(self, assumptions: Sequence[int] = ()) -> bool:
        """Whether the clauses and ``assumptions`` can all hold."""
        self._core, self._failed = [], None
        # The levels of a shared first part of the assumptions are kept from the last call.
        shared = min(len(self.limits), len(self._assumed), len(assumptions))
        if list(assumptions[:shared]) != self._assumed[:shared]:
            shared = next(i for i in range(shared) if assumptions[i] != self._assumed[i])
        self.grow(max((abs(x) for x in assumptions[shared:]), default=0))
        self._backtrack(shared)
        if self.inconsistent or (shared == 0 and self.propagate() is not None):
            self.inconsistent = True
            return False
        restarts = 0
        budget = _RESTART_BASE * _luby(restarts)
        while True:
            conflict = self.propagate()
            if conflict is not None:
                self.conflicts += 1
                budget -= 1
                if not self.limits:
                    self.inconsistent = True
                    return False
                learnt, level = self.analyze(conflict)
                self._backtrack(level)
                if len(learnt) == 1:
                    self._enqueue(learnt[0], None)
                else:
                    self._enqueue(learnt[0], self._attach(learnt))
                self.increment /= _DECAY
                continue
            if budget <= 0:
                restarts += 1
                budget = _RESTART_BASE * _luby(restarts)
                self._backtrack(0)
                continue
            if len(self.limits) < len(assumptions):
                literal = assumptions[len(self.limits)]
                state = self.value(literal)
                if state == -1:
                    # The core is found when asked for; the trail stays as it is until then.
                    self._failed = literal
                    self._assumed = list(assumptions[: len(self.limits)])
                    return False
                self.limits.append(len(self.trail))
                if state == 0:
                    self._enqueue(literal, None)
                continue
            variable = self._pick()
            if variable is None:
                self._model = {
                    v if self.assigns[v] > 0 else -v for v in range(1, self.variables + 1)
                }
                self._backtrack(len(assumptions))
                self._assumed = list(assumptions)
                return True
            self.limits.append(len(self.trail))
            self._enqueue(variable * self.phase[variable], None)

    def model(self) -> set[int]:
        """Return the true literals of the last satisfying assignment."""
        return set(self._model)

    def core(self) -> list[int]:
        """After an unsatisfiable solve, the assumptions it needed."""
        self._settle_core()
        return list(self._core)

    def _settle_core(self) -> None:
        if self._failed is not None:
            self._core = self.analyze_final(self._failed)
            self._failed = None


__all__ = ["CdclSolver"]
