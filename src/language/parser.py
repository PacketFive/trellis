# SPDX-License-Identifier: GPL-2.0
"""Parse the Kconfig subset Trellis accepts into an OptionTree.

The subset keeps ``config``, ``menuconfig``, ``choice``, ``menu``,
``if``, ``comment``, ``mainmenu`` and ``source`` with the attributes
``bool``, ``tristate``, ``int``, ``hex``, ``string``, ``def_bool``,
``def_tristate``, ``prompt``, ``default``, ``depends on``, ``select``,
``imply``, ``range``, ``visible if``, ``optional``, ``modules`` and
``help``. ``source`` takes a path relative to the directory of the top
file, which must stay inside that directory. Anything else is refused.

The tree is not checked here; checker.check does that.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..errors import ParseError, RejectedConstruct, TreeError
from ..logic.expr import (
    YES,
    And,
    Compare,
    Const,
    Expr,
    Not,
    Or,
    Sym,
    Text,
    conj,
    conjuncts,
)
from ..models import (
    Choice,
    ChoiceEntry,
    CommentEntry,
    Default,
    Entry,
    Location,
    MenuEntry,
    OptionTree,
    Range,
    Reverse,
    Symbol,
    SymbolEntry,
    SymbolType,
)
from .lexer import Line, Token, lex

Loader = Callable[[str], str]

_ENTRY = frozenset(
    {
        "config",
        "menuconfig",
        "choice",
        "endchoice",
        "menu",
        "endmenu",
        "if",
        "endif",
        "comment",
        "mainmenu",
        "source",
    }
)
_TYPES = frozenset({"bool", "tristate", "int", "hex", "string"})
_REJECTED_SOURCE = frozenset({"rsource", "osource", "orsource", "gsource", "grsource"})
_NAME = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")
MAX_NESTING = 100
"""The deepest nesting of sourced files, blocks, menuconfigs or parentheses and ``!``."""


def parse_file(path: str | Path) -> OptionTree:
    """Parse the tree whose top file is ``path``."""
    top = Path(path).resolve()
    root = top.parent

    def load(name: str) -> str:
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise RejectedConstruct(f"{name} is outside the option tree")
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ParseError(f"cannot read {name}: {error}") from None

    return parse_text(load(top.name), top.name, load)


def parse_text(source: str, file: str = "Kconfig", loader: Loader | None = None) -> OptionTree:
    """Parse Kconfig text; ``loader`` reads the files it sources, if any."""
    lines = _expand(lex(source, file), loader, [file])
    return _Builder(lines).build()


def _expand(lines: list[Line], loader: Loader | None, stack: list[str]) -> list[Line]:
    out: list[Line] = []
    for line in lines:
        if line.keyword in _REJECTED_SOURCE:
            raise RejectedConstruct(f"{line.keyword} is not supported", line.location)
        if line.keyword != "source":
            out.append(line)
            continue
        name = _source_name(line)
        if loader is None:
            raise RejectedConstruct("source needs a tree read from a file", line.location)
        if name in stack:
            raise ParseError(f"{name} sources itself", line.location)
        if len(stack) > MAX_NESTING:
            raise ParseError(f"source is nested more than {MAX_NESTING} files deep", line.location)
        try:
            text = loader(name)
        except ParseError as error:
            raise type(error)(str(error), line.location) from None
        out += _expand(lex(text, name), loader, [*stack, name])
    return out


def _source_name(line: Line) -> str:
    tokens = line.tokens[1:]
    if len(tokens) != 1 or tokens[0].kind != "string":
        raise ParseError("source takes one quoted path", line.location)
    path = PurePosixPath(tokens[0].text)
    if path.is_absolute() or any(c in tokens[0].text for c in "*?[\\\0"):
        raise RejectedConstruct("source takes a relative path without wildcards", line.location)
    if ".." in path.parts:
        raise RejectedConstruct("source paths may not contain ..", line.location)
    return path.as_posix()


@dataclass(frozen=True)
class _Context:
    depends: Expr = YES
    visible: Expr = YES
    choice: str | None = None


class _Builder:
    def __init__(self, lines: list[Line]) -> None:
        self.lines = lines
        self.position = 0
        self.title: str | None = None
        self.symbols: dict[str, Symbol] = {}
        self.choices: dict[str, Choice] = {}
        self.depth = 0

    def build(self) -> OptionTree:
        entries = self.block(None, _Context())
        return OptionTree(
            title=self.title or "Configuration",
            symbols=self.symbols,
            choices=self.choices,
            entries=tuple(entries),
        )

    def peek(self) -> Line | None:
        return self.lines[self.position] if self.position < len(self.lines) else None

    def take(self) -> Line:
        line = self.lines[self.position]
        self.position += 1
        return line

    def attributes(self) -> list[Line]:
        found: list[Line] = []
        while (line := self.peek()) is not None and line.keyword not in _ENTRY:
            found.append(self.take())
        return found

    def block(self, end: str | None, context: _Context) -> list[Entry]:
        """Return the entries up to ``end``; those of an ``if`` are left for the caller to nest."""
        self.depth += 1
        if self.depth > MAX_NESTING:
            where = self.lines[self.position - 1].location
            raise ParseError(f"blocks are nested more than {MAX_NESTING} deep", where)
        entries = self._entries(end, context)
        self.depth -= 1
        return entries if end == "endif" else self.nest(entries)

    def _entries(self, end: str | None, context: _Context) -> list[Entry]:
        entries: list[Entry] = []
        while (line := self.peek()) is not None:
            keyword = line.keyword
            if end is not None and keyword == end:
                self.take()
                return entries
            if keyword in ("config", "menuconfig"):
                entries.append(self.symbol(context, keyword == "menuconfig"))
            elif keyword == "comment":
                comment = self.comment(context)
                if context.choice is None:
                    entries.append(comment)
            elif keyword == "if":
                self.take()
                condition = _parse_expr(line.tokens[1:], line.location)
                inner = _Context(conj(context.depends, condition), context.visible, context.choice)
                entries += self.block("endif", inner)
            elif keyword == "choice" and context.choice is None:
                entries.append(self.choice(context))
            elif keyword == "menu" and context.choice is None:
                entries.append(self.menu(context))
            elif keyword == "mainmenu" and end is None and self.position == 0:
                self.title = _one_string(self.take(), "mainmenu")
                self.no_attributes("mainmenu")
            else:
                where = " here" if keyword in _ENTRY else ""
                raise ParseError(
                    f"unexpected {keyword or line.tokens[0].text!r}{where}", line.location
                )
        if end is not None:
            raise ParseError(f"missing {end} at the end of the file", self.lines[-1].location)
        return entries

    def no_attributes(self, what: str) -> None:
        for line in self.attributes():
            raise ParseError(f"{what} takes no attributes", line.location)

    def symbol(self, context: _Context, menu: bool) -> SymbolEntry:
        head = self.take()
        name = _name(head)
        if name in self.symbols:
            first = self.symbols[name].location
            raise TreeError(f"{name} is already defined at {first}", head.location)
        kind: SymbolType | None = None
        prompt: tuple[str, Expr] | None = None
        depends: Expr = YES
        defaults: list[Default] = []
        selects: list[Reverse] = []
        implies: list[Reverse] = []
        ranges: list[Range] = []
        help_text = ""
        modules = False
        for line in self.attributes():
            keyword, rest, where = line.keyword, line.tokens[1:], line.location
            if keyword in _TYPES or keyword in ("def_bool", "def_tristate"):
                new_kind: SymbolType = "tristate" if keyword == "def_tristate" else "bool"
                if keyword in _TYPES:
                    new_kind = keyword  # type: ignore[assignment]
                if kind is not None and kind != new_kind:
                    raise ParseError(f"{name} already has type {kind}", where)
                kind = new_kind
                if keyword.startswith("def_"):
                    defaults.append(_default(rest, where))
                elif rest:
                    prompt = _set_prompt(prompt, rest, where)
            elif keyword == "prompt":
                prompt = _set_prompt(prompt, rest, where)
            elif keyword == "default":
                defaults.append(_default(rest, where))
            elif keyword == "depends":
                depends = conj(depends, _depends_on(rest, where))
            elif keyword in ("select", "imply"):
                target, condition = _split_if(rest, where)
                if len(target) != 1 or target[0].kind != "word":
                    raise ParseError(f"{keyword} takes one option name", where)
                reverse = Reverse(symbol=_checked_name(target[0].text, where), condition=condition)
                (selects if keyword == "select" else implies).append(reverse)
            elif keyword == "range":
                bounds, condition = _split_if(rest, where)
                if len(bounds) != 2 or any(t.kind != "word" for t in bounds):
                    raise ParseError("range takes two numbers", where)
                ranges.append(Range(low=bounds[0].text, high=bounds[1].text, condition=condition))
            elif keyword in ("help", "---help---"):
                help_text = line.help or ""
            elif keyword == "modules" and not rest:
                modules = True
            elif keyword == "option":
                modules = modules or _option(rest, where)
            else:
                raise ParseError(f"{keyword or rest!r} is not an attribute of config", where)
        if kind is None:
            raise TreeError(f"{name} has no type", head.location)
        self.symbols[name] = Symbol(
            name=name,
            type=kind,
            prompt=prompt[0] if prompt else None,
            prompt_if=prompt[1] if prompt else YES,
            depends=conj(context.depends, depends),
            visible=context.visible,
            defaults=tuple(defaults),
            selects=tuple(selects),
            implies=tuple(implies),
            ranges=tuple(ranges),
            help=help_text,
            modules=modules,
            menu=menu,
            choice=context.choice,
            location=head.location,
        )
        return SymbolEntry(name=name)

    def choice(self, context: _Context) -> ChoiceEntry:
        head = self.take()
        rest = head.tokens[1:]
        if len(rest) > 1 or (rest and rest[0].kind != "word"):
            raise ParseError("choice takes at most one name", head.location)
        name = _checked_name(rest[0].text, head.location) if rest else f"<choice {head.location}>"
        if name in self.choices:
            raise TreeError(f"choice {name} is already defined", head.location)
        prompt: tuple[str, Expr] | None = None
        depends: Expr = YES
        defaults: list[Default] = []
        optional = False
        help_text = ""
        for line in self.attributes():
            keyword, rest, where = line.keyword, line.tokens[1:], line.location
            if keyword == "bool":
                if rest:
                    prompt = _set_prompt(prompt, rest, where)
            elif keyword == "tristate":
                raise RejectedConstruct("tristate choices are not supported", where)
            elif keyword == "prompt":
                prompt = _set_prompt(prompt, rest, where)
            elif keyword == "optional" and not rest:
                optional = True
            elif keyword == "default":
                defaults.append(_default(rest, where))
            elif keyword == "depends":
                depends = conj(depends, _depends_on(rest, where))
            elif keyword in ("help", "---help---"):
                help_text = line.help or ""
            else:
                raise ParseError(f"{keyword or rest!r} is not an attribute of choice", where)
        inner = _Context(conj(context.depends, depends), context.visible, name)
        self.block("endchoice", inner)
        members = tuple(s.name for s in self.symbols.values() if s.choice == name)
        self.choices[name] = Choice(
            name=name,
            prompt=prompt[0] if prompt else None,
            prompt_if=prompt[1] if prompt else YES,
            depends=inner.depends,
            visible=context.visible,
            optional=optional,
            defaults=tuple(defaults),
            members=members,
            help=help_text,
            location=head.location,
        )
        return ChoiceEntry(name=name)

    def menu(self, context: _Context) -> MenuEntry:
        head = self.take()
        title = _one_string(head, "menu")
        depends: Expr = YES
        visible: Expr = YES
        for line in self.attributes():
            keyword, rest, where = line.keyword, line.tokens[1:], line.location
            if keyword == "depends":
                depends = conj(depends, _depends_on(rest, where))
            elif keyword == "visible" and rest and rest[0].text == "if":
                visible = conj(visible, _parse_expr(rest[1:], where))
            else:
                raise ParseError(f"{keyword or rest!r} is not an attribute of menu", where)
        inner = _Context(conj(context.depends, depends), conj(context.visible, visible))
        children = self.block("endmenu", inner)
        return MenuEntry(
            title=title,
            depends=inner.depends,
            visible=inner.visible,
            location=head.location,
            children=tuple(children),
        )

    def comment(self, context: _Context) -> CommentEntry:
        head = self.take()
        text = _one_string(head, "comment")
        depends: Expr = YES
        for line in self.attributes():
            if line.keyword != "depends":
                raise ParseError("comment takes only depends on", line.location)
            depends = conj(depends, _depends_on(line.tokens[1:], line.location))
        return CommentEntry(
            text=text, depends=conj(context.depends, depends), location=head.location
        )

    def depends_of(self, entry: Entry) -> Expr:
        if isinstance(entry, SymbolEntry):
            return self.symbols[entry.name].depends
        if isinstance(entry, ChoiceEntry):
            return self.choices[entry.name].depends
        return entry.depends

    def nest(self, entries: list[Entry], level: int = 0) -> list[Entry]:
        """Put the entries after a menuconfig that depend on it under it."""
        if level > MAX_NESTING:
            raise ParseError(f"menuconfig options are nested more than {MAX_NESTING} deep")
        out: list[Entry] = []
        i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if isinstance(entry, SymbolEntry) and self.symbols[entry.name].menu:
                children: list[Entry] = []
                while i < len(entries) and Sym(name=entry.name) in conjuncts(
                    self.depends_of(entries[i])
                ):
                    children.append(entries[i])
                    i += 1
                entry = entry.model_copy(update={"children": tuple(self.nest(children, level + 1))})
            out.append(entry)
        return out


def _name(line: Line) -> str:
    rest = line.tokens[1:]
    if len(rest) != 1 or rest[0].kind != "word":
        raise ParseError(f"{line.keyword} takes one option name", line.location)
    return _checked_name(rest[0].text, line.location)


def _checked_name(text: str, location: Location) -> str:
    if not text or not set(text) <= _NAME or text in ("y", "m", "n"):
        raise ParseError(f"{text!r} is not a valid option name", location)
    return text


def _one_string(line: Line, what: str) -> str:
    rest = line.tokens[1:]
    if len(rest) != 1 or rest[0].kind != "string":
        raise ParseError(f"{what} takes one quoted string", line.location)
    return rest[0].text


def _option(rest: list[Token], location: Location) -> bool:
    name = rest[0].text if rest else ""
    if name == "modules" and len(rest) == 1:
        return True
    raise RejectedConstruct(f"option {name} is not supported".rstrip(), location)


def _split_if(tokens: list[Token], location: Location) -> tuple[list[Token], Expr]:
    for i, token in enumerate(tokens):
        if token.kind == "word" and token.text == "if":
            return tokens[:i], _parse_expr(tokens[i + 1 :], location)
    return tokens, YES


def _set_prompt(
    current: tuple[str, Expr] | None, tokens: list[Token], location: Location
) -> tuple[str, Expr]:
    if current is not None:
        raise ParseError("only one prompt is allowed", location)
    text, condition = _split_if(tokens, location)
    if len(text) != 1 or text[0].kind != "string":
        raise ParseError("a prompt is one quoted string", location)
    return text[0].text, condition


def _default(tokens: list[Token], location: Location) -> Default:
    value, condition = _split_if(tokens, location)
    return Default(value=_parse_expr(value, location), condition=condition)


def _depends_on(tokens: list[Token], location: Location) -> Expr:
    if not tokens or tokens[0].text != "on":
        raise ParseError("expected depends on", location)
    return _parse_expr(tokens[1:], location)


def _parse_expr(tokens: list[Token], location: Location) -> Expr:
    if not tokens:
        raise ParseError("expected an expression", location)
    parser = _ExprParser(tokens, location)
    expr = parser.disjunction()
    if parser.position != len(tokens):
        raise ParseError(f"unexpected {tokens[parser.position].text!r}", location)
    return expr


class _ExprParser:
    def __init__(self, tokens: list[Token], location: Location) -> None:
        self.tokens = tokens
        self.location = location
        self.position = 0
        self.depth = 0

    def at(self, text: str) -> bool:
        if self.position < len(self.tokens):
            token = self.tokens[self.position]
            return token.kind == "op" and token.text == text
        return False

    def disjunction(self) -> Expr:
        expr = self.conjunction()
        while self.at("||"):
            self.position += 1
            expr = Or(left=expr, right=self.conjunction())
        return expr

    def conjunction(self) -> Expr:
        expr = self.unary()
        while self.at("&&"):
            self.position += 1
            expr = And(left=expr, right=self.unary())
        return expr

    def unary(self) -> Expr:
        if self.at("!") or self.at("("):
            self.depth += 1
            if self.depth > MAX_NESTING:
                raise ParseError(
                    f"expression is nested more than {MAX_NESTING} deep", self.location
                )
            self.position += 1
            if self.tokens[self.position - 1].text == "!":
                expr: Expr = Not(operand=self.unary())
            else:
                expr = self.disjunction()
                if not self.at(")"):
                    raise ParseError("expected )", self.location)
                self.position += 1
            self.depth -= 1
            return expr
        left = self.atom()
        for op in ("<", "<=", ">", ">="):
            if self.at(op):
                raise RejectedConstruct(f"the comparison {op} is not supported", self.location)
        if self.at("=") or self.at("!="):
            op = self.tokens[self.position].text
            self.position += 1
            return Compare(op=op, left=left, right=self.atom())  # type: ignore[arg-type]
        return left

    def atom(self) -> Expr:
        if self.position == len(self.tokens):
            raise ParseError("expression ends too early", self.location)
        token = self.tokens[self.position]
        self.position += 1
        if token.kind == "string":
            return Text(value=token.text)
        if token.kind != "word":
            raise ParseError(f"unexpected {token.text!r}", self.location)
        if token.text in ("y", "m", "n"):
            return Const(value=token.text)  # type: ignore[arg-type]
        if token.text[0].isdigit() or token.text[0] == "-":
            return Text(value=token.text)
        return Sym(name=_checked_name(token.text, self.location))


__all__ = ["Loader", "parse_file", "parse_text"]
