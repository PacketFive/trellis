# SPDX-License-Identifier: GPL-2.0
"""The full-screen menu of ``trellis menu``.

MenuState holds everything the screen shows and every action a key
can take, with no curses in it, so it can be tested on its own. run()
is the curses loop that draws a MenuState and passes keys to it.

Keys: arrows or j and k move, Enter or the right arrow opens a menu
or a choice, space cycles a value, y, m and n set one, Enter on a
number or a string asks for a new value, the left arrow or Escape go
back, ? explains the option, a shows the options hidden by their
dependencies, 1 to 3 apply a fix after a conflict, s saves and q quits.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..api import Configurator
from ..errors import ConflictError, InvalidValue
from ..logic.expr import conj, evaluate
from ..models import ChoiceEntry, CommentEntry, Entry, Fix, MenuEntry, SymbolEntry
from .text import conflict_lines, fix_lines, values_of


@dataclass(frozen=True)
class Row:
    """One line of the menu."""

    text: str
    entry: Entry
    shown: bool = True
    opens: bool = False


@dataclass
class Level:
    """A menu open on the screen, with its cursor."""

    title: str
    entries: tuple[Entry, ...]
    choice: str | None = None
    cursor: int = 0


@dataclass
class MenuState:
    """What the menu shows and what each key does to it."""

    configurator: Configurator
    show_all: bool = False
    changed: bool = False
    message: list[str] = field(default_factory=list)
    fixes: list[Fix] = field(default_factory=list)
    pending: tuple[str, str] | None = None
    levels: list[Level] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Open the top menu and check that the choices resolve."""
        tree = self.configurator.tree
        self.levels = [Level(title=tree.title, entries=tree.entries)]
        self.configurator.resolve()

    @property
    def level(self) -> Level:
        """The menu on top."""
        return self.levels[-1]

    @property
    def title(self) -> str:
        """The path of menus open, for the top line."""
        return " > ".join(level.title for level in self.levels)

    def rows(self) -> list[Row]:
        """Return the lines of the menu on top."""
        level = self.level
        if level.choice is not None:
            members = self.configurator.tree.choices[level.choice].members
            entries: tuple[Entry, ...] = tuple(SymbolEntry(name=m) for m in members)
        else:
            entries = level.entries
        rows = [self._row(entry) for entry in entries]
        return [row for row in rows if row is not None and (row.shown or self.show_all)]

    def _row(self, entry: Entry) -> Row | None:
        configuration = self.configurator.resolve()
        values = configuration.values
        tree = self.configurator.tree
        if isinstance(entry, MenuEntry):
            shown = evaluate(conj(entry.depends, entry.visible), values) > 0
            return Row(f"    {entry.title}  --->", entry, shown, opens=True)
        if isinstance(entry, CommentEntry):
            shown = evaluate(entry.depends, values) > 0
            return Row(f"    *** {entry.text} ***", entry, shown)
        if isinstance(entry, ChoiceEntry):
            choice = tree.choices[entry.name]
            members = [m for m in choice.members if m in configuration.prompts]
            chosen = next((m for m in choice.members if values.get(m) == "y"), None)
            label = tree.symbols[chosen].prompt if chosen else "none"
            shown = bool(members)
            return Row(f"    {choice.prompt} ({label})  --->", entry, shown, opens=True)
        symbol = tree.symbols[entry.name]
        if symbol.prompt is None:
            return None
        shown = entry.name in configuration.prompts
        value = values.get(entry.name)
        if self.level.choice is not None:
            mark = "(X)" if value == "y" else "( )"
        elif symbol.type == "bool":
            mark = "[*]" if value == "y" else "[ ]"
        elif symbol.type == "tristate":
            mark = {"y": "<*>", "m": "<M>"}.get(value or "n", "< >")
        else:
            mark = f"({value if value is not None else ''})"
        text = f"{mark} {symbol.prompt}"
        if not shown:
            text += "  (hidden)"
        opens = bool(entry.children)
        return Row(text + ("  --->" if opens else ""), entry, shown, opens)

    def move(self, step: int) -> None:
        """Move the cursor by ``step`` rows, within the menu."""
        count = len(self.rows())
        self.level.cursor = max(0, min(count - 1, self.level.cursor + step))

    def current(self) -> Row | None:
        """Return the row under the cursor, if the menu has any."""
        rows = self.rows()
        if not rows:
            return None
        self.level.cursor = min(self.level.cursor, len(rows) - 1)
        return rows[self.level.cursor]

    def enter(self) -> bool:
        """Open the menu or choice under the cursor; False if it is not one."""
        row = self.current()
        if row is None or not row.opens:
            return False
        entry = row.entry
        tree = self.configurator.tree
        if isinstance(entry, MenuEntry):
            self.levels.append(Level(title=entry.title, entries=entry.children))
        elif isinstance(entry, ChoiceEntry):
            title = tree.choices[entry.name].prompt or entry.name
            self.levels.append(Level(title=title, entries=(), choice=entry.name))
        elif isinstance(entry, SymbolEntry):
            title = tree.symbols[entry.name].prompt or entry.name
            self.levels.append(Level(title=title, entries=entry.children))
        return True

    def back(self) -> bool:
        """Close the menu on top; False at the top menu."""
        if len(self.levels) == 1:
            return False
        self.levels.pop()
        return True

    def name(self) -> str | None:
        """Return the option under the cursor, if it is one."""
        row = self.current()
        if row is None or not isinstance(row.entry, SymbolEntry):
            return None
        return row.entry.name

    def needs_text(self) -> bool:
        """Whether the option under the cursor takes a typed value."""
        name = self.name()
        return name is not None and not values_of(self.configurator, name)

    def cycle(self) -> None:
        """Move the option under the cursor to its next value."""
        name = self.name()
        if name is None:
            return
        options = values_of(self.configurator, name)
        if not options:
            return
        if self.level.choice is not None:
            self.set(name, "y")
            return
        value = self.configurator.resolve().values.get(name, "n")
        following = options[(options.index(value) + 1) % len(options)]
        self.set(name, following)

    def set(self, name: str, value: str) -> None:
        """Choose ``value`` for ``name``; on a conflict undo it and list fixes."""
        configurator = self.configurator
        previous = configurator.choices
        self.message, self.fixes, self.pending = [], [], None
        try:
            configurator.set(name, value)
            configurator.resolve()
        except InvalidValue as error:
            configurator.restore(previous)
            self.message = [str(error)]
            return
        except ConflictError as error:
            configurator.restore(previous)
            self.message = [f"{name}={value} conflicts with:"]
            self.message += [f"  {line}" for line in conflict_lines(error.conflict)]
            if values_of(configurator, name):
                self.fixes = configurator.fixes(name, value)
                self.pending = (name, value)
                for number, fix in enumerate(self.fixes, 1):
                    self.message += [f"  {line}" for line in fix_lines(number, fix)]
                if not self.fixes:
                    self.message.append("  no change to other choices allows it")
            return
        self.changed = True

    def apply(self, number: int) -> None:
        """Apply fix ``number``, counted from 1, of the last conflict."""
        if self.pending is None or not 1 <= number <= len(self.fixes):
            return
        configurator = self.configurator
        fix = self.fixes[number - 1]
        for choice in fix.dropped:
            configurator.unset(choice.symbol)
        name, value = self.pending
        self.set(name, value)

    def explain(self) -> None:
        """Show the help and the reasons for the value of the option under the cursor."""
        name = self.name()
        if name is None:
            return
        symbol = self.configurator.symbol(name)
        explanation = self.configurator.explain(name)
        self.fixes, self.pending = [], None
        self.message = [f"{name} = {explanation.value} ({symbol.location})"]
        self.message += [f"  {reason}" for reason in explanation.reasons]
        if symbol.help:
            self.message += ["", *symbol.help.splitlines()]

    def toggle_all(self) -> None:
        """Show or hide the options whose dependencies do not hold."""
        self.show_all = not self.show_all
        self.level.cursor = 0


