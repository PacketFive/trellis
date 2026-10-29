# SPDX-License-Identifier: GPL-2.0
"""Text both consoles show: values an option takes, conflicts and fixes."""

from __future__ import annotations

from ..api import Configurator
from ..models import Conflict, Fix


def values_of(configurator: Configurator, name: str) -> list[str]:
    """Return the values option ``name`` can be set to, for bool and tristate options."""
    kind = configurator.symbol(name).type
    if kind == "tristate" and configurator.tree.modules is not None:
        return ["n", "m", "y"]
    if kind in ("bool", "tristate"):
        return ["n", "y"]
    return []


def conflict_lines(conflict: Conflict) -> list[str]:
    """One line per choice and rule in ``conflict``."""
    lines = [f"you set {c.symbol}={c.value}" for c in conflict.choices]
    return lines + [f"{rule.text} ({rule.location})" for rule in conflict.rules]


def fix_lines(number: int, fix: Fix) -> list[str]:
    """Return a numbered fix: the choices it drops, then the values it changes."""
    dropped = ", ".join(f"{c.symbol}={c.value}" for c in fix.dropped) or "nothing"
    lines = [f"{number}. drop {dropped}"]
    for change in fix.changes:
        lines.append(f"     {change.symbol}: {change.before or '-'} -> {change.after or '-'}")
    return lines


__all__ = ["conflict_lines", "fix_lines", "values_of"]
