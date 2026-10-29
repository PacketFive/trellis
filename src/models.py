# SPDX-License-Identifier: GPL-2.0
"""The option tree and the results of resolving it.

Every model is frozen. Values are kept as Kconfig writes them: ``y``,
``m`` and ``n`` for bool and tristate options, decimal text for ``int``,
``0x`` text for ``hex`` and the text itself for ``string``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .logic.expr import YES, Expr

SymbolType = Literal["bool", "tristate", "int", "hex", "string"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Location(_Model):
    """A line of a Kconfig file, the file relative to the root of the tree."""

    file: str
    line: int

    def __str__(self) -> str:
        """``file:line``."""
        return f"{self.file}:{self.line}"


class Default(_Model):
    """``default value if condition``."""

    value: Expr
    condition: Expr = YES


class Reverse(_Model):
    """``select symbol if condition`` or ``imply symbol if condition``."""

    symbol: str
    condition: Expr = YES


class Range(_Model):
    """``range low high if condition``, the bounds as written."""

    low: str
    high: str
    condition: Expr = YES


class Symbol(_Model):
    """An option, from a ``config`` or ``menuconfig`` entry.

    ``depends`` holds every condition the option depends on, its own
    ``depends on`` together with those of the menus, ``if`` blocks and
    choice around it. ``visible`` holds the ``visible if`` conditions of
    the menus around it.
    """

    name: str
    type: SymbolType
    prompt: str | None = None
    prompt_if: Expr = YES
    depends: Expr = YES
    visible: Expr = YES
    defaults: tuple[Default, ...] = ()
    selects: tuple[Reverse, ...] = ()
    implies: tuple[Reverse, ...] = ()
    ranges: tuple[Range, ...] = ()
    help: str = ""
    modules: bool = False
    menu: bool = False
    choice: str | None = None
    location: Location


class Choice(_Model):
    """A ``choice`` block: at most one member is ``y``, exactly one unless optional."""

    name: str
    prompt: str | None = None
    prompt_if: Expr = YES
    depends: Expr = YES
    visible: Expr = YES
    optional: bool = False
    defaults: tuple[Default, ...] = ()
    members: tuple[str, ...] = ()
    help: str = ""
    location: Location


class SymbolEntry(_Model):
    """An option in a menu; a ``menuconfig`` holds the entries that depend on it."""

    kind: Literal["symbol"] = "symbol"
    name: str
    children: tuple[Entry, ...] = ()


class ChoiceEntry(_Model):
    """A choice in a menu."""

    kind: Literal["choice"] = "choice"
    name: str


class MenuEntry(_Model):
    """A ``menu`` block."""

    kind: Literal["menu"] = "menu"
    title: str
    depends: Expr = YES
    visible: Expr = YES
    location: Location
    children: tuple[Entry, ...] = ()


class CommentEntry(_Model):
    """A ``comment`` line shown in a menu."""

    kind: Literal["comment"] = "comment"
    text: str
    depends: Expr = YES
    location: Location


Entry = Annotated[SymbolEntry | ChoiceEntry | MenuEntry | CommentEntry, Field(discriminator="kind")]

SymbolEntry.model_rebuild()
MenuEntry.model_rebuild()


class OptionTree(_Model):
    """Every option, choice and menu of a Kconfig tree, in the order written."""

    title: str = "Configuration"
    symbols: dict[str, Symbol]
    choices: dict[str, Choice] = Field(default_factory=dict)
    entries: tuple[Entry, ...] = ()

    @property
    def modules(self) -> str | None:
        """The option that enables modules, if the tree has one."""
        return next((s.name for s in self.symbols.values() if s.modules), None)


class Assignment(_Model):
    """A value a user chose for an option."""

    symbol: str
    value: str


class Rule(_Model):
    """A rule of the tree, named in conflicts and explanations."""

    kind: Literal["depends", "prompt", "default", "select", "choice", "range"]
    symbol: str
    text: str
    location: Location

    def __str__(self) -> str:
        """Return the rule as a line of text with its location."""
        return f"{self.text} ({self.location})"


class Conflict(_Model):
    """A smallest set of choices and rules that cannot hold together."""

    choices: tuple[Assignment, ...] = ()
    rules: tuple[Rule, ...] = ()

    def describe(self) -> str:
        """Return the conflict as text, one choice or rule per line."""
        lines = ["these choices and rules cannot all hold:"]
        lines += [f"  you set {c.symbol}={c.value}" for c in self.choices]
        lines += [f"  {rule}" for rule in self.rules]
        return "\n".join(lines)


class Configuration(_Model):
    """A resolved configuration.

    ``values`` holds every bool and tristate option, and every other
    option whose dependencies hold. ``prompts`` names the options a user
    can set now. ``user`` holds the choices it was resolved from.
    """

    values: dict[str, str]
    prompts: tuple[str, ...] = ()
    user: tuple[Assignment, ...] = ()
    solve_seconds: float = 0.0


class Change(_Model):
    """An option whose value a fix changes."""

    symbol: str
    before: str | None
    after: str | None


class Fix(_Model):
    """One way to reach a wanted value.

    ``dropped`` lists the user's choices to give up, and ``changes`` every
    option whose resolved value then differs from the current one.
    """

    dropped: tuple[Assignment, ...] = ()
    changes: tuple[Change, ...] = ()


class Explanation(_Model):
    """Why an option has its value."""

    symbol: str
    value: str | None
    reasons: tuple[str, ...] = ()


__all__ = [
    "Assignment",
    "Change",
    "Choice",
    "ChoiceEntry",
    "CommentEntry",
    "Configuration",
    "Conflict",
    "Default",
    "Entry",
    "Explanation",
    "Fix",
    "Location",
    "MenuEntry",
    "OptionTree",
    "Range",
    "Reverse",
    "Rule",
    "Symbol",
    "SymbolEntry",
    "SymbolType",
]
