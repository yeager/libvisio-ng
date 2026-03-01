"""Stress tests and performance benchmarks for libvisio-ng.

These tests are marked slow and excluded from normal CI runs.
Run with: pytest tests/test_stress.py -v
"""

import os
import tempfile
import time

import pytest

from libvisio_ng import convert, get_page_info, extract_text

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.mark.slow
class TestPondzodNetwork:
    """Stress test using the large pondzod-network.vsdx fixture."""

    FIXTURE = os.path.join(FIXTURES, "pondzod-network.vsdx")

    def test_fixture_exists(self) -> None:
        assert os.path.exists(self.FIXTURE), "pondzod-network.vsdx fixture missing"
        assert os.path.getsize(self.FIXTURE) > 0, "pondzod-network.vsdx is empty"

    def test_shape_count(self) -> None:
        """Verify the expected number of shapes."""
        pages = get_page_info(self.FIXTURE)
        assert len(pages) >= 1
        total_shapes = sum(len(p["shapes"]) for p in pages)
        assert total_shapes >= 858, f"Expected ≥858 shapes, got {total_shapes}"

    def test_convert_produces_svg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svgs = convert(self.FIXTURE, output_dir=tmp)
            assert len(svgs) >= 1
            for svg in svgs:
                size = os.path.getsize(svg)
                assert size > 1000, f"SVG too small: {size} bytes"

    def test_text_extraction(self) -> None:
        text = extract_text(self.FIXTURE)
        assert len(text) > 100, f"Too little text: {len(text)} chars"

    def test_conversion_time(self) -> None:
        """Conversion should complete in under 10 seconds."""
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.perf_counter()
            convert(self.FIXTURE, output_dir=tmp)
            elapsed = time.perf_counter() - t0
            assert elapsed < 10.0, f"Conversion took {elapsed:.1f}s (limit: 10s)"


@pytest.mark.slow
class TestPerformanceBenchmark:
    """Performance benchmarks for all fixture files."""

    def test_all_fixtures_convert(self) -> None:
        """Convert every fixture and report timing."""
        fixture_dir = FIXTURES
        results = []
        for fname in sorted(os.listdir(fixture_dir)):
            if not fname.endswith((".vsdx", ".vsd")):
                continue
            path = os.path.join(fixture_dir, fname)
            if os.path.getsize(path) == 0:
                continue
            with tempfile.TemporaryDirectory() as tmp:
                t0 = time.perf_counter()
                try:
                    svgs = convert(path, output_dir=tmp)
                    elapsed = time.perf_counter() - t0
                    results.append((fname, elapsed, len(svgs), "OK"))
                except Exception as e:
                    elapsed = time.perf_counter() - t0
                    results.append((fname, elapsed, 0, str(e)[:50]))

        print("\n--- Performance Results ---")
        for name, t, n, status in results:
            print(f"  {name:<40} {t*1000:>8.1f}ms  {n} SVGs  {status}")
        assert len(results) > 0
