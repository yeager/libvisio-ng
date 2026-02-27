"""libvisio-ng — A Python library for parsing Microsoft Visio files.

Supports .vsdx (XML-based) natively. Supports .vsd (binary) via libvisio
if installed.

Example usage:

    from libvisio_ng import convert, get_page_info, extract_text

    # Convert all pages to SVG files
    svg_files = convert("diagram.vsdx", output_dir="/tmp/output")

    # Get page metadata
    pages = get_page_info("diagram.vsdx")

    # Extract all text content
    text = extract_text("diagram.vsdx")
"""

__version__ = "0.1.0"

from libvisio_ng._converter import (
    convert_vsd_to_svg as convert,
    convert_vsd_page_to_svg as convert_page,
    get_page_info,
    extract_all_text as extract_text,
    export_to_png,
    export_to_pdf,
    find_vsd2xhtml,
    VISIO_EXTENSIONS,
    TEMPLATE_EXTENSIONS,
    STENCIL_EXTENSIONS,
    ALL_EXTENSIONS,
)

__all__ = [
    "convert",
    "convert_page",
    "get_page_info",
    "extract_text",
    "export_to_png",
    "export_to_pdf",
    "find_vsd2xhtml",
    "VISIO_EXTENSIONS",
    "TEMPLATE_EXTENSIONS",
    "STENCIL_EXTENSIONS",
    "ALL_EXTENSIONS",
]
