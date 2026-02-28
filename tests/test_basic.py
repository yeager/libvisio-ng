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
    assert libvisio_ng.__version__ == "0.5.0"


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
    assert "0.5.0" in result.stdout or "0.5.0" in result.stderr
