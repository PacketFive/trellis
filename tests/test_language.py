# SPDX-License-Identifier: GPL-2.0
"""Reading Kconfig: the lexer, the parser and the checks on a tree."""

from pathlib import Path

import pytest

from trellis import ParseError, RejectedConstruct, TreeError, check, parse_file, parse_text
from trellis.language.checker import parse_number
from trellis.language.lexer import lex, tokenize
from trellis.logic.expr import render
from trellis.models import ChoiceEntry, CommentEntry, Location, MenuEntry, SymbolEntry

TREE = """\
mainmenu "Demo"

config MODULES
\tbool "Loadable modules"
\tmodules

menu "Network"
\tdepends on MODULES || !MODULES

config NET
\tbool "Networking"
\tdefault y
\thelp
\t  Network support.

\t  Second paragraph.

menuconfig ETH
\ttristate "Ethernet" if NET
\tdepends on NET
\tselect PHY if NET
\timply LED

if ETH
config E1000
\ttristate "E1000"
endif

config PHY
\ttristate

config LED
\tbool "LEDs"
endmenu

choice SCHED
\tprompt "Scheduler"
\tdefault SCHED_B
config SCHED_A
\tbool "A"
comment "inside"
config SCHED_B
\tbool "B"
endchoice

comment "Sizes"
\tdepends on NET

config BUF
\tint "Buffer"
\trange 1 64 if NET
\tdefault 16

config BASE
\thex "Base"
\tdefault 0x1000

config NAME
\tstring "Name" if NET && \\
\t\t(ETH = m || ETH != y)
\tdefault "net\\"0"
"""


