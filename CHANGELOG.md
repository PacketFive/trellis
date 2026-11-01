# Changelog

All notable changes to Trellis are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-11-01

### Changed

- The models use Espalier (`pyrixos-espalier`) in place of pydantic 2,
  so Trellis and its dependencies are pure Python. The models, their
  validation and their JSON are unchanged.

## [1.0.0] - 2026-10-29

First release, published on PyPI as `trellis-kconfig`.

### Added

- A parser and checker for a subset of Kconfig that refuses everything
  outside it with its file and line, including macros and paths outside
  the tree.
- An encoding of the option tree into clauses, a built-in CDCL solver and
  an optional PicoSAT backend that give the same results.
- Resolution of the user's choices into a full configuration, the same
  every time and with every backend.
- Explanations: why an option has its value, and the smallest sets of
  choices to drop that would allow another value.
- Linux-format defconfig files and the generated `config.pyb`.
- The `trellis` command, with `menu` and `old` consoles.
- `Configurator` and the other names in `trellis.__all__` for programs.
