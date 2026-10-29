# SPDX-License-Identifier: GPL-2.0
"""Split a Kconfig file into logical lines of tokens.

Kconfig is line based. A backslash at the end of a line joins it to the
next, ``#`` starts a comment outside quotes, and the text after ``help``
is every following line indented more than the first of them. A ``$``
outside a string, and ``$(`` inside one, start a macro, which Trellis
refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..errors import ParseError, RejectedConstruct
from ..models import Location

TokenKind = Literal["word", "string", "op"]

_OPERATORS = ("&&", "||", "!=", "<=", ">=", "=", "!", "<", ">", "(", ")")
_WORD = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.")
_HELP = frozenset({"help", "---help---"})


@dataclass(frozen=True)
class Token:
    """A word, a quoted string without its quotes, or an operator."""

    kind: TokenKind
    text: str


@dataclass
class Line:
    """The tokens of one logical line, or the text of a help block."""

    tokens: list[Token]
    location: Location
    help: str | None = None

    @property
    def keyword(self) -> str:
        """The first token if it is a word, else an empty string."""
        first = self.tokens[0] if self.tokens else None
        return first.text if first is not None and first.kind == "word" else ""


def _indent(text: str) -> int:
    width = 0
    for char in text:
        if char == " ":
            width += 1
        elif char == "\t":
            width = (width // 8 + 1) * 8
        else:
            break
    return width


def tokenize(text: str, location: Location) -> list[Token]:
    """Return the tokens of one logical line, without its comment."""
    tokens: list[Token] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char in " \t\r":
            i += 1
        elif char == "#":
            break
        elif char == "$":
            raise RejectedConstruct("macros ($) are not supported", location)
        elif char in "\"'":
            value, i = _string(text, i, location)
            tokens.append(Token("string", value))
        elif char in _WORD:
            start = i
            while i < len(text) and text[i] in _WORD:
                i += 1
            tokens.append(Token("word", text[start:i]))
        else:
            op = next((o for o in _OPERATORS if text.startswith(o, i)), None)
            if op is None:
                raise ParseError(f"unexpected character {char!r}", location)
            tokens.append(Token("op", op))
            i += len(op)
    return tokens


def _string(text: str, start: int, location: Location) -> tuple[str, int]:
    quote = text[start]
    out: list[str] = []
    i = start + 1
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if char == quote:
            value = "".join(out)
            if "$(" in value:
                raise RejectedConstruct("macros ($) are not supported", location)
            return value, i + 1
        out.append(char)
        i += 1
    raise ParseError("unterminated string", location)


def lex(source: str, file: str) -> list[Line]:
    """Return the logical lines of a Kconfig file, help text included as ``Line.help``."""
    raw = source.splitlines()
    lines: list[Line] = []
    i = 0
    while i < len(raw):
        number = i + 1
        location = Location(file=file, line=number)
        text = raw[i]
        i += 1
        while text.endswith("\\") and i < len(raw):
            text = text[:-1] + raw[i]
            i += 1
        tokens = tokenize(text, location)
        if not tokens:
            continue
        line = Line(tokens, location)
        lines.append(line)
        if line.keyword in _HELP and len(tokens) == 1:
            line.help, i = _help(raw, i)
    return lines


def _help(raw: list[str], i: int) -> tuple[str, int]:
    while i < len(raw) and not raw[i].strip():
        i += 1
    if i == len(raw):
        return "", i
    first = _indent(raw[i])
    body: list[str] = []
    while i < len(raw) and (not raw[i].strip() or _indent(raw[i]) >= first > 0):
        body.append(raw[i].expandtabs(8)[first:].rstrip() if raw[i].strip() else "")
        i += 1
    while body and not body[-1]:
        body.pop()
    return "\n".join(body), i


__all__ = ["Line", "Token", "lex", "tokenize"]
