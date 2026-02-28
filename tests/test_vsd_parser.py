"""Tests for VSD binary parser."""

import struct
from libvisio_ng._vsd_parser import (
    VsdParser, VsdDocument, VsdShape, VsdPage, VsdParser,
    GeomRow, GeomSection, CharFormat, ParaFormat,
    TextXForm, XForm1D, XForm, ForeignData, ConnectionPoint,
    _vsd_shape_to_dict, _parse_chunk_header, _read_pointer,
    _read_u8, _read_u16, _read_u32, _read_double,
    ChunkHeader, Pointer,
)


def test_binary_reading_helpers():
    """Test low-level binary reading functions."""
    data = struct.pack('<BHId', 42, 1000, 99999, 3.14)
    val, off = _read_u8(data, 0)
    assert val == 42
    val, off = _read_u16(data, 1)
    assert val == 1000
    val, off = _read_u32(data, 3)
    assert val == 99999
    val, off = _read_double(data, 7)
    assert abs(val - 3.14) < 0.001


def test_binary_reading_out_of_bounds():
    """Test that out-of-bounds reads return defaults."""
    data = b'\x01'
    val, off = _read_u16(data, 0)
    assert val == 0
    val, off = _read_u32(data, 0)
    assert val == 0
    val, off = _read_double(data, 0)
    assert val == 0.0


def test_vsd_shape_to_dict_basic():
    """Test converting a VsdShape to dict format."""
    shape = VsdShape(shape_id=1)
    shape.xform.pin_x = 4.25
    shape.xform.pin_y = 5.5
    shape.xform.width = 2.0
    shape.xform.height = 1.0
    shape.text = "Hello"
    shape.line_color = "#FF0000"
    shape.fill_foreground = "#00FF00"

    d = _vsd_shape_to_dict(shape)
    assert d["id"] == "1"
    assert d["text"] == "Hello"
    assert d["cells"]["PinX"]["V"] == "4.25"
    assert d["cells"]["LineColor"]["V"] == "#FF0000"
    assert d["cells"]["FillForegnd"]["V"] == "#00FF00"
    assert len(d["text_parts"]) == 1
    assert d["text_parts"][0]["text"] == "Hello"


def test_vsd_shape_to_dict_text_xform():
    """Test text transform export."""
    shape = VsdShape(shape_id=2)
    shape.text_xform = TextXForm(txt_pin_x=1.0, txt_pin_y=2.0,
                                  txt_width=3.0, txt_height=0.5)
    d = _vsd_shape_to_dict(shape)
    assert d["cells"]["TxtPinX"]["V"] == "1.0"
    assert d["cells"]["TxtWidth"]["V"] == "3.0"


def test_vsd_shape_to_dict_xform_1d():
    """Test 1D connector endpoint export."""
    shape = VsdShape(shape_id=3)
    shape.xform_1d = XForm1D(begin_x=1.0, begin_y=2.0, end_x=5.0, end_y=6.0)
    d = _vsd_shape_to_dict(shape)
    assert d["cells"]["BeginX"]["V"] == "1.0"
    assert d["cells"]["EndX"]["V"] == "5.0"


def test_vsd_shape_to_dict_geometry():
    """Test geometry export with various row types."""
    shape = VsdShape(shape_id=4)
    geom = GeomSection()
    geom.rows.append(GeomRow(row_type="MoveTo", x=0.0, y=0.0))
    geom.rows.append(GeomRow(row_type="LineTo", x=1.0, y=0.0))
    geom.rows.append(GeomRow(row_type="ArcTo", x=2.0, y=1.0, a=0.5))
    geom.rows.append(GeomRow(row_type="NURBSTo", x=3.0, y=2.0,
                              knot_last=1.0, degree=3,
                              points=[(0.5, 0.5, 0.25, 1.0), (1.5, 1.5, 0.75, 1.0)]))
    geom.rows.append(GeomRow(row_type="PolylineTo", x=4.0, y=3.0,
                              points=[(1.0, 1.0), (2.0, 2.0)]))
    shape.geometry.append(geom)

    d = _vsd_shape_to_dict(shape)
    rows = d["geometry"][0]["rows"]
    assert len(rows) == 5
    assert rows[0]["type"] == "MoveTo"
    assert rows[3]["type"] == "NURBSTo"
    assert "E" in rows[3]["cells"]
    assert rows[4]["type"] == "PolylineTo"
    assert "A" in rows[4]["cells"]


def test_vsd_shape_to_dict_para_formats():
    """Test paragraph format export."""
    shape = VsdShape(shape_id=5)
    shape.para_formats.append(ParaFormat(horiz_align=1, bullet=1))
    d = _vsd_shape_to_dict(shape)
    assert "0" in d["para_formats"]
    assert d["para_formats"]["0"]["HorzAlign"] == "1"
    assert d["para_formats"]["0"]["Bullet"] == "1"


