"""Regression coverage for single-page conversion and CLI metadata."""
from pathlib import Path
import sys

from libvisio_ng import convert_page, get_page_info
from libvisio_ng.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "minimal.vsdx"


def test_negative_page_is_not_python_negative_indexing(tmp_path):
    assert convert_page(str(FIXTURE), -1, str(tmp_path)) is None
    assert list(tmp_path.iterdir()) == []


def test_out_of_range_page(tmp_path):
    assert convert_page(str(FIXTURE), 999, str(tmp_path)) is None


def test_single_page_creates_nested_output_directory(tmp_path):
    output = tmp_path / "new" / "nested"
    result = convert_page(str(FIXTURE), 0, str(output))
    assert Path(result).is_file()
    assert Path(result).parent == output


def test_info_displays_actual_dimensions(monkeypatch, capsys):
    page = get_page_info(str(FIXTURE))[0]
    monkeypatch.setattr(sys, "argv", ["visio2svg", "info", str(FIXTURE)])
    main()
    assert f"({page['page_w']}x{page['page_h']})" in capsys.readouterr().out
