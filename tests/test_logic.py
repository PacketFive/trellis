# SPDX-License-Identifier: GPL-2.0
"""Expressions, clauses and the SAT solvers."""

import itertools
import random

import pytest

from trellis import SolverUnavailable
from trellis.logic.cnf import FALSE, TRUE, Builder, Cnf, from_dimacs
from trellis.logic.expr import (
    NO,
    YES,
    And,
    Compare,
    Const,
    Not,
    Or,
    Sym,
    Text,
    conj,
    conjuncts,
    disj,
    evaluate,
    render,
    symbols,
)
from trellis.solver import BACKENDS, make_solver
from trellis.solver.cdcl import CdclSolver

A, B, C = Sym(name="A"), Sym(name="B"), Sym(name="C")


def test_simplify_and_walk() -> None:
    assert conj(YES, A) == A and conj(A, YES) == A and conj(A, A) == A
    assert conj(A, NO) == NO
    assert disj(NO, A) == A and disj(A, NO) == A and disj(A, YES) == YES
    both = conj(A, conj(B, C))
    assert [render(e) for e in conjuncts(both)] == ["A", "B", "C"]
    expr = Or(left=And(left=A, right=Not(operand=B)), right=Compare(op="=", left=C, right=A))
    assert symbols(expr) == ["A", "B", "C"]
    assert render(expr) == "A && !B || C = A"
    assert render(And(left=Or(left=A, right=B), right=C)) == "(A || B) && C"
    assert render(Not(operand=Or(left=A, right=B))) == "!(A || B)"
    assert render(Text(value='a"b')) == '"a\\"b"'


@pytest.mark.parametrize(
    ("expr", "value"),
    [
        (A, 2),
        (Not(operand=B), 1),
        (And(left=A, right=B), 1),
        (Or(left=B, right=C), 1),
        (Compare(op="=", left=B, right=Const(value="m")), 2),
        (Compare(op="!=", left=A, right=B), 2),
        (Compare(op="=", left=C, right=Const(value="n")), 2),
        (Compare(op="=", left=Not(operand=A), right=Const(value="n")), 2),
        (Text(value="x"), 0),
    ],
)
def test_evaluate(expr: object, value: int) -> None:
    assert evaluate(expr, {"A": "y", "B": "m"}) == value  # type: ignore[arg-type]


def test_builder_gates() -> None:
    builder = Builder()
    x, y = builder.new("x"), builder.new("y")
    assert builder.all_of([]) == TRUE and builder.all_of([x, FALSE]) == FALSE
    assert builder.all_of([x, -x]) == FALSE and builder.all_of([x, TRUE]) == x
    gate = builder.all_of([x, y])
    assert builder.all_of([y, x]) == gate
    assert builder.any_of([x, y]) == -builder.all_of([-x, -y])
    assert builder.same(x, x) == TRUE and builder.same(x, -x) == FALSE
    assert builder.same(TRUE, y) == y and builder.same(x, FALSE) == -x
    equal = builder.same(x, y)
    assert builder.same(y, x) == equal
    cnf = builder.finish()
    for a, b in itertools.product((False, True), repeat=2):
        solver = make_solver(cnf)
        assumptions = [x if a else -x, y if b else -y]
        assert solver.solve(assumptions)
        model = solver.model()
        assert (gate in model) == (a and b)
        assert (equal in model) == (a == b)


def test_builder_add() -> None:
    builder = Builder()
    x = builder.new()
    builder.add(x, TRUE)
    builder.add(x, -x)
    builder.add(FALSE, x, x)
    assert builder.finish().clauses == ((TRUE,), (x,))


def test_dimacs_round_trip() -> None:
    cnf = Cnf(variables=3, clauses=((1,), (-2, 3), ()), names={1: "true", 2: "A>=m"})
    text = cnf.to_dimacs()
    assert text.splitlines()[:3] == ["c 1 true", "c 2 A>=m", "p cnf 3 3"]
    assert from_dimacs(text) == cnf
    assert from_dimacs("c note\n\np cnf 2 1\n1 -2\n0\n").clauses == ((1, -2),)


def _brute(variables: int, clauses: list[list[int]], assumptions: list[int]) -> bool:
    for bits in itertools.product((False, True), repeat=variables):
        true = {v if bits[v - 1] else -v for v in range(1, variables + 1)}
        if all(a in true for a in assumptions) and all(
            any(lit in true for lit in clause) for clause in clauses
        ):
            return True
    return False