KEYS_HELP = "Enter open/edit  Space cycle  y/m/n set  ? why  a all  1-3 fix  s save  q quit"


def run(state: MenuState, save: Callable[[], None]) -> bool:
    """Show ``state`` until the user quits; whether it was saved at the end."""
    import curses

    return curses.wrapper(lambda screen: _loop(screen, state, save))


def _loop(screen: object, state: MenuState, save: Callable[[], None]) -> bool:  # pragma: no cover
    import curses

    window: curses.window = screen  # type: ignore[assignment]
    curses.curs_set(0)
    saved = False
    while True:
        _draw(window, state)
        key = window.getch()
        if key in (curses.KEY_UP, ord("k")):
            state.move(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            state.move(1)
        elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
            state.move(-10 if key == curses.KEY_PPAGE else 10)
        elif key in (curses.KEY_LEFT, 27, curses.KEY_BACKSPACE):
            state.back()
        elif key in (curses.KEY_RIGHT, 10, 13, curses.KEY_ENTER):
            if state.enter():
                continue
            if state.needs_text():
                name = state.name()
                text = _ask(window, f"{name} = ")
                if name is not None and text is not None:
                    state.set(name, text)
            else:
                state.cycle()
        elif key == ord(" "):
            state.cycle()
        elif key in (ord("y"), ord("m"), ord("n")):
            name = state.name()
            if name is not None and not state.needs_text():
                state.set(name, chr(key))
        elif key == ord("?"):
            state.explain()
        elif key == ord("a"):
            state.toggle_all()
        elif ord("1") <= key <= ord("9"):
            state.apply(key - ord("0"))
        elif key == ord("s"):
            save()
            state.changed, saved = False, True
            state.message = ["saved"]
        elif key == ord("q"):
            if state.changed:
                answer = _ask(window, "Save changes? [Y/n] ")
                if answer is None:
                    continue
                if answer.lower() in ("", "y", "yes"):
                    save()
                    saved = True
            return saved


def _draw(window: object, state: MenuState) -> None:  # pragma: no cover
    import curses

    screen: curses.window = window  # type: ignore[assignment]
    screen.erase()
    height, width = screen.getmaxyx()
    rows = state.rows()
    room = max(1, height - 3 - min(len(state.message), height // 2))
    top = max(0, state.level.cursor - room + 1)
    screen.addnstr(0, 0, state.title, width - 1, curses.A_BOLD)
    for index, row in enumerate(rows[top : top + room]):
        attribute = curses.A_REVERSE if top + index == state.level.cursor else 0
        if not row.shown:
            attribute |= curses.A_DIM
        screen.addnstr(1 + index, 0, row.text, width - 1, attribute)
    line = 1 + room
    for text in state.message[: height - line - 1]:
        screen.addnstr(line, 0, text, width - 1)
        line += 1
    screen.addnstr(height - 1, 0, KEYS_HELP, width - 1, curses.A_DIM)
    screen.refresh()


def _ask(window: object, prompt: str) -> str | None:  # pragma: no cover
    import curses

    screen: curses.window = window  # type: ignore[assignment]
    height, width = screen.getmaxyx()
    screen.move(height - 1, 0)
    screen.clrtoeol()
    screen.addnstr(height - 1, 0, prompt, width - 1)
    curses.echo()
    curses.curs_set(1)
    try:
        text = screen.getstr(height - 1, min(len(prompt), width - 1)).decode()
    except KeyboardInterrupt:
        return None
    finally:
        curses.noecho()
        curses.curs_set(0)
    return text


__all__ = ["KEYS_HELP", "Level", "MenuState", "Row", "run"]
