# libvisio-ng

A Python library for parsing and converting Microsoft Visio files (.vsdx, .vsd) to SVG.

Extracted from [vsdview](https://github.com/yeager/vsdview)'s built-in parser, libvisio-ng aims to be a standalone, pip-installable library for working with Visio files in Python.

## Features

- **Native .vsdx parsing** — zero external dependencies for XML-based Visio formats
- **Theme support** — resolves Visio themes, gradients, and shadows
- **Text extraction** — extract all text content from diagrams
- **Page metadata** — enumerate pages with dimensions and names
- **SVG output** — high-fidelity SVG conversion
- **CLI tool** — `visio2svg` command for quick conversions
- **.vsd support** — binary format via optional libvisio backend

## Installation

```bash
pip install libvisio-ng
```

For PNG/PDF export support:
```bash
pip install libvisio-ng[png]
```

## Quick Start

```python
from libvisio_ng import convert, get_page_info, extract_text

# Convert all pages to SVG
svg_files = convert("diagram.vsdx", output_dir="output/")

# Get page information
for page in get_page_info("diagram.vsdx"):
    print(f"{page['name']}: {page['width']}x{page['height']}")

# Extract text
text = extract_text("diagram.vsdx")
```

## CLI

```bash
# Convert to SVG
visio2svg convert diagram.vsdx -o output/

# Show page info
visio2svg info diagram.vsdx

# Extract text
visio2svg text diagram.vsdx
```

## Supported Formats

| Format | Extension | Support |
|--------|-----------|---------|
| Visio Drawing (XML) | .vsdx, .vsdm | ✅ Native |
| Visio Template (XML) | .vstx, .vstm | ✅ Native |
| Visio Stencil (XML) | .vssx, .vssm | ✅ Native |
| Visio Drawing (Binary) | .vsd | 🔧 Via libvisio |
| Visio Template (Binary) | .vst | 🔧 Via libvisio |
| Visio Stencil (Binary) | .vss | 🔧 Via libvisio |

## Roadmap

- [ ] Native .vsd binary format parser (no libvisio dependency)
- [ ] Programmatic shape access API (beyond SVG conversion)
- [ ] Connection/relationship graph extraction
- [ ] Stencil/master shape library support

## Architecture

libvisio-ng uses [libvisio](https://wiki.documentfoundation.org/DLP/Libraries/libvisio) (C++, TDF) as an architectural reference but aims to surpass it with better support for modern .vsdx features like themes, gradients, and shadows.

## License

GPL-3.0-or-later — same as vsdview.

## Author

Daniel Nylander <daniel@danielnylander.se>
