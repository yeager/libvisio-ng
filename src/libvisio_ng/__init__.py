"""libvisio-ng — A Python library for parsing Microsoft Visio files.

Supports both .vsdx (XML-based) and .vsd (binary/OLE2) natively.
No external C++ dependencies required.

Example usage:

    from libvisio_ng import convert, get_page_info, extract_text

    # Convert all pages to SVG files
    svg_files = convert("diagram.vsdx", output_dir="/tmp/output")

    # Get page metadata
    pages = get_page_info("diagram.vsdx")

    # Extract all text content
    text = extract_text("diagram.vsdx")
"""

__version__ = "0.2.0"

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

# VSD binary parser (direct access)
from libvisio_ng._vsd_parser import (
    parse_vsd_file,
    parse_vsd_to_dicts,
    VsdDocument,
    VsdPage,
    VsdShape,
    TextXForm,
    XForm1D,
    ParaFormat,
    ForeignData,
)

__all__ = [
    "convert",
    "convert_page",
    "get_page_info",
    "extract_text",
    "export_to_png",
    "export_to_pdf",
    "find_vsd2xhtml",
    "parse_vsd_file",
    "parse_vsd_to_dicts",
    "VsdDocument",
    "VsdPage",
    "VsdShape",
    "TextXForm",
    "XForm1D",
    "ParaFormat",
    "ForeignData",
    "VISIO_EXTENSIONS",
    "TEMPLATE_EXTENSIONS",
    "STENCIL_EXTENSIONS",
    "ALL_EXTENSIONS",
]
