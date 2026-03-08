# libvisio-ng [![Version](https://img.shields.io/badge/version-0.6.1-blue.svg)](https://github.com/yeager/libvisio-ng)

## Description

libvisio-ng is a Python library for parsing Microsoft Visio files (.vsdx, .vsd) with native support for both XML and binary formats. The library provides a clean, modern API for extracting shapes, pages, and metadata from Visio diagrams, as well as converting them to SVG format.

This library was originally extracted from the vsdview application and has been developed into a standalone component that can be used by other applications and scripts. The core parsing logic is also available as a Rust crate (libvisio-rs) for performance-critical applications.

## Features

- Parse both modern (.vsdx) and legacy (.vsd) Visio files
- Extract shapes, pages, and metadata
- Convert diagrams to SVG format
- Native Python implementation
- Command-line tool (visio2svg) for batch conversion
- Type hints and modern Python support
- Optional PNG/PDF export via Cairo

## Installation

### PyPI

```bash
pip install libvisio-ng
```

### APT Repository (Debian/Ubuntu)

```bash
echo "deb https://yeager.github.io/debian-repo stable main" | sudo tee /etc/apt/sources.list.d/yeager-l10n.list
sudo apt update
sudo apt install libvisio-ng
```

### DNF Repository (Fedora/RHEL)

```bash
sudo dnf config-manager --add-repo https://yeager.github.io/rpm-repo/yeager-l10n.repo
sudo dnf install libvisio-ng
```

### Building from Source

```bash
git clone https://github.com/yeager/libvisio-ng.git
cd libvisio-ng
pip install -e .
```

## Related Projects

- **PyPI Package**: `pip install libvisio-ng`
- **Rust Crate**: Available on crates.io as `libvisio-rs` for high-performance applications
- **Extracted from**: Originally part of the [vsdview](https://github.com/yeager/vsdview) application

## Translation

This application is managed on Transifex: https://app.transifex.com/danielnylander/libvisio-ng/

Available in 11 languages: Swedish, German, French, Spanish, Italian, Portuguese, Dutch, Polish, Czech, Russian, and Chinese (Simplified).

## License

GPL-3.0-or-later

## Author

Daniel Nylander (daniel@danielnylander.se)