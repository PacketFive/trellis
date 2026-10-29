# SPDX-License-Identifier: GPL-2.0
"""The errors Trellis raises on purpose.

Every one is a ConfigError, so a caller can catch them all at once and
let a bug in Trellis or in the caller surface as itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Conflict, Location


class ConfigError(Exception):
    """Base class for every error Trellis raises on purpose."""


class ParseError(ConfigError):
    """A Kconfig file cannot be read or does not follow the grammar."""

    def __init__(self, message: str, location: Location | None = None) -> None:
        """Keep the location, if known, and put it in front of the message."""
        self.location = location
        super().__init__(f"{location}: {message}" if location is not None else message)


class RejectedConstruct(ParseError):
    """A Kconfig construct outside the subset Trellis accepts.

    Macros, ``$(shell,...)``, ``option env`` and paths outside the option
    tree are refused, so loading a tree never reads the environment or runs
    a program.
    """


class TreeError(ParseError):
    """The option tree parses but breaks a rule, such as an undefined symbol."""


class InvalidValue(ConfigError):
    """A value given for an option has the wrong type, range or symbol."""


class ConflictError(ConfigError):
    """The choices cannot all hold at once under the rules of the tree."""

    def __init__(self, conflict: Conflict) -> None:
        """Keep the conflict and describe it in the message."""
        self.conflict = conflict
        super().__init__(conflict.describe())


class SolverUnavailable(ConfigError):
    """An optional solver backend was asked for but cannot be loaded."""


__all__ = [
    "ConfigError",
    "ConflictError",
    "InvalidValue",
    "ParseError",
    "RejectedConstruct",
    "SolverUnavailable",
    "TreeError",
]
