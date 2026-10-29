# SPDX-License-Identifier: GPL-2.0
"""The trellis command, the line prompts and the menu state."""

import io
from pathlib import Path

import pytest

from trellis import Configurator, parse_text
from trellis.cli import main
from trellis.models import ChoiceEntry, MenuEntry, SymbolEntry
from trellis.ui.line import LinePrompter
from trellis.ui.menu import KEYS_HELP, MenuState

TREE = """\
mainmenu "Demo"
config MODULES
\tbool "Modules"
\tmodules
menu "Network"
menuconfig NET
\tbool "Networking"
\tdefault y
\thelp
\t  Network support.
if NET
config ETH
\ttristate "Ethernet"
\tselect PHY
endif
config PHY
\ttristate
endmenu
config WIFI
\tbool "Wifi"
\tdepends on NET
choice SCHED
\tprompt "Scheduler"
\tdefault SCHED_B
\thelp
\t  Pick one.
config SCHED_A
\tbool "A"
config SCHED_B
\tbool "B"
endchoice
comment "Sizes"
config BUF
\tint "Buffer"
\trange 1 64
\tdefault 16
config NAME
\tstring "Name"
"""


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "Kconfig").write_text(TREE)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_olddefconfig_and_check(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check"]) == 1
    assert "config.pyb is missing" in capsys.readouterr().err
    assert main(["olddefconfig"]) == 0
    assert "resolved 9 options in" in capsys.readouterr().err
    assert not (work / "defconfig").exists()
    assert main(["check"]) == 0
    (work / "defconfig").write_text("CONFIG_ETH=y\nCONFIG_GONE=y\n")
    assert main(["check"]) == 1
    error = capsys.readouterr().err
    assert "ignoring GONE=y" in error and "out of date" in error


def test_set_and_savedefconfig(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["set", "ETH=y", "NET=y", "BUF=16"]) == 0
    assert (work / "defconfig").read_text() == "CONFIG_NET=y\nCONFIG_ETH=y\nCONFIG_BUF=16\n"
    assert '"PHY": "y"' in (work / "config.pyb").read_text()
    capsys.readouterr()
    assert main(["savedefconfig", "--output", "-"]) == 0
    assert capsys.readouterr().out == "CONFIG_ETH=y\n"
    assert main(["savedefconfig"]) == 0
    assert (work / "defconfig").read_text() == "CONFIG_ETH=y\n"
    assert main(["set", "NET=n"]) == 1
    error = capsys.readouterr().err
    assert "ETH depends on NET" in error and "trellis explain NAME VALUE" in error
    assert (work / "defconfig").read_text() == "CONFIG_ETH=y\n"
    assert main(["set", "NET"]) == 2
    assert main(["set", "NET=m"]) == 2
    assert "takes y or n" in capsys.readouterr().err


def test_defconfig_all_and_dimacs(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (work / "other").write_text("CONFIG_WIFI=y\nCONFIG_GONE=y\n")
    (work / "defconfig").write_text("CONFIG_ETH=y\n")
    assert main(["defconfig", "other"]) == 0
    assert (work / "defconfig").read_text() == "CONFIG_WIFI=y\n"
    assert "ignoring GONE=y" in capsys.readouterr().err
    assert main(["allyes"]) == 0
    assert "CONFIG_MODULES=y" in (work / "defconfig").read_text()
    assert main(["allno"]) == 0
    assert "# CONFIG_NET is not set" in (work / "defconfig").read_text()
    capsys.readouterr()
    assert main(["export-dimacs"]) == 0
    assert "p cnf" in capsys.readouterr().out
    assert main(["export-dimacs", "--output", "clauses.cnf"]) == 0
    assert (work / "clauses.cnf").read_text().startswith("c 1 true")
    assert main(["--kconfig", "missing", "olddefconfig"]) == 2
    assert main(["--solver", "picosat", "--kconfig", "missing", "olddefconfig"]) == 2


def test_explain(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (work / "defconfig").write_text("# CONFIG_NET is not set\n")
    assert main(["explain", "ETH"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("ETH = n\n") and "dependencies do not hold" in out
    assert main(["explain", "ETH", "y"]) == 1
    out = capsys.readouterr().out
    assert "you set NET=n" in out and "1. drop NET=n" in out and "NET: n -> y" in out
    assert main(["explain", "WIFI", "n"]) == 0
    assert "can be n" in capsys.readouterr().out
    (work / "defconfig").write_text("CONFIG_ETH=y\n")
    assert main(["explain", "PHY", "n"]) == 1
    assert "1. drop ETH=y" in capsys.readouterr().out
    (work / "never").write_text('config A\n\tbool "A"\n\tdepends on n\n')
    assert main(["--kconfig", "never", "explain", "A", "y"]) == 1
    assert "No change to the choices allows it." in capsys.readouterr().out


def test_old(
    work: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n\n\n\n1\n32\n"))
    assert main(["old"]) == 0
    out = capsys.readouterr().out
    assert "Modules (MODULES) [N/y/?] (NEW)" in out
    assert (work / "defconfig").read_text() == (
        "CONFIG_MODULES=y\nCONFIG_SCHED_A=y\nCONFIG_BUF=32\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["old"]) == 0
    assert "(NEW)" not in capsys.readouterr().out


def _prompter(configurator: Configurator, answers: str) -> tuple[LinePrompter, io.StringIO]:
    output = io.StringIO()
    return LinePrompter(configurator, set(), io.StringIO(answers), output), output


def test_line_prompter_details() -> None:
    configurator = Configurator(parse_text(TREE))
    prompter, output = _prompter(configurator, "\n?\n\nx\nm\n\n?\n9\n2\n\n\n")
    assert prompter.run() == 7
    text = output.getvalue()
    assert "Network support." in text and "it takes its default" in text
    assert "ETH is tristate and takes y, m or n, not 'x'" in text
    assert "Pick one." in text and "Answer with a number from 1 to 2." in text
    assert [(c.symbol, c.value) for c in configurator.choices] == [
        ("ETH", "m"), ("SCHED_B", "y"),
    ]  # fmt: skip
    assert configurator.resolve().values["MODULES"] == "y"

    configurator = Configurator(parse_text(TREE))
    configurator.set("NET", "y")
    configurator.set("ETH", "y")
    prompter, output = _prompter(configurator, "")
    prompter.known = {"MODULES", "WIFI"}
    assert prompter.try_set("NET", "n") is False
    text = output.getvalue()
    assert "NET=n conflicts with:" in text and "drop ETH=y" in text
    assert [c.symbol for c in configurator.choices] == ["NET", "ETH"]
    assert prompter.run() == 1


def test_menu_state() -> None:
    configurator = Configurator(parse_text(TREE))
    state = MenuState(configurator)
    assert "q quit" in KEYS_HELP
    assert state.title == "Demo"
    texts = [row.text for row in state.rows()]
    assert texts == [
        "[ ] Modules",
        "    Network  --->",
        "[ ] Wifi",
        "    Scheduler (B)  --->",
        "    *** Sizes ***",
        "(16) Buffer",
        "() Name",
    ]
    assert state.enter() is False
    state.cycle()
    assert state.changed and configurator.resolve().values["MODULES"] == "y"
    state.move(1)
    assert state.enter() and state.title == "Demo > Network"
    net = state.current()
    assert net is not None and net.opens and isinstance(net.entry, SymbolEntry)
    assert net.text == "[*] Networking  --->"
    assert state.enter() and state.title == "Demo > Network > Networking"
    assert [r.text for r in state.rows()] == ["< > Ethernet"]
    state.cycle()
    assert configurator.resolve().values["ETH"] == "m"
    assert state.name() == "ETH" and not state.needs_text()
    state.set("ETH", "y")
    assert [r.text for r in state.rows()] == ["<*> Ethernet"]
    assert state.back() and state.back() and not state.back()

    assert state.enter() and state.name() == "NET"
    state.set("NET", "n")
    assert state.message[0] == "NET=n conflicts with:"
    assert any("drop ETH=y" in line for line in state.message)
    assert state.pending == ("NET", "n")
    state.apply(9)
    state.apply(1)
    assert configurator.resolve().values["NET"] == "n"
    assert [c.symbol for c in configurator.choices] == ["MODULES", "NET"]
    assert [r.text for r in state.rows()] == ["[ ] Networking  --->"]
    state.back()

    state.move(1)
    state.toggle_all()
    assert state.level.cursor == 0
    texts = [row.text for row in state.rows()]
    assert "[ ] Wifi  (hidden)" in texts
    state.move(2)
    assert state.name() == "WIFI"
    state.cycle()
    assert state.message[0] == "WIFI=y conflicts with:"
    state.explain()
    assert state.message[0] == "WIFI = n (Kconfig:19)"
    state.toggle_all()

    state.move(2)
    choice = state.current()
    assert choice is not None and isinstance(choice.entry, ChoiceEntry)
    assert state.name() is None
    state.explain()
    state.cycle()
    assert state.enter() and state.title == "Demo > Scheduler"
    assert [r.text for r in state.rows()] == ["( ) A", "(X) B"]
    state.move(-1)
    state.cycle()
    assert [r.text for r in state.rows()] == ["(X) A", "( ) B"]
    state.back()

    state.move(2)
    assert state.name() == "BUF" and state.needs_text()
    state.cycle()
    state.set("BUF", "99")
    assert "between 1 and 64" in "\n".join(state.message)
    state.set("BUF", "zz")
    assert state.message == ["BUF is int, and 'zz' is not"]
    state.set("BUF", "8")
    state.explain()
    assert state.message[0] == "BUF = 8 (Kconfig:33)"


def test_menu_empty_and_unfixable() -> None:
    configurator = Configurator(
        parse_text('menu "M"\nendmenu\nconfig A\n\tbool "A"\n\tdepends on n\n')
    )
    state = MenuState(configurator)
    rows = state.rows()
    assert len(rows) == 1 and isinstance(rows[0].entry, MenuEntry)
    state.enter()
    assert state.current() is None and state.name() is None and not state.enter()
    state.cycle()
    state.explain()
    state.back()
    state.toggle_all()
    state.move(1)
    state.cycle()
    assert "  no change to other choices allows it" in state.message
