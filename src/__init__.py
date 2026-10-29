# SPDX-License-Identifier: GPL-2.0
"""Trellis configures a build from a tree of options.

The options are written in a subset of the Linux Kconfig language. The
user's choices are kept in a defconfig, and Trellis resolves them with
a SAT solver into one value for every option. Choices that cannot all
hold are reported as a smallest set of choices and rules in conflict,
with the ways to remove it.

Trellis is a library first:

    from trellis import Configurator

    configurator = Configurator.load("Kconfig", "defconfig")
    configurator.set("NET", "y")
    print(configurator.resolve().values)
    configurator.save("defconfig", "config.pyb")

language/ reads Kconfig files and checks the tree, logic/ turns the
tree into clauses, solver/ holds the built-in solver and the PicoSAT
binding, resolve.py and explain.py answer questions with them, files.py
reads and writes defconfig and config.pyb, api.py is the Configurator,
ui/ holds the line and menu consoles and cli.py is the ``trellis``
command.

Names in ``__all__`` follow semantic versioning from release 1.0. A name
is removed only after one minor release in which using it raises a
DeprecationWarning. Modules and names not listed here are internal.
"""

from __future__ import annotations

from .api import Configurator
from .errors import (
    ConfigError,
    ConflictError,
    InvalidValue,
    ParseError,
    RejectedConstruct,
    SolverUnavailable,
    TreeError,
)
from .files import read_config, read_defconfig, write_config, write_defconfig
from .language.checker import check
from .language.parser import parse_file, parse_text
from .models import (
    Assignment,
    Change,
    Choice,
    Configuration,
    Conflict,
    Explanation,
    Fix,
    OptionTree,
    Rule,
    Symbol,
)
from .solver import BACKENDS, Backend

__version__ = "1.0.0"

__all__ = [
    "BACKENDS",
    "Assignment",
    "Backend",
    "Change",
    "Choice",
    "ConfigError",
    "Configuration",
    "Configurator",
    "Conflict",
    "ConflictError",
    "Explanation",
    "Fix",
    "InvalidValue",
    "OptionTree",
    "ParseError",
    "RejectedConstruct",
    "Rule",
    "SolverUnavailable",
    "Symbol",
    "TreeError",
    "__version__",
    "check",
    "parse_file",
    "parse_text",
    "read_config",
    "read_defconfig",
    "write_config",
    "write_defconfig",
]