def test_tokens_and_lines() -> None:
    where = Location(file="f", line=1)
    assert [t.text for t in tokenize("config X # comment", where)] == ["config", "X"]
    tokens = tokenize('default "a\\"b" if A&&!(B||C)', where)
    assert [t.text for t in tokens] == [
        "default",
        'a"b',
        "if",
        "A",
        "&&",
        "!",
        "(",
        "B",
        "||",
        "C",
        ")",
    ]
    assert tokens[1].kind == "string"
    lines = lex('config X\n\tbool \\\n\t"x"\n\thelp\n\t  text\n\n\t  more\nconfig Y\n', "f")
    assert [line.keyword for line in lines] == ["config", "bool", "help", "config"]
    assert lines[2].help == "text\n\nmore"


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ('config X\n\tbool "$(shell,ls)"\n', RejectedConstruct),
        ("config $X\n", RejectedConstruct),
        ('config X\n\tbool "x\n', ParseError),
        ("config X\n\tbool @\n", ParseError),
    ],
)
def test_lexer_errors(text: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        parse_text(text)


def test_parse_tree() -> None:
    tree = parse_text(TREE)
    check(tree)
    assert tree.title == "Demo"
    assert tree.modules == "MODULES"
    assert list(tree.symbols) == [
        "MODULES", "NET", "ETH", "E1000", "PHY", "LED",
        "SCHED_A", "SCHED_B", "BUF", "BASE", "NAME",
    ]  # fmt: skip
    net = tree.symbols["NET"]
    assert net.help == "Network support.\n\nSecond paragraph."
    assert render(net.depends) == "MODULES || !MODULES"
    eth = tree.symbols["ETH"]
    assert eth.menu and eth.type == "tristate"
    assert render(eth.prompt_if) == "NET"
    assert [(r.symbol, render(r.condition)) for r in eth.selects] == [("PHY", "NET")]
    assert [r.symbol for r in eth.implies] == ["LED"]
    assert render(tree.symbols["E1000"].depends) == "(MODULES || !MODULES) && ETH"
    assert tree.symbols["PHY"].prompt is None
    assert tree.choices["SCHED"].members == ("SCHED_A", "SCHED_B")
    assert tree.symbols["SCHED_A"].choice == "SCHED"
    assert tree.symbols["BUF"].ranges[0].low == "1"
    assert tree.symbols["NAME"].defaults[0].value.value == 'net"0'  # type: ignore[union-attr]
    assert render(tree.symbols["NAME"].prompt_if) == "NET && (ETH = m || ETH != y)"

    top = tree.entries
    assert isinstance(top[0], SymbolEntry) and top[0].name == "MODULES"
    menu = top[1]
    assert isinstance(menu, MenuEntry) and menu.title == "Network"
    eth_entry = menu.children[1]
    assert isinstance(eth_entry, SymbolEntry) and eth_entry.name == "ETH"
    assert [e.name for e in eth_entry.children] == ["E1000"]  # type: ignore[union-attr]
    assert isinstance(top[2], ChoiceEntry)
    assert isinstance(top[3], CommentEntry) and render(top[3].depends) == "NET"


def test_unnamed_choice_and_optional() -> None:
    tree = parse_text('choice\n\tprompt "P"\n\toptional\nconfig A\n\tbool "A"\nendchoice\n')
    (name,) = tree.choices
    assert name == "<choice Kconfig:1>"
    assert tree.choices[name].optional


@pytest.mark.parametrize(
    ("text", "error", "message"),
    [
        ('config X\n\tbool\n\toption env="HOME"\n', RejectedConstruct, "option env"),
        ("config X\n\tbool\n\toption defconfig_list\n", RejectedConstruct, "not supported"),
        ('rsource "x"\n', RejectedConstruct, "rsource"),
        ('osource "x"\n', RejectedConstruct, "osource"),
        ('source "x"\n', RejectedConstruct, "needs a tree read from a file"),
        ("config X\n\tbool\nconfig Y\n\tbool\n\tdepends on X < Y\n", RejectedConstruct, "<"),
        (
            'choice\n\ttristate "t"\nconfig A\n\ttristate "A"\nendchoice\n',
            RejectedConstruct,
            "tristate",
        ),
        ("config X\n\tbool\nconfig X\n\tbool\n", TreeError, "already defined"),
        ("config X\n", TreeError, "no type"),
        ("config X\n\tbool\n\tint\n", ParseError, "already has type"),
        ("config X\n\tbool\n\tfrob\n", ParseError, "not an attribute"),
        ('menu "m"\nconfig X\n\tbool\n', ParseError, "missing endmenu"),
        ("endif\n", ParseError, "unexpected"),
        ("config X\n\tbool\n\tdepends on (X\n", ParseError, "expected \\)"),
        ("config X\n\tbool\n\tdepends on\n", ParseError, "expected an expression"),
        ('config X\n\tbool "a"\n\tprompt "b"\n', ParseError, "only one prompt"),
        ("config 1x-\n\tbool\n", ParseError, "not a valid option name"),
    ],
)
def test_parse_errors(text: str, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        parse_text(text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("config X\n\tbool\n\tdepends on Y\n", "Y is not defined"),
        (
            'config S\n\tstring "s"\nconfig X\n\tbool\n\tdepends on S\n',
            "cannot be used in a condition",
        ),
        ("config X\n\tbool\n\tselect I\nconfig I\n\tint\n", "it is int"),
        ("config X\n\tint\n\tselect X\n", "cannot select"),
        ("config X\n\tbool\n\trange 1 2\n", "cannot have a range"),
        ("config X\n\tint\n\trange 5 1\n", "range of X is empty"),
        ("config X\n\tint\n\tdefault Y\nconfig Y\n\tint\n", "must be a literal"),
        ("config X\n\thex\n\tdefault 12\n", "not a valid hex"),
        (
            "config A\n\tbool\n\tdepends on B\nconfig B\n\tbool\n\tdepends on A\n",
            "dependency cycle",
        ),
        ("config A\n\tbool\n\tdepends on B\n\tselect B\nconfig B\n\tbool\n", "dependency cycle"),
        ("config A\n\tbool\n\tmodules\nconfig B\n\tbool\n\tmodules\n", "only one option"),
        ("config A\n\ttristate\n\tmodules\n", "must be bool"),
        (
            'choice C\n\tprompt "c"\n\tdefault X\nconfig A\n\tbool "a"\nendchoice\n'
            "config X\n\tbool\n",
            "must be a member",
        ),
        (
            'choice C\n\tprompt "c"\nconfig A\n\tbool "a"\nendchoice\n'
            "config X\n\tbool\n\tselect A\n",
            "choice member",
        ),
        ("config A\n\tbool\nconfig B\n\tbool\n\tdepends on A = 1\n", "comparisons take"),
    ],
)
def test_checker(text: str, message: str) -> None:
    with pytest.raises(TreeError, match=message):
        check(parse_text(text))


def test_numbers() -> None:
    assert parse_number("int", "-12") == -12
    assert parse_number("hex", "0x1F") == 31
    for kind, text in (("int", "0x10"), ("hex", "10"), ("int", "x")):
        with pytest.raises(ValueError, match=text):
            parse_number(kind, text)


def test_source_files(tmp_path: Path) -> None:
    (tmp_path / "net").mkdir()
    (tmp_path / "Kconfig").write_text('mainmenu "T"\nsource "net/Kconfig"\n')
    (tmp_path / "net" / "Kconfig").write_text('config NET\n\tbool "Net"\nsource "net/more"\n')
    (tmp_path / "net" / "more").write_text("config MORE\n\tbool\n")
    tree = parse_file(tmp_path / "Kconfig")
    assert list(tree.symbols) == ["NET", "MORE"]
    assert str(tree.symbols["MORE"].location) == "net/more:1"

    for line, message in (
        ('source "../x"', "may not contain"),
        ('source "/etc/x"', "relative path"),
        ('source "net/*"', "relative path"),
        ('source "Kconfig"', "sources itself"),
        ('source "missing"', "cannot read missing"),
        ("source net", "one quoted path"),
    ):
        (tmp_path / "Kconfig").write_text(line + "\n")
        with pytest.raises(ParseError, match=message):
            parse_file(tmp_path / "Kconfig")


def test_source_symlink_out_of_tree(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("config X\n\tbool\n")
    root = tmp_path / "tree"
    root.mkdir()
    (root / "link").symlink_to(outside)
    (root / "Kconfig").write_text('source "link"\n')
    with pytest.raises(RejectedConstruct, match="outside the option tree"):
        parse_file(root / "Kconfig")


def test_nested_if_keeps_menuconfig_children() -> None:
    tree = parse_text(
        'menuconfig NETDEVICES\n\tbool "n"\nif NETDEVICES\n'
        'menuconfig ETHERNET\n\tbool "e"\nif ETHERNET\n'
        'config E1000\n\tbool "x"\nendif\nconfig DUMMY\n\tbool "d"\nendif\n'
    )
    (top,) = tree.entries
    assert isinstance(top, SymbolEntry) and top.name == "NETDEVICES"
    ethernet, dummy = top.children
    assert isinstance(ethernet, SymbolEntry) and isinstance(dummy, SymbolEntry)
    assert [c.name for c in ethernet.children if isinstance(c, SymbolEntry)] == ["E1000"]
    assert dummy.name == "DUMMY"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("config A\n\tbool\n\tdepends on " + "!" * 150 + "y\n", "expression is nested"),
        ("config A\n\tbool\n\tdepends on " + "(" * 150 + "y" + ")" * 150 + "\n", "expression"),
        ("if y\n" * 150 + "config A\n\tbool\n" + "endif\n" * 150, "blocks are nested"),
        (
            'menuconfig M0\n\tbool "m"\n'
            + "".join(
                f'menuconfig M{i}\n\tbool "m"\n\tdepends on '
                + " && ".join(f"M{k}" for k in range(i))
                + "\n"
                for i in range(1, 110)
            ),
            "menuconfig options are nested",
        ),
    ],
)
def test_nesting_limits(text: str, message: str) -> None:
    with pytest.raises((ParseError, TreeError), match=message):
        check(parse_text(text))


def test_deep_conditions_refused_and_long_chains_allowed() -> None:
    names = [f"S{i}" for i in range(250)]
    text = 'config X\n\tbool "x"\n' + "".join(f"\tdepends on {n}\n" for n in names)
    text += "".join(f'config {n}\n\tbool "s"\n' for n in names)
    with pytest.raises(TreeError, match="nested more than 200 levels"):
        check(parse_text(text))
    chain = "".join(f'config C{i}\n\tbool "c"\n\tdepends on C{i + 1}\n' for i in range(3000))
    check(parse_text(chain + 'config C3000\n\tbool "c"\n'))


def test_source_nul_byte_refused(tmp_path: Path) -> None:
    (tmp_path / "Kconfig").write_text('source "a\\\x00b"\n')
    with pytest.raises(RejectedConstruct):
        parse_file(tmp_path / "Kconfig")
    (tmp_path / "Kconfig").write_text('source "a\x00b"\n')
    with pytest.raises(RejectedConstruct):
        parse_file(tmp_path / "Kconfig")
