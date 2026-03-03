"""Basic tests for libvisio-ng."""

import os
import tempfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
MINIMAL_VSDX = FIXTURES / "minimal.vsdx"


def test_import():
    """Test that the package can be imported."""
    import libvisio_ng
    assert libvisio_ng.__version__ == "0.6.0"


def test_extensions():
    """Test extension sets are defined."""
    from libvisio_ng import VISIO_EXTENSIONS, ALL_EXTENSIONS
    assert ".vsdx" in VISIO_EXTENSIONS
    assert ".vsd" in VISIO_EXTENSIONS
    assert len(ALL_EXTENSIONS) > 0


def test_get_page_info():
    """Test page info extraction from minimal .vsdx."""
    from libvisio_ng import get_page_info
    pages = get_page_info(str(MINIMAL_VSDX))
    assert len(pages) >= 1
    assert "name" in pages[0]


def test_extract_text():
    """Test text extraction from minimal .vsdx."""
    from libvisio_ng import extract_text
    text = extract_text(str(MINIMAL_VSDX))
    assert "Hello World" in text


def test_convert():
    """Test SVG conversion from minimal .vsdx."""
    from libvisio_ng import convert
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_files = convert(str(MINIMAL_VSDX), output_dir=tmpdir)
        assert len(svg_files) >= 1
        for f in svg_files:
            assert os.path.exists(f)
            content = open(f).read()
            assert "<svg" in content


def test_cli_version():
    """Test CLI --version flag."""
    import subprocess
    result = subprocess.run(
        ["python3", "-m", "libvisio_ng.cli", "--version"],
        capture_output=True, text=True,
    )
    assert "0.6.0" in result.stdout or "0.6.0" in result.stderr


def test_dash_array():
    """Test line pattern dash array generation."""
    from libvisio_ng._converter import _get_dash_array
    # Solid line = no dash
    assert _get_dash_array(1, 1.0) == ""
    # No line
    assert _get_dash_array(0, 1.0) == "none"
    # Dash pattern
    dash = _get_dash_array(2, 1.0)
    assert dash and "," in dash
    # Unknown pattern 15 still generates something
    assert _get_dash_array(15, 1.0) != ""


def test_fill_pattern_defs():
    """Test fill pattern SVG def generation."""
    from libvisio_ng._converter import _fill_pattern_defs
    pats = {"pat1": {"fg": "#000000", "bg": "#FFFFFF", "type": 2}}
    result = _fill_pattern_defs(pats)
    assert len(result) > 0
    assert 'pattern id="pat1"' in result[0]


def test_convert_svg_contains_defs():
    """Test that converted SVG has proper defs (markers, gradients)."""
    from libvisio_ng import convert
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_files = convert(str(MINIMAL_VSDX), output_dir=tmpdir)
        content = open(svg_files[0]).read()
        assert "<svg" in content
        assert "viewBox" in content


# ---------------------------------------------------------------------------
# Rich text (per-run styling)
# ---------------------------------------------------------------------------

RICHTEXT_VSDX = FIXTURES / "richtext.vsdx"


def test_richtext_extract():
    """Extracting text from a rich-text shape returns all runs."""
    from libvisio_ng import extract_text
    text = extract_text(str(RICHTEXT_VSDX))
    assert "Bold Red" in text
    assert "Italic Blue" in text


def test_richtext_tspan_rendering():
    """Rich text renders as <tspan> elements with per-run styling."""
    from libvisio_ng import convert
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_files = convert(str(RICHTEXT_VSDX), output_dir=tmpdir)
        content = open(svg_files[0]).read()
        assert "<tspan" in content
        assert 'font-weight="bold"' in content
        assert 'font-style="italic"' in content


def test_richtext_colors():
    """Each text run carries its own fill color."""
    from libvisio_ng import convert
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_files = convert(str(RICHTEXT_VSDX), output_dir=tmpdir)
        content = open(svg_files[0]).read()
        assert "#FF0000" in content  # red run
        assert "#0000FF" in content  # blue run


# ---------------------------------------------------------------------------
# Gradient fills (linear + radial)
# ---------------------------------------------------------------------------

GRADIENT_VSDX = FIXTURES / "gradient.vsdx"


def test_linear_gradient():
    """Linear gradient fill produces <linearGradient> in SVG defs."""
    from libvisio_ng import convert
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_files = convert(str(GRADIENT_VSDX), output_dir=tmpdir)
        content = open(svg_files[0]).read()
        assert "<linearGradient" in content
        assert "url(#grad" in content


def test_radial_gradient():
    """Radial gradient fill produces <radialGradient> in SVG defs."""
    from libvisio_ng import convert
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_files = convert(str(GRADIENT_VSDX), output_dir=tmpdir)
        content = open(svg_files[0]).read()
        assert "<radialGradient" in content


def test_gradient_defs_unit():
    """Unit test for _gradient_defs helper with both gradient types."""
    from libvisio_ng._converter import _gradient_defs
    grads = {
        "g_linear": {"start": "#FF0000", "end": "#0000FF", "dir": 0, "radial": False},
        "g_radial": {"start": "#00FF00", "end": "#FFFF00", "dir": 0, "radial": True},
    }
    result = _gradient_defs(grads)
    combined = "\n".join(result)
    assert "<linearGradient" in combined
    assert "<radialGradient" in combined
    assert 'id="g_linear"' in combined
    assert 'id="g_radial"' in combined


# ---------------------------------------------------------------------------
# Image embedding
# ---------------------------------------------------------------------------

IMAGE_VSDX = FIXTURES / "image.vsdx"


