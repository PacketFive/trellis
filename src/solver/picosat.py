# SPDX-License-Identifier: GPL-2.0
"""The PicoSAT library as a solver backend, loaded with ctypes.

PicoSAT is the solver ConfigFix uses for the Linux kernel's Kconfig. It is
found as ``libpicosat`` through ctypes.util.find_library, or at the path
in the ``TRELLIS_PICOSAT`` environment variable. Its core is the list of
failed assumptions it reports.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from collections.abc import Sequence

from ..errors import SolverUnavailable

_SATISFIABLE = 10
_UNSATISFIABLE = 20


def load_library() -> ctypes.CDLL:
    """Return the PicoSAT shared library; raise SolverUnavailable if it cannot be found."""
    path = os.environ.get("TRELLIS_PICOSAT") or ctypes.util.find_library("picosat")
    if not path:
        raise SolverUnavailable("libpicosat is not installed; set TRELLIS_PICOSAT to its path")
    try:
        library = ctypes.CDLL(path)
    except OSError as error:
        raise SolverUnavailable(f"cannot load {path}: {error}") from None
    library.picosat_init.restype = ctypes.c_void_p
    for name, args in (
        ("picosat_reset", [ctypes.c_void_p]),
        ("picosat_add", [ctypes.c_void_p, ctypes.c_int]),
        ("picosat_assume", [ctypes.c_void_p, ctypes.c_int]),
        ("picosat_sat", [ctypes.c_void_p, ctypes.c_int]),
        ("picosat_deref", [ctypes.c_void_p, ctypes.c_int]),
        ("picosat_failed_assumption", [ctypes.c_void_p, ctypes.c_int]),
        ("picosat_variables", [ctypes.c_void_p]),
    ):
        getattr(library, name).argtypes = args
    return library


class PicosatSolver:
    """A PicoSAT instance with the methods of the Solver protocol."""

    def __init__(self, variables: int = 0) -> None:
        """Load the library and start an empty instance."""
        self.library = load_library()
        self.handle = ctypes.c_void_p(self.library.picosat_init())
        self.variables = variables
        self._model: set[int] = set()
        self._core: list[int] = []

    def __del__(self) -> None:
        """Free the instance."""
        handle = getattr(self, "handle", None)
        if handle is not None:
            self.library.picosat_reset(handle)
            self.handle = None

    def add_clause(self, clause: Sequence[int]) -> None:
        """Add a clause."""
        for literal in clause:
            self.variables = max(self.variables, abs(literal))
            self.library.picosat_add(self.handle, literal)
        self.library.picosat_add(self.handle, 0)

    def solve(self, assumptions: Sequence[int] = ()) -> bool:
        """Whether the clauses and ``assumptions`` can all hold."""
        for literal in assumptions:
            self.variables = max(self.variables, abs(literal))
            self.library.picosat_assume(self.handle, literal)
        result = self.library.picosat_sat(self.handle, -1)
        if result == _SATISFIABLE:
            deref = self.library.picosat_deref
            self._model = {
                v if deref(self.handle, v) > 0 else -v for v in range(1, self.variables + 1)
            }
            self._core = []
            return True
        if result != _UNSATISFIABLE:
            raise SolverUnavailable(f"picosat returned {result}")
        failed = self.library.picosat_failed_assumption
        self._core = [x for x in assumptions if failed(self.handle, x)]
        return False

    def model(self) -> set[int]:
        """Return the true literals of the last satisfying assignment."""
        return set(self._model)

    def core(self) -> list[int]:
        """After an unsatisfiable solve, the assumptions it needed."""
        return list(self._core)


__all__ = ["PicosatSolver", "load_library"]