def test_vsd_shape_to_dict_shadow():
    """Test shadow data export."""
    shape = VsdShape(shape_id=6)
    shape.shadow_color = "#808080"
    shape.shadow_offset_x = 0.05
    shape.shadow_offset_y = -0.05
    shape.shadow_pattern = 1
    d = _vsd_shape_to_dict(shape)
    assert d["cells"]["ShdwForegnd"]["V"] == "#808080"
    assert d["cells"]["ShapeShdwOffsetX"]["V"] == "0.05"


def test_vsd_shape_to_dict_text_block():
    """Test text block settings export."""
    shape = VsdShape(shape_id=7)
    shape.text_block_margin_left = 0.1
    shape.text_block_margin_right = 0.1
    shape.text_block_valign = 1  # middle
    d = _vsd_shape_to_dict(shape)
    assert d["cells"]["LeftMargin"]["V"] == "0.1"
    assert d["cells"]["VerticalAlign"]["V"] == "1"


def test_vsd_shape_to_dict_connection_points():
    """Test connection point export."""
    shape = VsdShape(shape_id=8)
    shape.connection_points = [ConnectionPoint(x=0.5, y=0.5), ConnectionPoint(x=1.0, y=0.0)]
    d = _vsd_shape_to_dict(shape)
    assert "0" in d["connections"]
    assert d["connections"]["0"]["X"]["V"] == "0.5"


def test_vsd_shape_to_dict_layer_member():
    """Test layer membership export."""
    shape = VsdShape(shape_id=9)
    shape.layer_member = "0;1"
    d = _vsd_shape_to_dict(shape)
    assert d["cells"]["LayerMember"]["V"] == "0;1"


def test_vsd_shape_to_dict_no_text():
    """Test shape without text produces empty text_parts."""
    shape = VsdShape(shape_id=10)
    d = _vsd_shape_to_dict(shape)
    assert d["text"] == ""
    assert d["text_parts"] == []


def test_vsd_shape_to_dict_sub_shapes():
    """Test sub-shape recursion."""
    parent = VsdShape(shape_id=100, shape_type="Group")
    child = VsdShape(shape_id=101, text="child")
    parent.sub_shapes.append(child)
    d = _vsd_shape_to_dict(parent)
    assert len(d["sub_shapes"]) == 1
    assert d["sub_shapes"][0]["text"] == "child"


def test_parse_chunk_header_skip_nulls():
    """Test that chunk header parser skips null padding."""
    # Construct a minimal chunk header: 4 bytes type, 4 id, 4 list, 4 len, 2 level, 1 unknown
    hdr_data = b'\x00\x00\x00'  # 3 null bytes to skip
    hdr_data += struct.pack('<IIIIHHB', 0x48, 1, 0, 10, 2, 0x50, 0)[:19]
    # Need exactly 19 bytes for header after nulls
    hdr_data = b'\x00\x00' + struct.pack('<I', 0x48)  # type
    hdr_data += struct.pack('<I', 1)  # id
    hdr_data += struct.pack('<I', 0)  # list
    hdr_data += struct.pack('<I', 10)  # data length
    hdr_data += struct.pack('<H', 2)  # level
    hdr_data += struct.pack('<B', 0x50)  # unknown

    hdr, offset = _parse_chunk_header(hdr_data, 0)
    assert hdr is not None
    assert hdr.chunk_type == 0x48
    assert hdr.record_id == 1
    assert hdr.data_length == 10


def test_chunk_header_too_short():
    """Test chunk header with insufficient data."""
    hdr, offset = _parse_chunk_header(b'\x01\x02\x03', 0)
    assert hdr is None


def test_geom_row_defaults():
    """Test GeomRow default values."""
    row = GeomRow()
    assert row.row_type == ""
    assert row.x == 0.0
    assert row.points == []
    assert row.degree == 0


def test_spline_rows():
    """Test SplineStart/SplineKnot geometry rows."""
    shape = VsdShape(shape_id=11)
    geom = GeomSection()
    geom.rows.append(GeomRow(row_type="SplineStart", x=0.0, y=0.0, a=0.5, b=0.0, c=1.0, degree=3))
    geom.rows.append(GeomRow(row_type="SplineKnot", x=1.0, y=1.0, a=0.75))
    shape.geometry.append(geom)
    d = _vsd_shape_to_dict(shape)
    rows = d["geometry"][0]["rows"]
    assert rows[0]["type"] == "SplineStart"
    assert rows[0]["cells"]["C"]["V"] == "1.0"
    assert rows[1]["type"] == "SplineKnot"


def test_infinite_line_row():
    """Test InfiniteLine geometry row."""
    shape = VsdShape(shape_id=12)
    geom = GeomSection()
    geom.rows.append(GeomRow(row_type="InfiniteLine", x=0.0, y=0.0, a=10.0, b=10.0))
    shape.geometry.append(geom)
    d = _vsd_shape_to_dict(shape)
    rows = d["geometry"][0]["rows"]
    assert rows[0]["type"] == "InfiniteLine"
    assert rows[0]["cells"]["A"]["V"] == "10.0"
