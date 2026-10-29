# SPDX-License-Identifier: GPL-2.0
"""Line prompts for the options a configuration does not cover yet.

``trellis old`` asks, one line at a time, about every option the user
can set that is not in the previous ``config.pyb``, the way Linux
``make oldconfig`` does. An empty answer keeps the value shown in
capitals or brackets, ``?`` shows the help and why the option has its
value, and a choice is answered with the number of a member.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TextIO

from ..api import Configurator
from ..errors import ConflictError, InvalidValue
from .text import conflict_lines, fix_lines, values_of


class LinePrompter:
    """Asks about new options on ``stdin`` and ``stdout``."""

    def __init__(
        self,
        configurator: Configurator,
        known: Collection[str],
        stdin: TextIO,
        stdout: TextIO,
    ) -> None:
        """Ask about options the user can set that are not in ``known``."""
        self.configurator = configurator
        self.known = set(known)
        self.stdin = stdin
        self.stdout = stdout
        self.asked: set[str] = set()

    def _say(self, text: str = "") -> None:
        self.stdout.write(text + "\n")

    def _read(self, prompt: str) -> str | None:
        self.stdout.write(prompt)
        self.stdout.flush()
        line = self.stdin.readline()
        if not line:
            self._say()
            return None
        return line.strip()

    def run(self) -> int:
        """Ask until no new option is left; the number of options asked about.

        Answers can reveal more options, so the list is worked out again
        after each one. Input ending early keeps the remaining values.
        """
        while True:
            pending = self.next_pending()
            if pending is None:
                return len(self.asked)
            if not self.ask(pending):
                return len(self.asked)

    def next_pending(self) -> str | None:
        """Return the first option in tree order still to ask about."""
        configuration = self.configurator.resolve()
        chosen = {c.symbol for c in self.configurator.choices}
        for name in configuration.prompts:
            symbol = self.configurator.tree.symbols[name]
            key = symbol.choice or name
            if key in self.asked or name in self.known or name in chosen:
                continue
            return key
        return None

    def ask(self, key: str) -> bool:
        """Ask about one option or choice; False if input ended."""
        self.asked.add(key)
        if key in self.configurator.tree.choices:
            return self._ask_choice(key)
        return self._ask_symbol(key)

    def _ask_symbol(self, name: str) -> bool:
        configurator = self.configurator
        symbol = configurator.symbol(name)
        while True:
            value = configurator.resolve().values.get(name, "")
            options = values_of(configurator, name)
            if options:
                shown = "/".join(v.upper() if v == value else v for v in options)
            else:
                shown = value
            answer = self._read(f"{symbol.prompt} ({name}) [{shown}/?] (NEW) ")
            if answer is None:
                return False
            if answer == "":
                return True
            if answer == "?":
                self._help(name)
                continue
            if self.try_set(name, answer.lower() if options else answer):
                return True

    def _ask_choice(self, key: str) -> bool:
        configurator = self.configurator
        choice = configurator.tree.choices[key]
        while True:
            configuration = configurator.resolve()
            members = [m for m in choice.members if m in configuration.prompts]
            self._say(choice.prompt or key)
            current = 0
            for number, member in enumerate(members, 1):
                mark = ">" if configuration.values.get(member) == "y" else " "
                if mark == ">":
                    current = number
                prompt = configurator.tree.symbols[member].prompt
                self._say(f" {mark} {number}. {prompt} ({member}) (NEW)")
            answer = self._read(f"choice[1-{len(members)}?] ({current or 'none'}) ")
            if answer is None:
                return False
            if answer == "":
                return True
            if answer == "?":
                self._say(choice.help or "There is no help for this choice.")
                continue
            if answer.isdigit() and 1 <= int(answer) <= len(members):
                if self.try_set(members[int(answer) - 1], "y"):
                    return True
                continue
            self._say(f"Answer with a number from 1 to {len(members)}.")

    def try_set(self, name: str, value: str) -> bool:
        """Set ``name``; on a conflict print it and the fixes, and undo."""
        configurator = self.configurator
        previous = configurator.choices
        try:
            configurator.set(name, value)
            configurator.resolve()
        except InvalidValue as error:
            self._say(str(error))
            return False
        except ConflictError as error:
            self._say(f"{name}={value} conflicts with:")
            for line in conflict_lines(error.conflict):
                self._say(f"  {line}")
            configurator.restore(previous)
            if values_of(configurator, name):
                for number, fix in enumerate(configurator.fixes(name, value), 1):
                    for line in fix_lines(number, fix):
                        self._say(f"  {line}")
            return False
        return True

    def _help(self, name: str) -> None:
        symbol = self.configurator.symbol(name)
        self._say(symbol.help or "There is no help for this option.")
        for reason in self.configurator.explain(name).reasons:
            self._say(f"  {reason}")


__all__ = ["LinePrompter"]
