# libvisio-ng

A Python library for parsing and converting Microsoft Visio files to SVG.

**Native support for both `.vsdx` (XML) and `.vsd` (binary) formats** — no external C++ dependencies required.

## Features

- **Native .vsdx parsing** — zero external dependencies for XML-based Visio formats
- **Native .vsd parsing** — binary OLE2 format support via `olefile` (no libvisio/C++ needed)
- **Theme support** — resolves Visio themes, gradients, and shadows
- **Text extraction** — extract all text content from diagrams
- **Page metadata** — enumerate pages with dimensions and names
- **SVG output** — high-fidelity SVG conversion with geometry, text, and styling
- **CLI tool** — `visio2svg` command for quick conversions
- **PNG/PDF export** — optional CairoSVG-based rasterization

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

# Convert all pages to SVG (works with both .vsdx and .vsd)
svg_files = convert("diagram.vsdx", output_dir="output/")
svg_files = convert("legacy.vsd", output_dir="output/")

# Get page information
for page in get_page_info("diagram.vsdx"):
    print(f"{page['name']}: {page['width']}x{page['height']}")

# Extract text
text = extract_text("diagram.vsdx")
```

### Working with .vsd binary files directly

```python
from libvisio_ng._vsd_parser import parse_vsd_file

doc = parse_vsd_file("legacy.vsd")
for page in doc.pages:
    print(f"Page: {page.name} ({page.width}x{page.height} inches)")
    for shape in page.shapes:
        if shape.text:
            print(f"  Shape {shape.shape_id}: {shape.text}")
        print(f"    Position: ({shape.xform.pin_x}, {shape.xform.pin_y})")
        print(f"    Size: {shape.xform.width}x{shape.xform.height}")
```

## CLI

```bash
# Convert to SVG
visio2svg convert diagram.vsdx -o output/
visio2svg convert legacy.vsd -o output/

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
| Visio Drawing (Binary) | .vsd | ✅ Native (olefile) |
| Visio Template (Binary) | .vst | ✅ Native (olefile) |
| Visio Stencil (Binary) | .vss | ✅ Native (olefile) |

## Architecture

libvisio-ng uses [libvisio](https://wiki.documentfoundation.org/DLP/Libraries/libvisio) (C++) as an architectural reference but is a pure Python implementation. The .vsd binary parser reads OLE2 structured storage and parses Visio's chunk-based record format to extract pages, shapes, text, geometry, and styling.

## License

GPL-3.0-or-later

## Author

Daniel Nylander <daniel@danielnylander.se>
