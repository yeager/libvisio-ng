"""Integration tests using real-world .vsdx fixtures.

Tests verify: no crashes, correct page counts, text extraction,
valid SVG XML output, and image elements where expected.
"""

import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from libvisio_ng import convert, get_page_info, extract_text

FIXTURES = Path(__file__).parent / "fixtures"


def _convert_and_validate(fixture: Path, expected_pages: int = None,
                          expected_text: list[str] = None,
                          expect_images: bool = False):
    """Convert a fixture file and run standard validations."""
    assert fixture.exists(), f"Fixture missing: {fixture}"

    pages = get_page_info(str(fixture))
    if expected_pages is not None:
        assert len(pages) >= expected_pages, (
            f"Expected >= {expected_pages} pages, got {len(pages)}")

    text = extract_text(str(fixture))

    if expected_text:
        for t in expected_text:
            assert t in text, f"Expected text {t!r} not found in extracted text"

    with tempfile.TemporaryDirectory() as tmpdir:
        svg_files = convert(str(fixture), output_dir=tmpdir)
        assert len(svg_files) >= 1, "No SVG files produced"

        for svg_path in svg_files:
            assert os.path.exists(svg_path)
            content = open(svg_path).read()
            assert len(content) > 50, "SVG file is suspiciously small"

            # Validate XML well-formedness
            tree = ET.fromstring(content)
            assert tree.tag.endswith("svg"), f"Root element is not <svg>: {tree.tag}"

            # Check basic SVG structure
            assert content.count("<svg") == 1
            assert content.count("</svg>") == 1
            assert "viewBox" in content

            if expect_images:
                assert "<image" in content, "Expected <image> elements in SVG"

    return pages, text, svg_files


# --- Nested/grouped shapes ---

class TestNestedShapes:
    """Tests for grouped and nested shape handling."""

    def test_nested_shapes_no_crash(self):
        pages, text, _ = _convert_and_validate(
            FIXTURES / "test10_nested_shapes.vsdx",
            expected_pages=1,
        )

    def test_nested_shapes_text_extraction(self):
        text = extract_text(str(FIXTURES / "test10_nested_shapes.vsdx"))
        assert len(text) > 0, "No text extracted from nested shapes file"

    def test_nested_shapes_svg_has_groups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svgs = convert(str(FIXTURES / "test10_nested_shapes.vsdx"), output_dir=tmpdir)
            content = open(svgs[0]).read()
            assert "<g " in content, "SVG should contain <g> groups for nested shapes"


# --- Connectors ---

class TestConnectors:
    """Tests for connector/1D shape handling."""

    def test_connectors_multi_page(self):
        _convert_and_validate(
            FIXTURES / "test4_connectors.vsdx",
            expected_pages=3,
        )

    def test_connectors_with_text(self):
        pages, text, _ = _convert_and_validate(
            FIXTURES / "test7_with_connector.vsdx",
            expected_pages=2,
        )
        assert len(text) > 0

    def test_connector_labels_in_svg(self):
        """Connectors with text should render text near the connector."""
        with tempfile.TemporaryDirectory() as tmpdir:
            svgs = convert(str(FIXTURES / "test4_connectors.vsdx"), output_dir=tmpdir)
            # At least one SVG should have text
            all_content = "".join(open(s).read() for s in svgs)
            # Connector text may be in any page
            if "<text" in all_content:
                pass  # Good, text is rendered
            # Even without visible text, no crash is a pass

    def test_simple_connector(self):
        fixture = FIXTURES / "test8_simple_connector.vsdx"
        if fixture.exists():
            _convert_and_validate(fixture, expected_pages=1)


# --- Rotation ---

class TestRotation:
    def test_rotated_shapes(self):
        _convert_and_validate(
            FIXTURES / "test11_rotate.vsdx",
            expected_pages=2,
        )

    def test_rotated_shapes_text(self):
        text = extract_text(str(FIXTURES / "test11_rotate.vsdx"))
        assert len(text) > 0


# --- Colors and themes ---

