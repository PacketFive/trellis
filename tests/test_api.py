# SPDX-License-Identifier: GPL-2.0
"""The Configurator and the files it reads and writes."""

import os
from pathlib import Path

import pytest

from trellis import (
    Assignment,
    Configurator,
    ConflictError,
    InvalidValue,
    parse_text,
    read_config,
    read_defconfig,
    write_defconfig,
)
from trellis.files import write_file

TREE = """\
mainmenu "Demo"
config MODULES
\tbool "Modules"
\tmodules
config NET
\tbool "Networking"
\tdefault y
config ETH
\ttristate "Ethernet"
\tdepends on NET
\tselect PHY
config PHY
\ttristate
config WIFI
\tbool "Wifi"
\tdepends on NET
\timply PHY
choice SCHED
\tprompt "Scheduler"
\tdefault SCHED_B
config SCHED_A
\tbool "A"
config SCHED_B
\tbool "B"
endchoice
config BUF
\tint "Buffer"
\trange 1 64
\tdefault 16
config BASE
\thex "Base"
\tdefault 0x1000
config NAME
\tstring "Name"
\tdefault "x"
"""


@pytest.fixture
def configurator() -> Configurator:
    return Configurator(parse_text(TREE))


def test_set_and_normalise(configurator: Configurator) -> None:
    configurator.set("BUF", "010")
    configurator.set("BASE", "0X1F")
    configurator.set("ETH", "m")
    assert [(c.symbol, c.value) for c in configurator.choices] == [
        ("ETH", "m"), ("BUF", "10"), ("BASE", "0x1f"),
    ]  # fmt: skip
    for name, value, message in (
        ("NOPE", "y", "not an option"),
        ("NET", "m", "takes y or n"),
        ("ETH", "x", "takes y, m or n"),
        ("BUF", "0x10", "is int"),
        ("PHY", "y", "has no prompt"),
    ):
        with pytest.raises(InvalidValue, match=message):
            configurator.set(name, value)
    plain = Configurator(parse_text('config T\n\ttristate "T"\n'))
    with pytest.raises(InvalidValue, match="no modules option"):
        plain.set("T", "m")


def test_choice_members_replace_each_other(configurator: Configurator) -> None:
    configurator.set("SCHED_A", "y")
    configurator.set("SCHED_B", "y")
    assert [c.symbol for c in configurator.choices] == ["SCHED_B"]
    configurator.unset("SCHED_B")
    configurator.unset("SCHED_B")
    assert configurator.choices == ()


def test_resolve_is_cached(configurator: Configurator) -> None:
    first = configurator.resolve()
    assert configurator.resolve() is first
    configurator.set("WIFI", "y")
    assert configurator.resolve() is not first
    assert configurator.resolve().values["PHY"] == "y"


def test_conflict_and_fixes(configurator: Configurator) -> None:
    configurator.set("NET", "n")
    assert configurator.conflict("WIFI", "n") is None
    conflict = configurator.conflict("ETH", "y")
    assert conflict is not None and conflict.choices == (Assignment(symbol="NET", value="n"),)
    (fix,) = configurator.fixes("ETH", "y")
    assert fix.dropped == (Assignment(symbol="NET", value="n"),)
    assert {(c.symbol, c.before, c.after) for c in fix.changes} == {
        ("NET", "n", "y"), ("ETH", "n", "y"), ("PHY", "n", "y"),
    }  # fmt: skip
    with pytest.raises(InvalidValue, match="not bool or tristate"):
        configurator.conflict("BUF", "1")
    with pytest.raises(InvalidValue, match="not bool or tristate"):
        configurator.fixes("NAME", "x")
    configurator.set("ETH", "y")
    with pytest.raises(ConflictError):
        configurator.resolve()
    assert configurator.fixes("ETH", "y")[0].dropped == (Assignment(symbol="NET", value="n"),)


def test_explain(configurator: Configurator) -> None:
    configurator.set("ETH", "y")
    explanation = configurator.explain("PHY")
    assert explanation.value == "y"
    assert "ETH=y selects it (Kconfig:8)" in explanation.reasons
    assert any(
        r.startswith("it cannot be n: you set ETH=y; ETH selects PHY") for r in explanation.reasons
    )
    assert configurator.explain("NET").reasons[0] == "it takes its default"
    assert configurator.explain("ETH").reasons[0] == "you set ETH=y"
    assert configurator.explain("BUF").reasons == ("it takes its default",)
    assert any(
        "choice" in r and "SCHED_B is chosen" in r for r in configurator.explain("SCHED_A").reasons
    )
    configurator.unset("ETH")
    configurator.set("NET", "n")
    reasons = configurator.explain("ETH").reasons
    assert reasons[0] == "its dependencies do not hold: NET (NET=n)"
    assert configurator.explain("WIFI").value == "n"
    configurator.set("WIFI", "y")
    assert "cannot all hold" in configurator.explain("WIFI").reasons[0]


