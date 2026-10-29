# SPDX-License-Identifier: GPL-2.0
"""Encode an option tree as clauses.

A bool or tristate value is a pair of literals ``(at least m, y)``: ``n``
is (false, false), ``m`` is (true, false) and ``y`` is (true, true). A
bool option has one variable used for both. ``a <= b`` is then two
implications, one per half of the pair.

Each rule of the tree becomes clauses guarded by a variable of its own,
so a solver can be asked which rules a conflict needs. For an option X
with dependencies D, prompt visibility P, selecting options R, default
and implying options W, and hidden default H = (not P) and W and D:

========  ============================================
depends   X <= D
prompt    X <= P or R or H
default   H <= X
select    S and condition <= X, for each S that selects X
choice    exactly one member is y while the choice's dependencies hold
          (at most one if optional), and the default member while its
          prompt is hidden
========  ============================================

A ``select`` of an option whose dependencies fail is therefore a
conflict, not an override as in the Linux kernel. Defaults while a
prompt is visible, ``imply`` and the default choice member are not
rules but preferences, tried in the order of the tree once the user's
choices hold.

As in Linux, an upper bound of m on an option that cannot be m, a bool
option or a tristate option while modules are off, counts as y. An
optional choice has no default member: it starts with none chosen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..logic.expr import (
    NO,
    YES,
    And,
    Compare,
    Const,
    Expr,
    Not,
    Or,
    Sym,
    conj,
    render,
)
from ..models import Choice, Location, OptionTree, Rule, Symbol
from .cnf import FALSE, TRUE, Builder, Cnf

Pair = tuple[int, int]

_LOGICAL = frozenset({"bool", "tristate"})


@dataclass(frozen=True)
class Preference:
    """A soft rule; ``literal`` switches its clauses on.

    ``option`` names the option whose default it is, or is empty for the
    default member of a choice.
    """

    literal: int
    text: str
    option: str = ""


@dataclass
class Encoding:
    """The clauses of a tree and what their variables mean."""

    cnf: Cnf
    pairs: dict[str, Pair]
    rules: dict[int, Rule]
    preferences: list[Preference]
    weak: dict[str, Pair] = field(default_factory=dict)
    selectors: dict[str, Pair] = field(default_factory=dict)

    def literals(self, symbol: str, value: str) -> list[int]:
        """Return the literals that set a bool or tristate option to ``value``."""
        low, high = self.pairs[symbol]
        if value == "y":
            return [low] if low == high else [low, high]
        if value == "m":
            return [low, -high]
        return [-low]

    def value(self, symbol: str, model: set[int]) -> str:
        """Return the value of an option in a model, as a set of true literals."""
        low, high = self.pairs[symbol]
        return "y" if high in model else "m" if low in model else "n"


def encode(tree: OptionTree) -> Encoding:
    """Return the clauses, rules and preferences of ``tree``."""
    return _Encoder(tree).run()


class _Encoder:
    def __init__(self, tree: OptionTree) -> None:
        self.tree = tree
        self.cnf = Builder()
        self.pairs: dict[str, Pair] = {}
        self.rules: dict[int, Rule] = {}
        self.preferences: list[Preference] = []
        self.cache: dict[Expr, Pair] = {}
        self.weak: dict[str, Pair] = {}
        self.selectors: dict[str, Pair] = {}
        self.modules = FALSE

    def run(self) -> Encoding:
        tree = self.tree
        for symbol in tree.symbols.values():
            if symbol.type == "bool":
                variable = self.cnf.new(symbol.name)
                self.pairs[symbol.name] = (variable, variable)
            elif symbol.type == "tristate":
                low = self.cnf.new(f"{symbol.name}>=m")
                high = self.cnf.new(f"{symbol.name}=y")
                self.cnf.add(-high, low)
                self.pairs[symbol.name] = (low, high)
        if tree.modules is not None:
            self.modules = self.pairs[tree.modules][1]
        selected: dict[str, list[tuple[Symbol, Expr]]] = {}
        implied: dict[str, list[tuple[Symbol, Expr]]] = {}
        for symbol in tree.symbols.values():
            for reverse in symbol.selects:
                selected.setdefault(reverse.symbol, []).append((symbol, reverse.condition))
            for reverse in symbol.implies:
                implied.setdefault(reverse.symbol, []).append((symbol, reverse.condition))
        placed: set[str] = set()
        for symbol in tree.symbols.values():
            if symbol.type not in _LOGICAL:
                continue
            self.symbol_rules(symbol, selected.get(symbol.name, []), implied.get(symbol.name, []))
            if symbol.choice is not None and symbol.choice not in placed:
                placed.add(symbol.choice)
                self.choice_rules(tree.choices[symbol.choice])
        return Encoding(
            cnf=self.cnf.finish(),
            pairs=self.pairs,
            rules=self.rules,
            preferences=self.preferences,
            weak=self.weak,
            selectors=self.selectors,
        )

    def pair(self, expr: Expr) -> Pair:
        """Return the literals ``(expr >= m, expr = y)``."""
        if expr in self.cache:
            return self.cache[expr]
        cnf = self.cnf
        if isinstance(expr, Const):
            result = {"y": (TRUE, TRUE), "m": (TRUE, FALSE), "n": (FALSE, FALSE)}[expr.value]
        elif isinstance(expr, Sym):
            result = self.pairs.get(expr.name, (FALSE, FALSE))
        elif isinstance(expr, Not):
            low, high = self.pair(expr.operand)
            result = (-high, -low)
        elif isinstance(expr, And | Or):
            left, right = self.pair(expr.left), self.pair(expr.right)
            gate = cnf.all_of if isinstance(expr, And) else cnf.any_of
            result = (gate((left[0], right[0])), gate((left[1], right[1])))
        elif isinstance(expr, Compare):
            left, right = self.pair(expr.left), self.pair(expr.right)
            equal = cnf.all_of((cnf.same(left[0], right[0]), cnf.same(left[1], right[1])))
            equal = equal if expr.op == "=" else -equal
            result = (equal, equal)
        else:
            result = (FALSE, FALSE)
        self.cache[expr] = result
        return result

    def join(self, pairs: list[Pair]) -> Pair:
        """Return the largest of several values."""
        return (self.cnf.any_of(p[0] for p in pairs), self.cnf.any_of(p[1] for p in pairs))

    def meet(self, pairs: list[Pair]) -> Pair:
        """Return the smallest of several values."""
        return (self.cnf.all_of(p[0] for p in pairs), self.cnf.all_of(p[1] for p in pairs))

    def rule(self, kind: str, symbol: str, text: str, location: Location) -> int:
        guard = self.cnf.new(f"rule:{kind}:{symbol}")
        self.rules[guard] = Rule(
            kind=kind,  # type: ignore[arg-type]
            symbol=symbol,
            text=text,
            location=location,
        )
        return guard

    def bound(self, symbol: Symbol, limit: Pair) -> Pair:
        """``limit`` as a bound on ``symbol``: m counts as y where the option cannot be m."""
        if symbol.type == "bool":
            return (limit[0], limit[0])
        promoted = self.cnf.all_of((limit[0], -self.modules))
        return (limit[0], self.cnf.any_of((limit[1], promoted)))

    def at_most(self, guard: int, small: Pair, large: Pair) -> None:
        """Under ``guard``, ``small <= large``."""
        self.cnf.add(-guard, -small[0], large[0])
        self.cnf.add(-guard, -small[1], large[1])

    def first_default(self, defaults: list[tuple[Expr, Expr]]) -> Pair:
        """Return the value of the first default whose condition is not ``n``."""
        chosen: list[Pair] = []
        earlier: list[int] = []
        for value, condition in defaults:
            active = self.pair(condition)[0]
            take = self.cnf.all_of([active, *(-e for e in earlier)])
            chosen.append(self.meet([(take, take), self.pair(conj(value, condition))]))
            earlier.append(active)
        return self.join(chosen) if chosen else (FALSE, FALSE)

    def symbol_rules(
        self,
        symbol: Symbol,
        selected: list[tuple[Symbol, Expr]],
        implied: list[tuple[Symbol, Expr]],
    ) -> None:
        name, where = symbol.name, symbol.location
        value = self.pairs[name]
        depends = self.pair(symbol.depends)
        if symbol.type == "tristate":
            guard = self.rule(
                "depends",
                name,
                f"{name}=m needs {self.tree.modules}"
                if self.tree.modules
                else f"{name} cannot be m: the tree has no modules option",
                where,
            )
            self.cnf.add(-guard, -value[0], value[1], self.modules)
        if symbol.depends != YES:
            guard = self.rule("depends", name, f"{name} depends on {render(symbol.depends)}", where)
            self.at_most(guard, value, self.bound(symbol, depends))
        for source, condition in selected:
            guard = self.rule(
                "select",
                source.name,
                f"{source.name} selects {name}" + _if(condition),
                source.location,
            )
            self.at_most(guard, self.pair(conj(Sym(name=source.name), condition)), value)
        if symbol.choice is not None:
            return
        reverse = self.join([self.pair(conj(Sym(name=s.name), c)) for s, c in selected])
        weak = self.join(
            [
                self.first_default([(d.value, d.condition) for d in symbol.defaults]),
                *(self.pair(conj(Sym(name=s.name), c)) for s, c in implied),
            ]
        )
        self.weak[name] = weak
        self.selectors[name] = reverse
        visible = (
            self.pair(conj(conj(symbol.prompt_if, symbol.visible), symbol.depends))
            if symbol.prompt is not None
            else (FALSE, FALSE)
        )
        hidden = self.meet([(-visible[0], -visible[0]), weak, depends])
        text = (
            f"{name} is set by the user only while its prompt is visible"
            + _if(conj(conj(symbol.prompt_if, symbol.visible), symbol.depends))
            if symbol.prompt is not None
            else f"{name} has no prompt and is set only by select or default"
        )
        guard = self.rule("prompt", name, text, where)
        self.at_most(guard, value, self.bound(symbol, self.join([visible, reverse, hidden])))
        if hidden != (FALSE, FALSE):
            guard = self.rule("default", name, f"{name} takes its default while hidden", where)
            self.at_most(guard, hidden, value)
        if symbol.prompt is not None:
            target = self.join([reverse, self.meet([weak, depends])])
            self.prefer(name, value, self.bound(symbol, target))

    def prefer(self, name: str, value: Pair, target: Pair) -> None:
        text = f"{name} takes its default"
        literal = self.cnf.new(f"prefer:{text}")
        self.cnf.add(-literal, self.cnf.same(value[0], target[0]))
        self.cnf.add(-literal, self.cnf.same(value[1], target[1]))
        self.preferences.append(Preference(literal, text, name))

    def choice_rules(self, choice: Choice) -> None:
        members = [self.pairs[m][0] for m in choice.members]
        active = self.pair(choice.depends)[0]
        if len(members) > 1 or not choice.optional:
            amount = "at most one" if choice.optional else "exactly one"
            text = f"{amount} of {', '.join(choice.members)} is y"
            if choice.depends != YES:
                text += f" while {render(choice.depends)}"
            guard = self.rule("choice", choice.name, text, choice.location)
            if not choice.optional:
                self.cnf.add(-guard, -active, *members)
            for i, first in enumerate(members):
                for second in members[i + 1 :]:
                    self.cnf.add(-guard, -first, -second)
        candidates: list[tuple[int, int]] = []
        for default in () if choice.optional else choice.defaults:
            if not isinstance(default.value, Sym):
                continue
            index = choice.members.index(default.value.name)
            depends = self.pair(self.tree.symbols[default.value.name].depends)[0]
            candidates.append((self.cnf.all_of((self.pair(default.condition)[0], depends)), index))
        for index, name in enumerate(() if choice.optional else choice.members):
            candidates.append((self.pair(self.tree.symbols[name].depends)[0], index))
        chosen: list[list[int]] = [[] for _ in members]
        earlier: list[int] = []
        for literal, index in candidates:
            chosen[index].append(self.cnf.all_of([literal, *(-e for e in earlier)]))
            earlier.append(literal)
        defaults = [self.cnf.any_of(c) for c in chosen]
        visible = (
            self.pair(conj(conj(choice.prompt_if, choice.visible), choice.depends))[0]
            if choice.prompt is not None
            else FALSE
        )
        guard = self.rule(
            "default",
            choice.name,
            f"choice {choice.name} takes its default while hidden",
            choice.location,
        )
        literal = self.cnf.new(f"prefer:choice {choice.name}")
        for member, default in zip(members, defaults, strict=True):
            hold = self.cnf.same(member, default)
            self.cnf.add(-guard, visible, -active, hold)
            self.cnf.add(-literal, -active, hold)
        self.preferences.append(Preference(literal, f"choice {choice.name} takes its default"))
        for name in choice.members:
            symbol = self.tree.symbols[name]
            if symbol.prompt is not None:
                shown = self.pair(conj(conj(symbol.prompt_if, symbol.visible), symbol.depends))
                guard = self.rule(
                    "prompt",
                    name,
                    f"{name} is set only while its prompt is visible",
                    symbol.location,
                )
                self.cnf.add(-guard, -self.pairs[name][0], shown[0], -visible)


def _if(condition: Expr) -> str:
    return "" if condition in (YES, NO) else f" if {render(condition)}"


__all__ = ["Encoding", "Pair", "Preference", "encode"]
