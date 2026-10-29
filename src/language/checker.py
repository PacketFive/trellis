# SPDX-License-Identifier: GPL-2.0
"""Check the rules of an option tree that the grammar cannot express.

Every name used must be defined, since an undefined name would silently
be ``n``. ``select`` and ``imply`` name bool or tristate options that are
not choice members. Conditions use only bool and tristate options, and
comparisons only bool and tristate values. Defaults, ranges and the
``modules`` option must fit their types. An option may not depend on
itself through ``depends on`` and ``select``.
"""

from __future__ import annotations

from ..errors import TreeError
from ..logic.expr import Compare, Const, Expr, Sym, Text, depth, symbols, walk
from ..models import Location, OptionTree, Symbol

_LOGICAL = frozenset({"bool", "tristate"})


MAX_DEPTH = 200
"""The deepest expression a tree may hold, after joining its conditions."""


def check(tree: OptionTree) -> None:
    """Raise TreeError for the first rule ``tree`` breaks."""
    _check_depth(tree)
    for symbol in tree.symbols.values():
        _check_symbol(tree, symbol)
    for choice in tree.choices.values():
        where = choice.location
        if not choice.members:
            raise TreeError(f"choice {choice.name} has no members", where)
        for name in choice.members:
            if tree.symbols[name].type != "bool":
                raise TreeError(f"choice member {name} must be bool", tree.symbols[name].location)
        for expr in (choice.depends, choice.prompt_if, choice.visible):
            _condition(tree, expr, where)
        for default in choice.defaults:
            _condition(tree, default.condition, where)
            value = default.value
            if not isinstance(value, Sym) or value.name not in choice.members:
                raise TreeError(f"the default of choice {choice.name} must be a member", where)
    modules = [s for s in tree.symbols.values() if s.modules]
    if len(modules) > 1:
        raise TreeError("only one option may enable modules", modules[1].location)
    if modules and modules[0].type != "bool":
        raise TreeError(f"{modules[0].name} enables modules and must be bool", modules[0].location)
    _check_cycles(tree)


def _check_depth(tree: OptionTree) -> None:
    items: list[tuple[str, Location, tuple[Expr, ...]]] = []
    for symbol in tree.symbols.values():
        exprs = [symbol.depends, symbol.prompt_if, symbol.visible]
        exprs += [e for d in symbol.defaults for e in (d.value, d.condition)]
        exprs += [r.condition for r in (*symbol.selects, *symbol.implies)]
        exprs += [r.condition for r in symbol.ranges]
        items.append((symbol.name, symbol.location, tuple(exprs)))
    for choice in tree.choices.values():
        exprs = [choice.depends, choice.prompt_if, choice.visible]
        exprs += [e for d in choice.defaults for e in (d.value, d.condition)]
        items.append((choice.name, choice.location, tuple(exprs)))
    for name, where, found in items:
        if any(depth(expr) > MAX_DEPTH for expr in found):
            raise TreeError(
                f"the conditions of {name} are nested more than {MAX_DEPTH} levels deep", where
            )


def _check_symbol(tree: OptionTree, symbol: Symbol) -> None:
    where = symbol.location
    for expr in (symbol.depends, symbol.prompt_if, symbol.visible):
        _condition(tree, expr, where)
    logical = symbol.type in _LOGICAL
    for default in symbol.defaults:
        _condition(tree, default.condition, where)
        if logical:
            _condition(tree, default.value, where)
        elif not isinstance(default.value, Text):
            raise TreeError(f"the default of {symbol.name} must be a literal value", where)
        else:
            _literal(symbol, default.value.value, where)
    for kind, reverses in (("select", symbol.selects), ("imply", symbol.implies)):
        if reverses and not logical:
            raise TreeError(f"{symbol.name} is {symbol.type} and cannot {kind}", where)
        for reverse in reverses:
            _condition(tree, reverse.condition, where)
            target = _defined(tree, reverse.symbol, where)
            if target.type not in _LOGICAL:
                raise TreeError(f"{kind} {target.name}: it is {target.type}", where)
            if target.choice is not None:
                raise TreeError(f"{kind} {target.name}: it is a choice member", where)
    if symbol.ranges and symbol.type not in ("int", "hex"):
        raise TreeError(f"{symbol.name} is {symbol.type} and cannot have a range", where)
    for item in symbol.ranges:
        _condition(tree, item.condition, where)
        low, high = _literal(symbol, item.low, where), _literal(symbol, item.high, where)
        if low > high:
            raise TreeError(f"the range of {symbol.name} is empty", where)


