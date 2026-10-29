# SPDX-License-Identifier: GPL-2.0
"""SAT solver backends behind one protocol.

``builtin`` is the pure Python solver in cdcl.py and the reference.
``picosat`` loads the PicoSAT library if it is installed. Trellis only
asks a backend whether clauses can hold under assumptions, and then for
a model or a core, and the choices it makes from the answers do not
depend on which model a backend returns, so every backend gives the same
configuration.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from ..errors import SolverUnavailable
from ..logic.cnf import Cnf
from .cdcl import CdclSolver

Backend = Literal["builtin", "picosat"]
BACKENDS: tuple[Backend, ...] = ("builtin", "picosat")


class Solver(Protocol):
    """What Trellis needs from a SAT solver."""

    def add_clause(self, clause: Sequence[int]) -> None:
        """Add a clause."""
        ...

    def solve(self, assumptions: Sequence[int] = ()) -> bool:
        """Whether the clauses and ``assumptions`` can all hold."""
        ...

    def model(self) -> set[int]:
        """Return the true literals of the last satisfying assignment."""
        ...

    def core(self) -> list[int]:
        """After an unsatisfiable solve, the assumptions it needed."""
        ...


def make_solver(cnf: Cnf, backend: Backend = "builtin") -> Solver:
    """Return a solver of the given backend holding the clauses of ``cnf``."""
    solver: Solver
    if backend == "builtin":
        solver = CdclSolver(cnf.variables)
    elif backend == "picosat":
        from .picosat import PicosatSolver

        solver = PicosatSolver(cnf.variables)
    else:
        raise SolverUnavailable(f"unknown solver backend {backend!r}")
    for clause in cnf.clauses:
        solver.add_clause(clause)
    return solver


__all__ = ["BACKENDS", "Backend", "CdclSolver", "Solver", "make_solver"]
