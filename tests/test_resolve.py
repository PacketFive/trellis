# SPDX-License-Identifier: GPL-2.0
"""Resolving choices: the meaning of each rule, conflicts and fixes."""

import itertools
import random
import time

import pytest

from trellis import (
    Assignment,
    Configuration,
    ConflictError,
    OptionTree,
    SolverUnavailable,
    Symbol,
    TreeError,
    check,
    parse_text,
)
from trellis.explain import Explainer
from trellis.logic.cnf import Cnf
from trellis.logic.expr import Expr, conj, evaluate
from trellis.resolve import Resolver
from trellis.solver import BACKENDS, make_solver

DEMO = """\
mainmenu "Demo"
config MODULES
\tbool "Modules"
\tmodules
config NET
\tbool "Networking"
\tdefault y
config ETH
\ttristate "Ethernet"
\tdepends on NET
\tselect PHY
\timply LED
config PHY
\ttristate
config LED
\tbool "LEDs"
config DEBUG
\tbool "Debug" if !NET
\tdefault y
config FAST
\tbool
\tdefault y if ETH
\tdefault n
config MOD
\ttristate "Module"
\tdefault m
choice SCHED
\tprompt "Scheduler"
\tdefault SCHED_B if NET
config SCHED_A
\tbool "A"
config SCHED_B
\tbool "B"
\tdepends on NET
endchoice
choice HIDDEN
\tprompt "Hidden" if !NET
\tdefault H2
config H1
\tbool "H1"
config H2
\tbool "H2"
endchoice
choice OPT
\tprompt "Optional"
\toptional
\tdepends on NET
config O1
\tbool "O1"
endchoice
config BUF
\tint "Buffer"
\trange 4 64 if NET
\tdefault 2
config BASE
\thex "Base"
\tdepends on NET
config NAME
\tstring "Name"
"""


def _tree(text: str = DEMO) -> OptionTree:
    tree = parse_text(text)
    check(tree)
    return tree


def _resolve(*choices: str, text: str = DEMO, backend: str = "builtin") -> dict[str, str]:
    resolver = Resolver(_tree(text), backend)  # type: ignore[arg-type]
    pairs = [c.split("=", 1) for c in choices]
    return resolver.resolve([Assignment(symbol=n, value=v) for n, v in pairs]).values


def test_defaults() -> None:
    values = _resolve()
    assert values == {
        "MODULES": "n", "NET": "y", "ETH": "n", "PHY": "n", "LED": "n", "DEBUG": "y",
        "FAST": "n", "MOD": "y", "SCHED_A": "n", "SCHED_B": "y", "H1": "n", "H2": "y",
        "O1": "n", "BUF": "4", "BASE": "0x0", "NAME": "",
    }  # fmt: skip


def test_select_imply_and_conditional_default() -> None:
    values = _resolve("ETH=y")
    assert values["PHY"] == "y" and values["LED"] == "y" and values["FAST"] == "y"
    values = _resolve("ETH=y", "LED=n")
    assert values["LED"] == "n" and values["PHY"] == "y"
    values = _resolve("ETH=m")
    assert values["MODULES"] == "y" and values["PHY"] == "m"
    assert values["MOD"] == "m"


def test_dependencies() -> None:
    values = _resolve("NET=n")
    assert values["ETH"] == "n" and values["SCHED_A"] == "y" and values["SCHED_B"] == "n"
    assert "BASE" not in values
    assert values["BUF"] == "2"
    assert values["H2"] == "y"
    assert values["O1"] == "n"
    values = _resolve("NET=n", "H1=y")
    assert values["H1"] == "y" and values["H2"] == "n"


def test_hidden_prompt_takes_default() -> None:
    assert _resolve()["DEBUG"] == "y"
    assert _resolve("NET=n", "DEBUG=n")["DEBUG"] == "n"
    with pytest.raises(ConflictError, match="DEBUG takes its default while hidden"):
        _resolve("NET=y", "DEBUG=n")
    assert _resolve("DEBUG=n")["NET"] == "n"


def test_hidden_choice_takes_default() -> None:
    with pytest.raises(ConflictError, match="HIDDEN takes its default while hidden"):
        _resolve("NET=y", "H1=y")


def test_choices() -> None:
    values = _resolve("SCHED_A=y")
    assert values["SCHED_A"] == "y" and values["SCHED_B"] == "n"
    with pytest.raises(ConflictError):
        _resolve("SCHED_A=y", "SCHED_B=y")
    with pytest.raises(ConflictError):
        _resolve("SCHED_A=n", "SCHED_B=n")
    assert _resolve("O1=y")["O1"] == "y"
    assert _resolve("O1=n")["O1"] == "n"