def _defined(tree: OptionTree, name: str, where: Location) -> Symbol:
    if name not in tree.symbols:
        raise TreeError(f"{name} is not defined", where)
    return tree.symbols[name]


def _condition(tree: OptionTree, expr: Expr, where: Location) -> None:
    """``expr`` must be a condition over bool and tristate options."""
    for name in symbols(expr):
        symbol = _defined(tree, name, where)
        if symbol.type not in _LOGICAL:
            raise TreeError(f"{name} is {symbol.type} and cannot be used in a condition", where)
    for node in walk(expr):
        if isinstance(node, Text):
            raise TreeError(
                f"{node.value!r}: conditions compare only bool and tristate values", where
            )
        if isinstance(node, Compare) and not all(
            isinstance(side, Sym | Const) for side in (node.left, node.right)
        ):
            raise TreeError("comparisons take an option or y, m or n on each side", where)


def parse_number(type_: str, text: str) -> int:
    """Return the value of an ``int`` or ``hex`` literal; raise ValueError if it is not one."""
    if type_ == "hex":
        if not text.lower().startswith("0x"):
            raise ValueError(text)
        return int(text, 16)
    return int(text, 10)


def _literal(symbol: Symbol, text: str, where: Location) -> int:
    if symbol.type == "string":
        return 0
    try:
        return parse_number(symbol.type, text)
    except ValueError:
        raise TreeError(f"{text!r} is not a valid {symbol.type} for {symbol.name}", where) from None


def _check_cycles(tree: OptionTree) -> None:
    """Refuse an option that depends on itself through depends on and select.

    An option depends on the options in its conditions, and on every
    option that selects it.
    """
    edges: dict[str, list[tuple[str, str]]] = {name: [] for name in tree.symbols}
    for symbol in tree.symbols.values():
        for name in symbols(symbol.depends):
            edges[symbol.name].append((name, f"{symbol.name} depends on {name}"))
        for reverse in symbol.selects:
            edges[reverse.symbol].append(
                (symbol.name, f"{reverse.symbol} is selected by {symbol.name}")
            )
    state: dict[str, int] = {}
    for start in tree.symbols:
        if state.get(start, 0) == 0:
            _visit(tree, edges, state, start)


def _visit(
    tree: OptionTree,
    edges: dict[str, list[tuple[str, str]]],
    state: dict[str, int],
    start: str,
) -> None:
    """Search depth first from ``start`` without recursion; raise on a cycle.

    ``path`` holds the edges that led to the option on top of ``stack``.
    """
    state[start] = 1
    path: list[tuple[str, str]] = []
    stack = [(start, iter(edges[start]))]
    while stack:
        name, pending = stack[-1]
        for target, why in pending:
            if state.get(target, 0) == 1:
                names = [n for n, _ in path]
                steps = [w for _, w in path[names.index(target) :]] if target in names else []
                chain = ", ".join([*steps, why])
                raise TreeError(f"dependency cycle: {chain}", tree.symbols[target].location)
            if state.get(target, 0) == 0:
                state[target] = 1
                path.append((name, why))
                stack.append((target, iter(edges[target])))
                break
        else:
            state[name] = 2
            stack.pop()
            if path:
                path.pop()


__all__ = ["MAX_DEPTH", "check", "parse_number"]
