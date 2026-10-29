# SPDX-License-Identifier: GPL-2.0
"""The ``trellis`` command.

Usage::

    trellis [FILE OPTIONS] menu
    trellis [FILE OPTIONS] old
    trellis [FILE OPTIONS] olddefconfig
    trellis [FILE OPTIONS] defconfig SOURCE
    trellis [FILE OPTIONS] savedefconfig [--output PATH]
    trellis [FILE OPTIONS] set NAME=VALUE [NAME=VALUE ...]
    trellis [FILE OPTIONS] allyes | allno
    trellis [FILE OPTIONS] check
    trellis [FILE OPTIONS] explain NAME [VALUE]
    trellis [FILE OPTIONS] export-dimacs [--output PATH]

``menu`` opens the full-screen menu and ``old`` asks, one line at a time,
about the options the previous config.pyb does not have. Both save the
defconfig and config.pyb when done. ``olddefconfig`` writes config.pyb
from the defconfig without asking. ``defconfig`` takes the choices of
SOURCE as the defconfig. ``savedefconfig`` writes the choices without
those that do not change the result, to the defconfig or to PATH
(``-`` for standard output).
``set`` adds choices. ``allyes`` and ``allno`` start from no choices and
set every option the rules allow to y or n. ``check`` exits with 1 if config.pyb is missing
or differs from what the defconfig resolves to. ``explain`` prints why
NAME has its value or, with VALUE, why it cannot have VALUE and the
ways to allow it. ``export-dimacs`` prints the clauses of the tree.

FILE OPTIONS:

    --kconfig PATH      top Kconfig file (Kconfig)
    --defconfig PATH    the user's choices (defconfig)
    --config PATH       the resolved configuration (config.pyb)
    --solver NAME       builtin (the default) or picosat

The time the solver took is printed to stderr. Exit status is 0 on
success, 1 if the choices conflict or a check fails and 2 for an error
in a file or an argument.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .api import Configurator
from .errors import ConfigError, ConflictError
from .files import read_config, write_file
from .solver import BACKENDS
from .ui.text import fix_lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trellis", description="Configure a build from a Kconfig option tree."
    )
    parser.add_argument("--kconfig", default="Kconfig", help="top Kconfig file")
    parser.add_argument("--defconfig", default="defconfig", help="the user's choices")
    parser.add_argument("--config", default="config.pyb", help="the resolved configuration")
    parser.add_argument("--solver", choices=BACKENDS, default="builtin", help="SAT solver")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("menu", help="full-screen menu")
    commands.add_parser("old", help="ask about new options")
    commands.add_parser("olddefconfig", help="write config.pyb from the defconfig")
    source = commands.add_parser("defconfig", help="take the choices of another defconfig")
    source.add_argument("source")
    save = commands.add_parser("savedefconfig", help="write the smallest defconfig")
    save.add_argument("--output")
    assign = commands.add_parser("set", help="add choices")
    assign.add_argument("assignments", nargs="+", metavar="NAME=VALUE")
    commands.add_parser("allyes", help="every option the rules allow, set to y")
    commands.add_parser("allno", help="every option the rules allow, set to n")
    commands.add_parser("check", help="check that config.pyb is up to date")
    explain = commands.add_parser("explain", help="why an option has its value")
    explain.add_argument("name")
    explain.add_argument("value", nargs="?")
    dimacs = commands.add_parser("export-dimacs", help="print the clauses of the tree")
    dimacs.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command in ``argv`` and return its exit status."""
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except ConflictError as error:
        print(f"trellis: {error}", file=sys.stderr)
        if args.command == "set":
            print(
                "trellis: run trellis explain NAME VALUE to see the ways to allow a choice",
                file=sys.stderr,
            )
        return 1
    except (ConfigError, OSError) as error:
        print(f"trellis: {error}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    defconfig = None if args.command == "defconfig" else args.defconfig
    configurator = Configurator.load(args.kconfig, defconfig, backend=args.solver)
    command = args.command
    if command == "defconfig":
        configurator.add_defconfig(args.source)
    for choice in configurator.ignored:
        print(
            f"trellis: ignoring {choice.symbol}={choice.value}: no such option to set",
            file=sys.stderr,
        )
    if command == "set":
        for assignment in args.assignments:
            name, found, value = assignment.partition("=")
            if not found or not name:
                print(f"trellis: {assignment!r} must be written NAME=VALUE", file=sys.stderr)
                return 2
            configurator.set(name, value)
    elif command in ("allyes", "allno"):
        configurator.restore(())
        configurator.set_all("y" if command == "allyes" else "n")
    elif command == "explain":
        return _explain(configurator, args.name, args.value)
    elif command == "export-dimacs":
        _output(args.output, configurator.dimacs())
        return 0
    elif command == "menu":
        return _menu(configurator, args)
    configuration = configurator.resolve()
    print(
        f"trellis: resolved {len(configuration.values)} options in "
        f"{configuration.solve_seconds * 1000:.1f} ms",
        file=sys.stderr,
    )
    if command == "check":
        return _check(configurator, args.config)
    if command == "savedefconfig":
        configurator.restore(configurator.minimal())
        _output(args.output or args.defconfig, configurator.defconfig_text())
        return 0
    if command == "old":
        _old(configurator, args.config)
    if command == "olddefconfig":
        write_file(args.config, configurator.config_text())
    else:
        configurator.save(args.defconfig, args.config)
    return 0


def _output(path: str | None, text: str) -> None:
    if path is None or path == "-":
        sys.stdout.write(text)
    else:
        write_file(path, text)


def _check(configurator: Configurator, config: str) -> int:
    path = Path(config)
    if not path.exists():
        print(f"trellis: {config} is missing; run trellis olddefconfig", file=sys.stderr)
        return 1
    if path.read_text(encoding="utf-8") != configurator.config_text():
        print(f"trellis: {config} is out of date; run trellis olddefconfig", file=sys.stderr)
        return 1
    return 0


def _explain(configurator: Configurator, name: str, value: str | None) -> int:
    if value is None:
        explanation = configurator.explain(name)
        print(f"{name} = {explanation.value}")
        for reason in explanation.reasons:
            print(f"  {reason}")
        return 0
    conflict = configurator.conflict(name, value)
    if conflict is None:
        print(f"{name} can be {value} with the current choices")
        return 0
    print(conflict.describe())
    fixes = configurator.fixes(name, value)
    if not fixes:
        print("No change to the choices allows it.")
    for number, fix in enumerate(fixes, 1):
        for line in fix_lines(number, fix):
            print(line)
    return 1


def _old(configurator: Configurator, config: str) -> None:
    from .ui.line import LinePrompter

    path = Path(config)
    known: set[str] = set()
    if path.exists():
        known = set(read_config(path.read_text(encoding="utf-8"), configurator.tree))
    LinePrompter(configurator, known, sys.stdin, sys.stdout).run()


def _menu(configurator: Configurator, args: argparse.Namespace) -> int:  # pragma: no cover
    from .ui.menu import MenuState, run

    state = MenuState(configurator)
    run(state, lambda: configurator.save(args.defconfig, args.config))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