def test_select_of_unmet_dependency_is_a_conflict() -> None:
    text = 'config A\n\tbool "A"\nconfig B\n\tbool "B"\nconfig C\n\tbool\n\tdepends on A\n'
    text += 'config D\n\tbool "D"\n\tselect C\n'
    with pytest.raises(ConflictError) as raised:
        _resolve("A=n", "D=y", text=text)
    conflict = raised.value.conflict
    assert {c.symbol for c in conflict.choices} == {"A", "D"}
    assert {r.kind for r in conflict.rules} == {"select", "depends"}
    assert _resolve("D=y", text=text)["A"] == "y"


def test_modules() -> None:
    with pytest.raises(ConflictError, match="ETH=m needs MODULES"):
        _resolve("MODULES=n", "ETH=m")
    plain = 'config T\n\ttristate "T"\n\tdefault m\nconfig U\n\ttristate\n\tdefault y\n'
    assert _resolve(text=plain) == {"T": "y", "U": "y"}
    with pytest.raises(ConflictError, match="the tree has no modules option"):
        _resolve("T=m", text=plain)


def test_plain_values() -> None:
    values = _resolve("BUF=10", "BASE=0x10", "NAME=x y")
    assert (values["BUF"], values["BASE"], values["NAME"]) == ("10", "0x10", "x y")
    with pytest.raises(ConflictError, match="between 4 and 64"):
        _resolve("BUF=100")
    assert _resolve("NET=n", "BUF=100")["BUF"] == "100"
    text = "config N\n\tint\n\trange 1 5\n\tdefault 9\nconfig M\n\tint\n\trange 3 5\n"
    assert _resolve(text=text) == {"N": "5", "M": "3"}


def test_prompts_and_details() -> None:
    resolver = Resolver(_tree())
    configuration = resolver.resolve([])
    assert "PHY" not in configuration.prompts and "DEBUG" not in configuration.prompts
    assert "H1" not in configuration.prompts and "SCHED_A" in configuration.prompts
    assert configuration.solve_seconds >= 0
    assert len(resolver.encoding.literals("ETH", "m")) == 2


def _working_backends() -> list[str]:
    found = []
    for backend in BACKENDS:
        try:
            make_solver(Cnf(variables=1, clauses=()), backend)  # type: ignore[arg-type]
        except SolverUnavailable:
            continue
        found.append(backend)
    return found


def test_explainer() -> None:
    tree = _tree()
    resolver = Resolver(tree)
    explainer = Explainer(resolver.encoding)
    net_off = Assignment(symbol="NET", value="n")
    led_off = Assignment(symbol="LED", value="n")
    eth = Assignment(symbol="ETH", value="y")
    assert explainer.conflict([net_off]) is None
    conflict = explainer.conflict([net_off, led_off], [eth])
    assert conflict is not None
    assert conflict.choices == (net_off,)
    assert [r.text for r in conflict.rules] == ["ETH depends on NET"]
    assert explainer.corrections([net_off, led_off], [eth]) == [[net_off]]
    assert explainer.corrections([led_off], [eth]) == [[]]
    phy_n = Assignment(symbol="PHY", value="n")
    assert explainer.corrections([], [eth, phy_n]) == []


# An independent statement of the rules, checked against every assignment.


def _expr(generator: random.Random, names: list[str]) -> Expr:
    a, b = generator.choice(names), generator.choice(names)
    return (
        parse_text(
            "config Z\n\tbool\n\tdepends on "
            + generator.choice([a, f"!{a}", f"{a} && {b}", f"{a} || {b}", f"{a} = m", f"{a} != y"])
            + "\n"
        )
        .symbols["Z"]
        .depends
    )


def _random_tree(generator: random.Random) -> OptionTree | None:
    count = generator.randint(2, 5)
    names = [f"S{i}" for i in range(count)]
    lines = ['config MODULES\n\tbool "modules"\n\tmodules\n'] if generator.random() < 0.6 else []
    for index, name in enumerate(names):
        kind = generator.choice(["bool", "tristate"])
        lines.append(f"config {name}\n\t{kind}")
        if generator.random() < 0.7:
            lines.append(
                ' "p"' + (f" if {generator.choice(names)}" if generator.random() < 0.3 else "")
            )
        lines.append("\n")
        earlier = names[:index]
        if earlier and generator.random() < 0.5:
            lines.append(f"\tdepends on {generator.choice(earlier)}\n")
        if generator.random() < 0.5:
            condition = f" if {generator.choice(names)}" if generator.random() < 0.5 else ""
            value = generator.choice(["y", "n", *earlier])
            lines.append(f"\tdefault {value}{condition}\n")
        later = names[index + 1 :]
        if later and generator.random() < 0.4:
            lines.append(f"\tselect {generator.choice(later)}\n")
        if later and generator.random() < 0.3:
            lines.append(f"\timply {generator.choice(later)}\n")
    try:
        tree = parse_text("".join(lines))
        check(tree)
    except TreeError:
        return None
    return tree


