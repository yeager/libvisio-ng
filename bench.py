#!/usr/bin/env python3
"""Performance benchmark for libvisio-ng.

Usage:
    python bench.py [FILE ...]

If no files are given, benchmarks all .vsdx fixtures in tests/fixtures/.
"""

import os
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from libvisio_ng import convert, get_page_info, extract_text


def benchmark_file(filepath: str) -> dict:
    """Benchmark parse, convert, and text extraction for a single file."""
    name = os.path.basename(filepath)
    size = os.path.getsize(filepath)
    results: dict = {"file": name, "size_kb": round(size / 1024, 1)}

    # Page info (parsing)
    t0 = time.perf_counter()
    pages = get_page_info(filepath)
    results["parse_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    results["pages"] = len(pages)

    # Text extraction
    t0 = time.perf_counter()
    text = extract_text(filepath)
    results["text_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    results["text_chars"] = len(text)

    # SVG conversion with memory tracking
    tracemalloc.start()
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.perf_counter()
        svg_files = convert(filepath, output_dir=tmp)
        results["svg_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        results["svg_files"] = len(svg_files)
        total_svg_size = sum(os.path.getsize(f) for f in svg_files)
        results["svg_total_kb"] = round(total_svg_size / 1024, 1)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    results["peak_mem_mb"] = round(peak / 1024 / 1024, 1)

    return results


def main() -> None:
    files = sys.argv[1:]
    if not files:
        fixture_dir = Path(__file__).parent / "tests" / "fixtures"
        files = sorted(str(f) for f in fixture_dir.glob("*.vsdx"))
        if not files:
            print("No .vsdx files found in tests/fixtures/")
            sys.exit(1)

    print(f"{'File':<35} {'Size':>7} {'Parse':>8} {'Text':>8} {'SVG':>8} "
          f"{'Mem':>7} {'Pages':>5} {'SVG KB':>8}")
    print("-" * 100)

    for filepath in files:
        try:
            r = benchmark_file(filepath)
            print(f"{r['file']:<35} {r['size_kb']:>6.1f}K {r['parse_ms']:>7.1f}ms "
                  f"{r['text_ms']:>7.1f}ms {r['svg_ms']:>7.1f}ms "
                  f"{r['peak_mem_mb']:>6.1f}M {r['pages']:>5} {r['svg_total_kb']:>7.1f}K")
        except Exception as e:
            print(f"{os.path.basename(filepath):<35} ERROR: {e}")


if __name__ == "__main__":
    main()