def test_minimal_set_all_and_restore(configurator: Configurator) -> None:
    configurator.set("NET", "y")
    configurator.set("SCHED_B", "y")
    configurator.set("ETH", "y")
    configurator.set("BUF", "16")
    assert configurator.minimal() == [Assignment(symbol="ETH", value="y")]
    saved = configurator.choices
    configurator.set_all("y")
    values = configurator.resolve().values
    assert values["MODULES"] == "y" and values["WIFI"] == "y" and values["ETH"] == "y"
    configurator.restore(saved)
    assert configurator.choices == saved
    configurator.set_all("n")
    assert configurator.resolve().values["NET"] == "y"
    configurator.restore(())
    configurator.set_all("n")
    assert configurator.resolve().values["NET"] == "n"


def test_set_all_keeps_earlier_choices() -> None:
    text = 'config A\n\tbool "A"\nconfig B\n\tbool "B"\n\tdepends on !A\n'
    configurator = Configurator(parse_text(text), [Assignment(symbol="B", value="y")])
    configurator.set_all("y")
    assert configurator.resolve().values == {"A": "n", "B": "y"}


def test_defconfig_files() -> None:
    text = (
        "CONFIG_NET=y\n# CONFIG_WIFI is not set\n# a comment\n\n"
        'CONFIG_NAME="a \\"b\\" \\\\c"\nCONFIG_BUF=8\nCONFIG_NET=n\n'
    )
    choices = read_defconfig(text)
    assert [(c.symbol, c.value) for c in choices] == [
        ("NET", "n"), ("WIFI", "n"), ("NAME", 'a "b" \\c'), ("BUF", "8"),
    ]  # fmt: skip
    tree = parse_text(TREE)
    written = write_defconfig([*choices, Assignment(symbol="GONE", value="1")], tree)
    assert written == (
        "# CONFIG_NET is not set\n# CONFIG_WIFI is not set\nCONFIG_BUF=8\n"
        'CONFIG_NAME="a \\"b\\" \\\\c"\nCONFIG_GONE="1"\n'
    )
    for bad, message in (("NET=y\n", "not a CONFIG_ line"), ('CONFIG_X="a\n', "unterminated")):
        with pytest.raises(InvalidValue, match=message):
            read_defconfig(bad, "d")


def test_config_pyb(configurator: Configurator) -> None:
    configurator.set("ETH", "m")
    text = configurator.config_text()
    assert text.startswith('# Generated by trellis from the option tree "Demo".')
    assert '    "MODULES": True,\n' in text and '    "ETH": "m",\n' in text
    assert '    "BUF": 16,\n' in text and '    "BASE": 0x1000,\n' in text
    assert '    "NAME": "x",\n' in text
    tree = configurator.tree
    values = read_config(text, tree)
    assert values == configurator.resolve().values
    assert read_config('options = {"NET": False, "OLD": 1, 2: 3}\n', tree) == {"NET": "n"}
    for bad in ("options = {", "x = 1\n", "options = [1]\n", "options = f()\n"):
        with pytest.raises(InvalidValue):
            read_config(bad, tree)


def test_config_pyb_is_pybonsai(configurator: Configurator) -> None:
    pybonsai = pytest.importorskip("pybonsai")
    options = pybonsai.evaluate_source(configurator.config_text())["options"]
    assert options["NET"] is True and options["BUF"] == 16


def test_load_and_save(tmp_path: Path) -> None:
    (tmp_path / "Kconfig").write_text(TREE)
    defconfig = tmp_path / "defconfig"
    defconfig.write_text("CONFIG_ETH=y\nCONFIG_PHY=y\nCONFIG_GONE=y\n")
    configurator = Configurator.load(tmp_path / "Kconfig", defconfig)
    assert [c.symbol for c in configurator.ignored] == ["PHY", "GONE"]
    assert [c.symbol for c in configurator.choices] == ["ETH"]
    defconfig.chmod(0o600)
    config = tmp_path / "config.pyb"
    configurator.save(defconfig, config)
    assert defconfig.read_text() == "CONFIG_ETH=y\n"
    assert (defconfig.stat().st_mode & 0o777) == 0o600
    assert (config.stat().st_mode & 0o777) == 0o644
    assert read_config(config.read_text(), configurator.tree)["PHY"] == "y"
    assert Configurator.load(tmp_path / "Kconfig", tmp_path / "missing").choices == ()
    assert configurator.dimacs().startswith("c 1 true\n")

    configurator.set("NET", "n")
    with pytest.raises(ConflictError):
        configurator.save(defconfig, config)
    assert defconfig.read_text() == "CONFIG_ETH=y\n"
    assert sorted(os.listdir(tmp_path)) == ["Kconfig", "config.pyb", "defconfig"]


def test_write_file_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        write_file(tmp_path / "out", "text")
    assert os.listdir(tmp_path) == []