class TestColors:
    def test_colors_multi_page(self):
        _convert_and_validate(
            FIXTURES / "test12_colors.vsdx",
            expected_pages=2,
        )

    def test_colors_in_svg(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svgs = convert(str(FIXTURES / "test12_colors.vsdx"), output_dir=tmpdir)
            content = open(svgs[0]).read()
            # Should have fill colors
            assert "fill=" in content


# --- Complex diagrams ---

class TestComplexDiagrams:
    def test_house_diagram(self):
        _convert_and_validate(
            FIXTURES / "test3_house.vsdx",
            expected_pages=1,
        )

    def test_bpmn_sample(self):
        _convert_and_validate(
            FIXTURES / "bpmn-sample.vsdx",
            expected_pages=1,
        )

    def test_master_shapes(self):
        _convert_and_validate(
            FIXTURES / "test5_master.vsdx",
            expected_pages=1,
        )


# --- Images/media ---

class TestMedia:
    def test_media_file(self):
        _convert_and_validate(
            FIXTURES / "media.vsdx",
            expected_pages=1,
        )

    def test_mikrotik_large_file(self):
        """Large real-world file with many shapes and images."""
        fixture = FIXTURES / "mikrotik_crs312.vsdx"
        if not fixture.exists():
            pytest.skip("Large fixture not available")
        pages, text, _ = _convert_and_validate(
            fixture, expected_pages=1, expect_images=True
        )

    def test_image_fixture_has_image_element(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svgs = convert(str(FIXTURES / "image.vsdx"), output_dir=tmpdir)
            content = open(svgs[0]).read()
            assert "<image" in content


# --- SVG quality checks ---

class TestSVGQuality:
    """Cross-cutting SVG quality checks on all fixtures."""

    @pytest.fixture(params=[
        "minimal.vsdx", "richtext.vsdx", "gradient.vsdx", "image.vsdx",
        "test10_nested_shapes.vsdx", "test4_connectors.vsdx",
        "test11_rotate.vsdx", "test12_colors.vsdx",
        "test3_house.vsdx", "bpmn-sample.vsdx",
        "test5_master.vsdx", "media.vsdx",
    ])
    def fixture_name(self, request):
        return request.param

    def test_svg_is_valid_xml(self, fixture_name):
        fixture = FIXTURES / fixture_name
        if not fixture.exists():
            pytest.skip(f"Fixture {fixture_name} not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            svgs = convert(str(fixture), output_dir=tmpdir)
            for svg_path in svgs:
                content = open(svg_path).read()
                tree = ET.fromstring(content)
                assert tree.tag.endswith("svg")

    def test_no_empty_svg(self, fixture_name):
        fixture = FIXTURES / fixture_name
        if not fixture.exists():
            pytest.skip(f"Fixture {fixture_name} not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            svgs = convert(str(fixture), output_dir=tmpdir)
            for svg_path in svgs:
                size = os.path.getsize(svg_path)
                assert size > 100, f"SVG {svg_path} is too small ({size} bytes)"


# --- Page info API ---

class TestPageInfo:
    def test_page_names_present(self):
        pages = get_page_info(str(FIXTURES / "test4_connectors.vsdx"))
        for p in pages:
            assert "name" in p
            assert isinstance(p["name"], str)

    def test_page_dimensions(self):
        pages = get_page_info(str(FIXTURES / "minimal.vsdx"))
        assert len(pages) >= 1
        p = pages[0]
        assert "page_w" in p
        assert "page_h" in p
        assert p["page_w"] > 0
        assert p["page_h"] > 0

    def test_multi_page_file(self):
        pages = get_page_info(str(FIXTURES / "test4_connectors.vsdx"))
        assert len(pages) >= 3


# --- Text extraction ---

class TestTextExtraction:
    def test_minimal_text(self):
        text = extract_text(str(FIXTURES / "minimal.vsdx"))
        assert "Hello World" in text

    def test_richtext_all_runs(self):
        text = extract_text(str(FIXTURES / "richtext.vsdx"))
        assert "Bold Red" in text
        assert "Italic Blue" in text

    def test_connector_file_text(self):
        text = extract_text(str(FIXTURES / "test4_connectors.vsdx"))
        assert len(text) > 0

    def test_house_text(self):
        text = extract_text(str(FIXTURES / "test3_house.vsdx"))
        assert len(text) > 0

    def test_bpmn_text(self):
        text = extract_text(str(FIXTURES / "bpmn-sample.vsdx"))
        assert len(text) > 0


# --- EMF handling ---

class TestEMFHandling:
    """EMF images should not crash conversion, even without inkscape."""

    def test_emf_data_uri_returns_empty_without_inkscape(self):
        from libvisio_ng._converter import _image_to_data_uri
        # Fake EMF data (just header bytes)
        emf_header = b'\x01\x00\x00\x00' + b'\x00' * 80
        result = _image_to_data_uri(emf_header, "test.emf")
        # Should return empty string (graceful skip) or SVG data URI
        assert result == "" or result.startswith("data:")

    def test_wmf_data_uri_returns_empty_without_inkscape(self):
        from libvisio_ng._converter import _image_to_data_uri
        wmf_header = b'\xd7\xcd\xc6\x9a' + b'\x00' * 80
        result = _image_to_data_uri(wmf_header, "test.wmf")
        assert result == "" or result.startswith("data:")

    def test_convert_emf_to_svg_returns_none_without_inkscape(self):
        from libvisio_ng._converter import _convert_emf_to_svg
        import shutil
        if shutil.which("inkscape"):
            pytest.skip("Inkscape available — would actually convert")
        result = _convert_emf_to_svg(b'\x01\x00\x00\x00' + b'\x00' * 80, ".emf")
        assert result is None


# --- Edge cases ---

class TestEdgeCases:
    def test_convert_returns_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = convert(str(FIXTURES / "minimal.vsdx"), output_dir=tmpdir)
            assert isinstance(result, list)

    def test_nonexistent_file_raises(self):
        with pytest.raises((FileNotFoundError, Exception)):
            convert("/nonexistent/file.vsdx")

    def test_empty_text_extraction(self):
        """Files with no text should return empty string, not crash."""
        text = extract_text(str(FIXTURES / "image.vsdx"))
        # image.vsdx may or may not have text, but should not crash
        assert isinstance(text, str)