def _holds(tree: OptionTree, values: dict[str, str]) -> bool:
    """Whether ``values`` keeps every rule of a tree without choices."""
    selected: dict[str, list[tuple[str, Expr]]] = {}
    implied: dict[str, list[tuple[str, Expr]]] = {}
    for symbol in tree.symbols.values():
        for reverse in symbol.selects:
            selected.setdefault(reverse.symbol, []).append((symbol.name, reverse.condition))
        for reverse in symbol.implies:
            implied.setdefault(reverse.symbol, []).append((symbol.name, reverse.condition))
    rank = {"n": 0, "m": 1, "y": 2}
    modules = tree.modules is not None and values[tree.modules] == "y"
    for name, symbol in tree.symbols.items():
        value = rank[values[name]]

        def up(limit: int, symbol: Symbol = symbol) -> int:
            promote = symbol.type == "bool" or not modules
            return 2 if limit == 1 and promote else limit

        if value == 1 and (
            symbol.type == "bool" or tree.modules is None or values[tree.modules] != "y"
        ):
            return False
        depends = evaluate(symbol.depends, values)
        reverse = max(
            (evaluate(conj(_sym(s), c), values) for s, c in selected.get(name, [])), default=0
        )
        default = next(
            (
                evaluate(conj(d.value, d.condition), values)
                for d in symbol.defaults
                if evaluate(d.condition, values) > 0
            ),
            0,
        )
        weak = max(
            [default, *(evaluate(conj(_sym(s), c), values) for s, c in implied.get(name, []))]
        )
        visible = (
            evaluate(conj(conj(symbol.prompt_if, symbol.visible), symbol.depends), values)
            if symbol.prompt is not None
            else 0
        )
        hidden = min(2 if visible == 0 else 0, weak, depends)
        if not (
            reverse <= value <= up(depends) and hidden <= value <= up(max(visible, reverse, hidden))
        ):
            return False
    return True


def _sym(name: str) -> Expr:
    return parse_text(f"config Z\n\tbool\n\tdepends on {name}\n").symbols["Z"].depends


def test_rules_against_brute_force() -> None:
    generator = random.Random(11)  # noqa: S311
    backends = _working_backends()
    checked = 0
    while checked < 150:
        tree = _random_tree(generator)
        if tree is None:
            continue
        checked += 1
        names = list(tree.symbols)
        settable = [n for n in names if tree.symbols[n].prompt is not None]
        choices = []
        for name in generator.sample(settable, min(len(settable), generator.randint(0, 2))):
            values = ["n", "y", "m"] if tree.symbols[name].type == "tristate" else ["n", "y"]
            choices.append(Assignment(symbol=name, value=generator.choice(values)))
        possible = any(
            _holds(tree, dict(zip(names, combination, strict=True)))
            and all(
                dict(zip(names, combination, strict=True))[c.symbol] == c.value for c in choices
            )
            for combination in itertools.product("nmy", repeat=len(names))
        )
        results: list[Configuration | None] = []
        for backend in backends:
            resolver = Resolver(tree, backend)  # type: ignore[arg-type]
            try:
                results.append(resolver.resolve(choices))
            except ConflictError as error:
                assert not possible, (tree, choices)
                again = Resolver(tree, backend)  # type: ignore[arg-type]
                with pytest.raises(ConflictError):
                    again.resolve(list(error.conflict.choices))
                results.append(None)
                continue
            assert possible
            values = results[-1].values  # type: ignore[union-attr]
            assert _holds(tree, values), (tree, choices, values)
            assert all(values[c.symbol] == c.value for c in choices)
        assert all(
            r == results[0] or (r and results[0] and r.values == results[0].values) for r in results
        )


def test_large_trees_resolve_quickly() -> None:
    size = 2000
    chain = "".join(
        f'config C{i}\n\tbool "c"\n\tdefault y\n\tdepends on C{i + 1}\n' for i in range(size)
    )
    flat = "".join(f'config F{i}\n\ttristate "f"\n\tdefault m\n' for i in range(size))
    tree = _tree(chain + f'config C{size}\n\tbool "c"\n\tdefault y\n' + flat)
    start = time.perf_counter()
    configuration = Resolver(tree, "builtin").resolve([Assignment(symbol="C7", value="n")])
    assert time.perf_counter() - start < 20
    assert [configuration.values[f"C{i}"] for i in (0, 7, 8, size)] == ["n", "n", "y", "y"]
    assert configuration.values["F0"] == "y"


def test_user_value_does_not_pull_on_dependencies() -> None:
    text = 'config A\n\tbool "a"\n\tdefault y\n\tdepends on B\nconfig B\n\tbool "b"\n\tdefault y\n'
    assert _resolve("A=n", text=text) == {"A": "n", "B": "y"}
