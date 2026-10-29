# SPDX-License-Identifier: GPL-2.0
"""The Configurator: the one interface the command and the console use.

It holds an option tree and the user's choices, and resolves, explains
and saves them. A choice is kept only if its value fits the option's
type and the option has a prompt; whether it fits the rules is decided
when resolving, so a conflict can be explained rather than refused.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .errors import ConflictError, InvalidValue
from .explain import Explainer
from .files import read_defconfig, write_config, write_defconfig, write_file
from .language.checker import check, parse_number
from .language.parser import parse_file
from .logic.expr import Expr, evaluate, render, symbols
from .models import (
    Assignment,
    Change,
    Configuration,
    Conflict,
    Explanation,
    Fix,
    OptionTree,
    Symbol,
)
from .resolve import Resolver
from .solver import Backend

_LOGICAL = frozenset({"bool", "tristate"})


class Configurator:
    """An option tree, the user's choices and what they resolve to."""

    def __init__(
        self,
        tree: OptionTree,
        choices: Iterable[Assignment] = (),
        backend: Backend = "builtin",
    ) -> None:
        """Check ``tree`` and apply ``choices``; TreeError or InvalidValue if one is wrong."""
        check(tree)
        self.tree = tree
        self.backend: Backend = backend
        self.resolver = Resolver(tree, backend)
        self.ignored: list[Assignment] = []
        self._choices: dict[str, Assignment] = {}
        self._explainer: Explainer | None = None
        self._configuration: Configuration | None = None
        for choice in choices:
            self.set(choice.symbol, choice.value)

    @classmethod
    def load(
        cls,
        kconfig: str | Path,
        defconfig: str | Path | None = None,
        backend: Backend = "builtin",
    ) -> Configurator:
        """Read a tree and, if the file exists, a defconfig.

        Choices for options the tree no longer has, or that have no prompt,
        are left out and listed in ``ignored``, as the Linux tools do.
        """
        configurator = cls(parse_file(kconfig), backend=backend)
        if defconfig is not None and Path(defconfig).exists():
            configurator.add_defconfig(defconfig)
        return configurator

    def add_defconfig(self, path: str | Path) -> None:
        """Add the choices of the defconfig at ``path``, a later one replacing an earlier.

        Choices for options the tree does not have, or that have no prompt,
        are appended to ``ignored`` instead.
        """
        text = Path(path).read_text(encoding="utf-8")
        for choice in read_defconfig(text, str(path)):
            symbol = self.tree.symbols.get(choice.symbol)
            if symbol is None or symbol.prompt is None:
                self.ignored.append(choice)
            else:
                self.set(choice.symbol, choice.value)

    @property
    def choices(self) -> tuple[Assignment, ...]:
        """The user's choices, in the order of the tree."""
        return tuple(self._choices[n] for n in self.tree.symbols if n in self._choices)

    def symbol(self, name: str) -> Symbol:
        """Return the option called ``name``; raise InvalidValue if there is none."""
        if name not in self.tree.symbols:
            raise InvalidValue(f"{name} is not an option of this tree")
        return self.tree.symbols[name]

    def normalise(self, name: str, value: str) -> str:
        """``value`` in the form kept for option ``name``; InvalidValue if it does not fit."""
        symbol = self.symbol(name)
        kind = symbol.type
        if kind == "bool" and value not in ("y", "n"):
            raise InvalidValue(f"{name} is bool and takes y or n, not {value!r}")
        if kind == "tristate":
            if value not in ("y", "m", "n"):
                raise InvalidValue(f"{name} is tristate and takes y, m or n, not {value!r}")
            if value == "m" and self.tree.modules is None:
                raise InvalidValue(f"{name} cannot be m: the tree has no modules option")
        if kind in ("int", "hex"):
            try:
                number = parse_number(kind, value)
            except ValueError:
                raise InvalidValue(f"{name} is {kind}, and {value!r} is not") from None
            return f"0x{number:x}" if kind == "hex" else str(number)
        return value

    def set(self, name: str, value: str) -> None:
        """Choose ``value`` for option ``name``."""
        value = self.normalise(name, value)
        symbol = self.symbol(name)
        if symbol.prompt is None:
            raise InvalidValue(f"{name} has no prompt; it is set only by select or default")
        if symbol.choice is not None and value == "y":
            for member in self.tree.choices[symbol.choice].members:
                self._choices.pop(member, None)
        self._choices[name] = Assignment(symbol=name, value=value)
        self._configuration = None

    def unset(self, name: str) -> None:
        """Forget the choice for option ``name``, if there is one."""
        self.symbol(name)
        if self._choices.pop(name, None) is not None:
            self._configuration = None

    def restore(self, choices: Iterable[Assignment]) -> None:
        """Put back choices read earlier from ``choices``, exactly as they were."""
        self._choices = {c.symbol: c for c in choices}
        self._configuration = None

    def resolve(self) -> Configuration:
        """Return the configuration of the current choices; raise ConflictError if they conflict."""
        if self._configuration is None:
            self._configuration = self.resolver.resolve(self.choices)
        return self._configuration

    @property
    def explainer(self) -> Explainer:
        """The solver used for conflicts and fixes, made on first use."""
        if self._explainer is None:
            self._explainer = Explainer(self.resolver.encoding, self.backend)
        return self._explainer

    def _others(self, name: str) -> list[Assignment]:
        return [c for c in self.choices if c.symbol != name]

    def conflict(self, name: str, value: str = "y") -> Conflict | None:
        """Why option ``name`` cannot be ``value`` with the other choices, or None if it can."""
        value = self.normalise(name, value)
        if self.symbol(name).type not in _LOGICAL:
            raise InvalidValue(f"{name} is not bool or tristate")
        wanted = [Assignment(symbol=name, value=value)]
        return self.explainer.conflict(self._others(name), wanted)

    def fixes(self, name: str, value: str = "y", limit: int = 3) -> list[Fix]:
        """Up to ``limit`` ways to make option ``name`` take ``value``.

        Each drops a smallest set of other choices and lists every option
        whose value would then change. An empty list means no choice of
        the user stands in the way and the rules alone forbid it.
        """
        value = self.normalise(name, value)
        if self.symbol(name).type not in _LOGICAL:
            raise InvalidValue(f"{name} is not bool or tristate")
        wanted = Assignment(symbol=name, value=value)
        try:
            before = self.resolve().values
        except ConflictError:
            before = {}
        fixes: list[Fix] = []
        for group in self.explainer.corrections(self._others(name), [wanted], limit):
            kept = [c for c in self._others(name) if c not in group]
            try:
                after = self.resolver.resolve([*kept, wanted]).values
            except ConflictError:
                continue
            changes = tuple(
                Change(symbol=n, before=before.get(n), after=after.get(n))
                for n in self.tree.symbols
                if before.get(n) != after.get(n)
            )
            fixes.append(Fix(dropped=tuple(group), changes=changes))
        return fixes

    def explain(self, name: str) -> Explanation:
        """Why option ``name`` has its value, and why it cannot have the others."""
        symbol = self.symbol(name)
        try:
            values = self.resolve().values
        except ConflictError as error:
            return Explanation(symbol=name, value=None, reasons=(str(error),))
        value = values.get(name)
        reasons: list[str] = []
        user = self._choices.get(name)
        if user is not None:
            reasons.append(
                f"you set {name}={user.value}"
                + ("" if user.value == value else f", but it resolves to {value}")
            )
        if evaluate(symbol.depends, values) == 0:
            reasons.append(
                f"its dependencies do not hold: {render(symbol.depends)}"
                + _current(symbol.depends, values)
            )
        if symbol.type not in _LOGICAL:
            if value is not None and user is None:
                reasons.append("it takes its default")
            return Explanation(symbol=name, value=value, reasons=tuple(reasons))
        for other in self.tree.symbols.values():
            for kind, reverses in (("selects", other.selects), ("implies", other.implies)):
                for reverse in reverses:
                    if (
                        reverse.symbol == name
                        and values.get(other.name, "n") != "n"
                        and evaluate(reverse.condition, values) > 0
                    ):
                        reasons.append(
                            f"{other.name}={values[other.name]} {kind} it ({other.location})"
                        )
        if symbol.choice is not None:
            choice = self.tree.choices[symbol.choice]
            chosen = next((m for m in choice.members if values.get(m) == "y"), None)
            reasons.append(
                f"it is a member of the choice {choice.prompt or choice.name!r}; "
                f"{chosen or 'none'} is chosen"
            )
        if not reasons:
            reasons.append("it takes its default" if value != "n" else "nothing enables it")
        possible = (
            ["n", "y"] if symbol.type == "bool" or self.tree.modules is None else ["n", "m", "y"]
        )
        for other_value in possible:
            if other_value == value:
                continue
            conflict = self.conflict(name, other_value)
            if conflict is not None:
                parts = [f"you set {c.symbol}={c.value}" for c in conflict.choices]
                parts += [rule.text for rule in conflict.rules]
                reasons.append(f"it cannot be {other_value}: " + "; ".join(parts))
        return Explanation(symbol=name, value=value, reasons=tuple(reasons))

    def minimal(self) -> list[Assignment]:
        """Return the choices without those that do not change the result."""
        target = self.resolve().values
        kept = list(self.choices)
        for choice in list(kept):
            trial = [c for c in kept if c != choice]
            try:
                if self.resolver.resolve(trial).values == target:
                    kept = trial
            except ConflictError:
                continue
        return kept

    def set_all(self, value: str) -> None:
        """Set every bool and tristate option the user can set to ``value`` where the rules allow.

        Options are tried in the order of the tree, so enabling an option
        can reveal the ones after it. Choice members are left to the choice.
        """
        for name, symbol in self.tree.symbols.items():
            if symbol.type not in _LOGICAL or symbol.choice is not None:
                continue
            if name not in self.resolve().prompts:
                continue
            previous = self._choices.get(name)
            self.set(name, value)
            try:
                self.resolve()
            except ConflictError:
                if previous is None:
                    self.unset(name)
                else:
                    self.set(name, previous.value)

    def defconfig_text(self) -> str:
        """Return the choices as defconfig text."""
        return write_defconfig(self.choices, self.tree)

    def config_text(self) -> str:
        """Return the resolved configuration as ``config.pyb`` text."""
        return write_config(self.resolve(), self.tree)

    def save(self, defconfig: str | Path, config: str | Path | None = None) -> None:
        """Write the defconfig and, if given a path, ``config.pyb``.

        The configuration is resolved first, so nothing is written for
        choices that conflict.
        """
        config_text = self.config_text()
        write_file(defconfig, self.defconfig_text())
        if config is not None:
            write_file(config, config_text)

    def dimacs(self) -> str:
        """Return the clauses of the tree in DIMACS format."""
        return self.resolver.encoding.cnf.to_dimacs()


def _current(expr: Expr, values: dict[str, str]) -> str:
    names = symbols(expr)
    return " (" + ", ".join(f"{n}={values.get(n, 'n')}" for n in names) + ")" if names else ""


__all__ = ["Configurator"]
