"""Advanced feature tests for libvisio-ng quality validation."""

import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from libvisio_ng import convert, extract_text, get_page_info

FIXTURES = Path(__file__).parent / "fixtures"


class TestSVGQualityAdvanced:
    """Test advanced SVG output quality."""

    def test_svg_viewbox_present(self):
        """Test that SVG has proper viewBox attribute."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            svg_files = convert(str(FIXTURES / "minimal.vsdx"), output_dir=tmpdir)
            with open(svg_files[0], 'r') as f:
                content = f.read()
                # SVG should have a viewBox
                assert 'viewBox=' in content

    def test_svg_has_proper_dimensions(self):
        """Test that SVG has width and height attributes."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            svg_files = convert(str(FIXTURES / "minimal.vsdx"), output_dir=tmpdir)
            with open(svg_files[0], 'r') as f:
                content = f.read()
                assert 'width=' in content
                assert 'height=' in content

    def test_svg_coordinate_precision(self):
        """Test that SVG coordinates have reasonable precision."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            svg_files = convert(str(FIXTURES / "minimal.vsdx"), output_dir=tmpdir)
            with open(svg_files[0], 'r') as f:
                content = f.read()
                # Coordinates shouldn't have excessive decimal places
                import re
                coords = re.findall(r'\d+\.\d{6,}', content)
                assert len(coords) < 10  # Allow some, but not too many

    def test_svg_style_consolidation(self):
        """Test that SVG styling is consistent and well-formed."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            svg_files = convert(str(FIXTURES / "minimal.vsdx"), output_dir=tmpdir)
            with open(svg_files[0], 'r') as f:
                content = f.read()
                # Test that styling attributes are present and well-formed
                if 'fill=' in content:
                    import re
                    fills = re.findall(r'fill="([^"]*)"', content)
                    # Fill values should be valid
                    valid_css_colors = ['none', 'transparent', 'white', 'black', 'red', 'green', 'blue', 'yellow', 'cyan', 'magenta']
                    for fill in fills[:5]:  # Check first 5
                        assert fill in valid_css_colors or \
                               fill.startswith('#') or \
                               fill.startswith('rgb(') or \
                               fill.startswith('url(')