def _backends() -> list[str]:
    found = []
    for backend in BACKENDS:
        try:
            make_solver(Cnf(variables=1, clauses=()), backend)  # type: ignore[arg-type]
        except SolverUnavailable:
            continue
        found.append(backend)
    return found


@pytest.mark.parametrize("backend", _backends())
def test_solvers_against_brute_force(backend: str) -> None:
    generator = random.Random(7)  # noqa: S311
    for _ in range(400):
        variables = generator.randint(1, 8)
        clauses = [
            [
                generator.choice((1, -1)) * generator.randint(1, variables)
                for _ in range(generator.randint(1, 3))
            ]
            for _ in range(generator.randint(0, 30))
        ]
        assumptions = list(
            {generator.choice((1, -1)) * generator.randint(1, variables) for _ in range(3)}
        )
        assumptions = [a for a in assumptions if -a not in assumptions]
        cnf = Cnf(variables=variables, clauses=tuple(tuple(c) for c in clauses))
        solver = make_solver(cnf, backend)  # type: ignore[arg-type]
        expected = _brute(variables, clauses, assumptions)
        assert solver.solve(assumptions) == expected
        if expected:
            model = solver.model()
            assert all(a in model for a in assumptions)
            assert all(any(lit in model for lit in clause) for clause in clauses)
        else:
            core = solver.core()
            assert set(core) <= set(assumptions)
            assert not _brute(variables, clauses, core)
        assert solver.solve() == _brute(variables, clauses, [])


@pytest.mark.parametrize("backend", _backends())
def test_repeated_calls_against_brute_force(backend: str) -> None:
    generator = random.Random(11)  # noqa: S311
    for _ in range(60):
        variables = generator.randint(2, 8)
        clauses = [
            [
                generator.choice((1, -1)) * generator.randint(1, variables)
                for _ in range(generator.randint(1, 3))
            ]
            for _ in range(generator.randint(0, 20))
        ]
        cnf = Cnf(variables=variables, clauses=tuple(tuple(c) for c in clauses))
        solver = make_solver(cnf, backend)  # type: ignore[arg-type]
        assumptions: list[int] = []
        for _ in range(12):
            step = generator.randint(0, 3)
            if step == 0 and assumptions:
                del assumptions[generator.randint(0, len(assumptions) - 1) :]
            elif step == 1 and assumptions:
                assumptions[generator.randint(0, len(assumptions) - 1)] *= -1
            elif step == 2:
                clause = [generator.choice((1, -1)) * generator.randint(1, variables)]
                clauses.append(clause)
                solver.add_clause(clause)
            else:
                assumptions.append(generator.choice((1, -1)) * generator.randint(1, variables))
            expected = _brute(variables, clauses, assumptions)
            assert solver.solve(assumptions) == expected
            if expected:
                model = solver.model()
                assert all(a in model for a in assumptions)
                assert all(any(lit in model for lit in clause) for clause in clauses)
            elif generator.random() < 0.5:
                core = solver.core()
                assert set(core) <= set(assumptions)
                assert not _brute(variables, clauses, core)


def test_builtin_core_survives_added_clause() -> None:
    solver = CdclSolver(3)
    solver.add_clause([-1, -2])
    assert solver.solve([3, 1])
    assert not solver.solve([3, 1, 2])
    solver.add_clause([3])
    assert sorted(solver.core()) == [1, 2]


def test_builtin_grows_and_empty_clause() -> None:
    solver = CdclSolver()
    solver.add_clause([5, -6])
    assert solver.solve([-5])
    assert -6 in solver.model()
    solver.add_clause([])
    assert not solver.solve()
    assert solver.core() == []


def test_unknown_backend() -> None:
    with pytest.raises(SolverUnavailable, match="unknown"):
        make_solver(Cnf(variables=1, clauses=()), "minisat")  # type: ignore[arg-type]


def test_picosat_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from trellis.solver import picosat

    monkeypatch.setenv("TRELLIS_PICOSAT", "/nonexistent/libpicosat.so")
    with pytest.raises(SolverUnavailable):
        picosat.load_library()


@pytest.mark.requires_picosat
def test_picosat_present() -> None:
    if "picosat" not in _backends():
        pytest.skip("libpicosat is not installed")
    solver = make_solver(Cnf(variables=2, clauses=((1, 2), (-1,))), "picosat")
    assert solver.solve()
    assert solver.model() >= {-1, 2}
    assert not solver.solve([-2])
    assert solver.core() == [-2]
