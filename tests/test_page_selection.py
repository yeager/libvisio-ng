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


def test_catalog_order_and_layer_visibility(tmp_path):
    from pathlib import Path
    from zipfile import ZipFile

    from libvisio_ng import convert, convert_page, get_page_info

    file = tmp_path / "ordered.vsdx"
    ns = "http://schemas.microsoft.com/office/visio/2012/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    root_rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    with ZipFile(file, "w") as archive:
        archive.writestr("visio/pages/pages.xml", f'''
            <Pages xmlns="{ns}" xmlns:r="{rel}">
              <Page ID="7" Name="Second file first"><Rel r:id="r10"/></Page>
              <Page ID="8" Name="Empty page"><Rel r:id="r1"/></Page>
            </Pages>
        ''')
        archive.writestr("visio/pages/_rels/pages.xml.rels", f'''
            <Relationships xmlns="{root_rel}">
              <Relationship Id="r1" Target="page1.xml"/>
              <Relationship Id="r10" Target="page10.xml"/>
            </Relationships>
        ''')
        archive.writestr("visio/pages/page1.xml", f'<PageContents xmlns="{ns}"/>')
        archive.writestr("visio/pages/page10.xml", f'''
          <PageContents xmlns="{ns}">
            <PageSheet><Section N="Layer"><Row IX="0"><Cell N="Name" V="Labels"/></Row></Section></PageSheet>
            <Shapes><Shape ID="1"><Cell N="PinX" V="2"/><Cell N="PinY" V="2"/>
              <Cell N="Width" V="2"/><Cell N="Height" V="1"/><Cell N="LayerMember" V="0"/>
              <Text>Layer marker</Text></Shape></Shapes>
          </PageContents>
        ''')
    info = get_page_info(str(file))
    assert [page["name"] for page in info] == ["Second file first", "Empty page"]
    assert info[0]["layers"]["0"]["name"] == "Labels"
    files = convert(str(file), str(tmp_path / "all"))
    assert len(files) == 2
    assert "Layer marker" in Path(files[0]).read_text()
    hidden = convert_page(str(file), 0, str(tmp_path / "hidden"), layer_visibility={"0": False})
    assert hidden and "Layer marker" not in Path(hidden).read_text()