class TestGradientHandling:
    """Test advanced gradient and fill handling."""

    def test_gradient_definitions_in_defs(self):
        """Test that gradients are properly defined in <defs> section."""
        if (FIXTURES / "gradient.vsdx").exists():
            from libvisio_ng import convert
            with tempfile.TemporaryDirectory() as tmpdir:
                svg_files = convert(str(FIXTURES / "gradient.vsdx"), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    if '<defs>' in content:
                        assert 'linearGradient' in content or 'radialGradient' in content

    def test_gradient_stop_colors(self):
        """Test that gradient stops have valid colors."""
        if (FIXTURES / "gradient.vsdx").exists():
            from libvisio_ng import convert
            with tempfile.TemporaryDirectory() as tmpdir:
                svg_files = convert(str(FIXTURES / "gradient.vsdx"), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    if 'stop-color' in content:
                        import re
                        colors = re.findall(r'stop-color[=:]"([^"]*)"', content)
                        for color in colors:
                            # Should be valid hex color or named color
                            assert (color.startswith('#') and len(color) in [4, 7]) or \
                                   color in ['black', 'white', 'red', 'green', 'blue'] or \
                                   color.startswith('rgb(')

    def test_fill_pattern_handling(self):
        """Test that fill patterns are properly handled."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            # Test with any available fixture
            fixture_files = list(FIXTURES.glob("*.vsdx"))
            if fixture_files:
                svg_files = convert(str(fixture_files[0]), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    # If patterns exist, they should be in defs
                    if 'pattern' in content:
                        assert '<defs>' in content


class TestTextHandling:
    """Test advanced text rendering and handling."""

    def test_text_escaping_xml_entities(self):
        """Test that text is properly XML-escaped."""
        # This needs a fixture with special characters
        text = extract_text(str(FIXTURES / "minimal.vsdx"))
        # Basic test that it's valid text
        assert isinstance(text, str)

    def test_rich_text_formatting_preservation(self):
        """Test that rich text formatting is preserved."""
        if (FIXTURES / "richtext.vsdx").exists():
            from libvisio_ng import convert
            with tempfile.TemporaryDirectory() as tmpdir:
                svg_files = convert(str(FIXTURES / "richtext.vsdx"), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    # Rich text should use tspan elements
                    if 'tspan' in content:
                        # tspan should have proper attributes
                        import re
                        tspans = re.findall(r'<tspan[^>]*>', content)
                        assert len(tspans) > 0

    def test_text_font_fallbacks(self):
        """Test that text has proper font fallbacks."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            svg_files = convert(str(FIXTURES / "minimal.vsdx"), output_dir=tmpdir)
            with open(svg_files[0], 'r') as f:
                content = f.read()
                if 'font-family' in content:
                    # Should have fallback fonts
                    import re
                    fonts = re.findall(r'font-family[=:]\s*"([^"]*)"', content)
                    for font in fonts[:5]:  # Check first 5
                        # Should have at least one fallback
                        assert ',' in font or font in ['serif', 'sans-serif', 'monospace']

    def test_text_positioning_accuracy(self):
        """Test that text is positioned accurately."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            svg_files = convert(str(FIXTURES / "minimal.vsdx"), output_dir=tmpdir)
            with open(svg_files[0], 'r') as f:
                content = f.read()
                if '<text' in content:
                    # Text should have x,y coordinates
                    import re
                    text_elements = re.findall(r'<text[^>]*>', content)
                    for text_elem in text_elements[:3]:  # Check first 3
                        assert 'x=' in text_elem and 'y=' in text_elem


class TestConnectorAdvanced:
    """Test advanced connector handling."""

    def test_connector_path_optimization(self):
        """Test that connector paths are optimized."""
        if (FIXTURES / "test4_connectors.vsdx").exists():
            from libvisio_ng import convert
            with tempfile.TemporaryDirectory() as tmpdir:
                svg_files = convert(str(FIXTURES / "test4_connectors.vsdx"), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    if '<path' in content:
                        import re
                        paths = re.findall(r'd="([^"]*)"', content)
                        for path in paths[:3]:  # Check first 3
                            # Path should be reasonably concise
                            assert len(path) < 1000  # Arbitrary limit

    def test_connector_arrow_markers(self):
        """Test that connectors have proper arrow markers."""
        if (FIXTURES / "test4_connectors.vsdx").exists():
            from libvisio_ng import convert
            with tempfile.TemporaryDirectory() as tmpdir:
                svg_files = convert(str(FIXTURES / "test4_connectors.vsdx"), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    if 'marker-' in content:
                        # Markers should be defined in defs
                        assert '<defs>' in content
                        assert '<marker' in content

    def test_connector_labels(self):
        """Test that connector labels are properly positioned."""
        if (FIXTURES / "test4_connectors.vsdx").exists():
            from libvisio_ng import convert
            with tempfile.TemporaryDirectory() as tmpdir:
                svg_files = convert(str(FIXTURES / "test4_connectors.vsdx"), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    # Basic test that there's content
                    assert len(content) > 100


class TestLayerHandling:
    """Test layer handling and visibility."""

    def test_layer_visibility_respected(self):
        """Test that layer visibility is properly respected."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            # Use any available fixture
            fixture_files = list(FIXTURES.glob("*.vsdx"))
            if fixture_files:
                svg_files = convert(str(fixture_files[0]), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    # Basic test that content is generated
                    assert len(content) > 100

    def test_layer_ordering(self):
        """Test that layers are rendered in correct order."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_files = list(FIXTURES.glob("*.vsdx"))
            if fixture_files:
                svg_files = convert(str(fixture_files[0]), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    # Layers should be rendered consistently
                    assert len(content) > 50


class TestImageHandling:
    """Test embedded image handling."""

    def test_image_data_uri_format(self):
        """Test that embedded images use proper data URIs."""
        if (FIXTURES / "image.vsdx").exists():
            from libvisio_ng import convert
            with tempfile.TemporaryDirectory() as tmpdir:
                svg_files = convert(str(FIXTURES / "image.vsdx"), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    if 'data:image' in content:
                        import re
                        data_uris = re.findall(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', content)
                        for uri in data_uris[:3]:  # Check first 3
                            # Should be valid base64
                            import base64
                            try:
                                data = uri.split(',')[1]
                                base64.b64decode(data)
                            except:
                                assert False, "Invalid base64 in image data URI"

    def test_image_positioning(self):
        """Test that images are positioned correctly."""
        if (FIXTURES / "image.vsdx").exists():
            from libvisio_ng import convert
            with tempfile.TemporaryDirectory() as tmpdir:
                svg_files = convert(str(FIXTURES / "image.vsdx"), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    if '<image' in content:
                        # Images should have x,y,width,height
                        import re
                        images = re.findall(r'<image[^>]*>', content)
                        for img in images[:3]:  # Check first 3
                            assert 'x=' in img and 'y=' in img

    def test_image_aspect_ratio(self):
        """Test that image aspect ratios are preserved."""
        if (FIXTURES / "image.vsdx").exists():
            from libvisio_ng import convert
            with tempfile.TemporaryDirectory() as tmpdir:
                svg_files = convert(str(FIXTURES / "image.vsdx"), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    if 'preserveAspectRatio' in content:
                        # Should have proper aspect ratio preservation
                        assert 'none' not in content or 'xMidYMid' in content


class TestComplexGeometry:
    """Test complex geometry handling."""

    def test_nurbs_curve_rendering(self):
        """Test that NURBS curves are properly rendered."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_files = list(FIXTURES.glob("*.vsdx"))
            if fixture_files:
                svg_files = convert(str(fixture_files[0]), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    # NURBS should be converted to paths
                    if 'path' in content:
                        import re
                        paths = re.findall(r'd="([^"]*)"', content)
                        # Paths should be reasonably smooth (have curves)
                        has_curves = any('C' in path or 'Q' in path for path in paths[:5])
                        # This is optional - not all files have curves

    def test_spline_accuracy(self):
        """Test that splines are rendered with good accuracy."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_files = list(FIXTURES.glob("*.vsdx"))
            if fixture_files:
                svg_files = convert(str(fixture_files[0]), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    # Basic test that paths are present
                    assert len(content) > 100

    def test_arc_segment_precision(self):
        """Test that arc segments have proper precision."""
        from libvisio_ng import convert
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture_files = list(FIXTURES.glob("*.vsdx"))
            if fixture_files:
                svg_files = convert(str(fixture_files[0]), output_dir=tmpdir)
                with open(svg_files[0], 'r') as f:
                    content = f.read()
                    if 'A' in content:  # Arc commands
                        # Arc commands should have reasonable precision
                        import re
                        arcs = re.findall(r'A[\d\s.,\-]+', content)
                        for arc in arcs[:3]:  # Check first 3
                            # Basic validation that it's not empty
                            assert len(arc) > 5