def test_image_embedding():
    """Embedded image appears as <image> with data URI in SVG."""
    from libvisio_ng import convert
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_files = convert(str(IMAGE_VSDX), output_dir=tmpdir)
        content = open(svg_files[0]).read()
        assert "<image" in content
        assert "data:image/png;base64," in content


def test_image_to_data_uri():
    """Unit test for _image_to_data_uri helper."""
    from libvisio_ng._converter import _image_to_data_uri
    result = _image_to_data_uri(b"\x89PNG\r\n\x1a\nfake", "test.png")
    assert result.startswith("data:image/png;base64,")


def test_image_to_data_uri_jpeg():
    """JPEG files get the correct MIME type."""
    from libvisio_ng._converter import _image_to_data_uri
    result = _image_to_data_uri(b"\xff\xd8\xff\xe0fake", "photo.jpg")
    assert result.startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------------------
# SVG well-formedness
# ---------------------------------------------------------------------------

def test_svg_wellformed():
    """SVG output is well-formed (single root, closed tags)."""
    from libvisio_ng import convert
    with tempfile.TemporaryDirectory() as tmpdir:
        svg_files = convert(str(MINIMAL_VSDX), output_dir=tmpdir)
        content = open(svg_files[0]).read()
        assert content.count("<svg") == 1
        assert content.count("</svg>") == 1


# ---------------------------------------------------------------------------
# Theme color resolution
# ---------------------------------------------------------------------------

def test_resolve_color_hex():
    """_resolve_color passes through hex colors."""
    from libvisio_ng._converter import _resolve_color
    assert _resolve_color("#FF0000", None) == "#FF0000"
    assert _resolve_color("#ff0000", {}) == "#ff0000"


def test_resolve_color_empty():
    """_resolve_color returns empty string for empty input."""
    from libvisio_ng._converter import _resolve_color
    assert _resolve_color("", None) == ""
    assert _resolve_color(None, None) == ""


# ---------------------------------------------------------------------------
# Text parsing
# ---------------------------------------------------------------------------

def test_parse_text_element():
    """_parse_text_element splits runs by cp/pp references."""
    import xml.etree.ElementTree as ET
    from libvisio_ng._converter import _parse_text_element
    ns = "http://schemas.microsoft.com/office/visio/2012/main"
    xml_str = (
        f'<Text xmlns="{ns}">'
        f'<cp IX="0"/>Hello <cp IX="1"/>World</Text>'
    )
    elem = ET.fromstring(xml_str)
    parts = _parse_text_element(elem)
    texts = [p["text"] for p in parts]
    assert "Hello " in texts
    assert "World" in texts
    cps = [p["cp"] for p in parts]
    assert "0" in cps
    assert "1" in cps


def test_version_consistency():
    """Version in __init__.py matches pyproject.toml."""
    import libvisio_ng
    import tomllib
    toml_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    assert libvisio_ng.__version__ == data["project"]["version"]


def test_group_dimension_fallback_from_master_subs():
    """When a group has Width=0 Height=0 and sub-shapes have no cells,
    the renderer should estimate group dimensions from master sub-shapes."""
    from libvisio_ng._converter import _render_shape_svg

    # Build a minimal group shape with zero dimensions and empty sub-shapes
    masters = {
        "99": {
            "500": {
                "id": "500", "type": "Group", "geometry": [],
                "cells": {"Width": {"V": "2.0", "F": ""},
                           "Height": {"V": "3.0", "F": ""}},
                "sub_shapes": [],
                "text": [], "connections": [],
            },
            "10": {
                "id": "10", "type": "Shape", "geometry": [],
                "cells": {"PinX": {"V": "0.5", "F": ""},
                           "PinY": {"V": "1.0", "F": ""},
                           "Width": {"V": "0.2", "F": ""},
                           "Height": {"V": "0.1", "F": ""}},
                "text": [], "connections": [],
            },
            "11": {
                "id": "11", "type": "Shape", "geometry": [],
                "cells": {"PinX": {"V": "1.5", "F": ""},
                           "PinY": {"V": "2.5", "F": ""},
                           "Width": {"V": "0.2", "F": ""},
                           "Height": {"V": "0.1", "F": ""}},
                "text": [], "connections": [],
            },
        }
    }

    _empty_shape_fields = {
        "geometry": [], "text": [], "connections": [],
        "text_parts": [], "char_formats": [], "para_formats": [],
        "tab_stops": [], "user": {}, "foreign_data": None,
        "name_u": "", "name": "",
    }

    group_shape = {
        "id": "900", "type": "Group", "master": "99",
        "cells": {
            "PinX": {"V": "5.0", "F": ""},
            "PinY": {"V": "5.0", "F": ""},
            "Width": {"V": "0", "F": ""},
            "Height": {"V": "0", "F": ""},
            "LocPinX": {"V": "0", "F": ""},
            "LocPinY": {"V": "0", "F": ""},
        },
        **_empty_shape_fields,
        "sub_shapes": [
            {"id": "901", "type": "Shape", "master_shape": "10",
             "cells": {}, **_empty_shape_fields, "sub_shapes": []},
            {"id": "902", "type": "Shape", "master_shape": "11",
             "cells": {}, **_empty_shape_fields, "sub_shapes": []},
        ],
    }

    # Render — should NOT crash and should produce a group with some content
    svg_lines = _render_shape_svg(group_shape, 11.0, masters, "")
    svg = "\n".join(svg_lines)
    # Should contain a group transform (not collapsed to origin)
    assert "<g transform=" in svg
