"""Parser for .vsd (Visio binary) files using the OLE2/Compound Binary format.

Based on analysis of the libvisio C++ parser from LibreOffice.
Uses olefile to read the OLE2 structured storage.

The .vsd binary format stores data in streams within an OLE2 container.
The main stream is "VisioDocument" which contains a pointer-based tree
of records (chunks). Each chunk has a header with type, id, data length, etc.

Author: Daniel Nylander <daniel@danielnylander.se>
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path


# Record type constants (from libvisio VSDDocumentStructure.h)
VSD_FOREIGN_DATA      = 0x0C
VSD_OLE_LIST          = 0x0D
VSD_TEXT              = 0x0E
VSD_TRAILER_STREAM    = 0x14
VSD_PAGE              = 0x15
VSD_COLORS            = 0x16
VSD_FONT_LIST         = 0x18
VSD_FONT_IX           = 0x19
VSD_STYLES            = 0x1A
VSD_STENCILS          = 0x1D
VSD_STENCIL_PAGE      = 0x1E
VSD_OLE_DATA          = 0x1F
VSD_PAGES             = 0x27
VSD_NAME_LIST         = 0x2C
VSD_NAME              = 0x2D
VSD_NAME_LIST2        = 0x32
VSD_NAME2             = 0x33
VSD_NAMEIDX123        = 0x34
VSD_PAGE_SHEET        = 0x46
VSD_SHAPE_GROUP       = 0x47
VSD_SHAPE_SHAPE       = 0x48
VSD_SHAPE_GUIDE       = 0x4D
VSD_SHAPE_FOREIGN     = 0x4E
VSD_STYLE_SHEET       = 0x4A
VSD_SCRATCH_LIST      = 0x64
VSD_SHAPE_LIST        = 0x65
VSD_FIELD_LIST        = 0x66
VSD_PROP_LIST         = 0x68
VSD_CHAR_LIST         = 0x69
VSD_PARA_LIST         = 0x6A
VSD_TABS_DATA_LIST    = 0x6B
VSD_GEOM_LIST         = 0x6C
VSD_CUST_PROPS_LIST   = 0x6D
VSD_ACT_ID_LIST       = 0x6E
VSD_LAYER_LIST        = 0x6F
VSD_CTRL_LIST         = 0x70
VSD_C_PNTS_LIST       = 0x71
VSD_CONNECT_LIST      = 0x72
VSD_HYPER_LNK_LIST    = 0x73
VSD_SMART_TAG_LIST    = 0x76
VSD_SHAPE_ID          = 0x83
VSD_EVENT             = 0x84
VSD_LINE              = 0x85
VSD_FILL_AND_SHADOW   = 0x86
VSD_TEXT_BLOCK         = 0x87
VSD_TABS_DATA_1       = 0x88
VSD_GEOMETRY          = 0x89
VSD_MOVE_TO           = 0x8A
VSD_LINE_TO           = 0x8B
VSD_ARC_TO            = 0x8C
VSD_INFINITE_LINE     = 0x8D
VSD_ELLIPSE           = 0x8F
VSD_ELLIPTICAL_ARC_TO = 0x90
VSD_PAGE_PROPS        = 0x92
VSD_STYLE_PROPS       = 0x93
VSD_CHAR_IX           = 0x94
VSD_PARA_IX           = 0x95
VSD_FOREIGN_DATA_TYPE = 0x98
VSD_CONNECTION_POINTS = 0x99
VSD_XFORM_DATA        = 0x9B
VSD_TEXT_XFORM        = 0x9C
VSD_XFORM_1D          = 0x9D
VSD_SCRATCH           = 0x9E
VSD_PROTECTION        = 0xA0
VSD_TEXT_FIELD         = 0xA1
VSD_MISC              = 0xA4
VSD_SPLINE_START      = 0xA5
VSD_SPLINE_KNOT       = 0xA6
VSD_LAYER_MEMBERSHIP  = 0xA7
VSD_LAYER             = 0xA8
VSD_ACT_ID            = 0xA9
VSD_CONTROL           = 0xAA
VSD_USER_DEFINED_CELLS = 0xB4
VSD_CUSTOM_PROPS      = 0xB6
VSD_RULER_GRID        = 0xB7
VSD_CONN_PTS_ALT      = 0xBA
VSD_DOC_PROPS         = 0xBC
VSD_IMAGE             = 0xBD
VSD_GROUP             = 0xBE
VSD_LAYOUT            = 0xBF
VSD_PAGE_LAYOUT_IX    = 0xC0
VSD_POLYLINE_TO       = 0xC1
VSD_NURBS_TO          = 0xC3
VSD_HYPERLINK         = 0xC4
VSD_REVIEWER          = 0xC5
VSD_ANNOTATION        = 0xC6
VSD_SMART_TAG_DEF     = 0xC7
VSD_PRINT_PROPS       = 0xC8
VSD_NAMEIDX           = 0xC9
VSD_SHAPE_DATA        = 0xD1
VSD_FONTFACE          = 0xD7
VSD_FONTFACES         = 0xD8

_TRAILER_TYPES = {0x64, 0x65, 0x66, 0x69, 0x6A, 0x6B, 0x6F, 0x71,
                  0x92, 0xA9, 0xB4, 0xB6, 0xB9, 0xC7}
_LIST_TRAILER_TYPES = {0x71, 0x70, 0x6B, 0x6A, 0x69, 0x66, 0x65, 0x2C}
_NO_TRAILER_TYPES = {0x1F, 0xC9, 0x2D, 0xD1}


@dataclass
class XForm:
    pin_x: float = 0.0
    pin_y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    loc_pin_x: float = 0.0
    loc_pin_y: float = 0.0
    angle: float = 0.0
    flip_x: bool = False
    flip_y: bool = False


@dataclass
class TextXForm:
    """Text block transform — positions text independently of shape."""
    txt_pin_x: float = 0.0
    txt_pin_y: float = 0.0
    txt_width: float = 0.0
    txt_height: float = 0.0
    txt_loc_pin_x: float = 0.0
    txt_loc_pin_y: float = 0.0
    txt_angle: float = 0.0


@dataclass
class XForm1D:
    """1D connector endpoints."""
    begin_x: float = 0.0
    begin_y: float = 0.0
    end_x: float = 0.0
    end_y: float = 0.0


@dataclass
class GeomRow:
    row_type: str = ""
    x: float = 0.0
    y: float = 0.0
    a: float = 0.0
    b: float = 0.0
    c: float = 0.0
    d: float = 0.0
    # For NURBS/Polyline: extra data
    knot_last: float = 0.0
    degree: int = 0
    x_type: int = 0
    y_type: int = 0
    points: list = field(default_factory=list)  # list of (x, y) or (x, y, knot) tuples


@dataclass
class GeomSection:
    no_fill: bool = False
    no_line: bool = False
    no_show: bool = False
    rows: list = field(default_factory=list)


@dataclass
class CharFormat:
    char_count: int = 0
    font_id: int = 0
    color_r: int = 0
    color_g: int = 0
    color_b: int = 0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font_size: float = 12.0


@dataclass
class ParaFormat:
    char_count: int = 0
    indent_first: float = 0.0
    indent_left: float = 0.0
    indent_right: float = 0.0
    spacing_line: float = -1.2  # negative = multiplier
    spacing_before: float = 0.0
    spacing_after: float = 0.0
    horiz_align: int = 0  # 0=left, 1=center, 2=right, 3=justify
    bullet: int = 0
    bullet_str: str = ""


@dataclass
class ForeignData:
    """Embedded image/OLE data."""
    data_type: str = ""  # "img", "ole", "metafile"
    img_format: str = ""  # "png", "jpg", "bmp", "emf", "wmf"
    data: bytes = b""


@dataclass
class ConnectionPoint:
    x: float = 0.0
    y: float = 0.0


@dataclass
class VsdShape:
    shape_id: int = 0
    shape_type: str = "Shape"
    parent: int = 0
    master_page: int = -1
    master_shape: int = -1
    xform: XForm = field(default_factory=XForm)
    text_xform: TextXForm | None = None
    xform_1d: XForm1D | None = None
    text: str = ""
    geometry: list = field(default_factory=list)
    char_formats: list = field(default_factory=list)
    para_formats: list = field(default_factory=list)
    line_weight: float = 0.01
    line_color: str = "#000000"
    line_pattern: int = 1
    fill_foreground: str = ""
    fill_background: str = ""
    fill_pattern: int = 1
    shadow_offset_x: float = 0.0
    shadow_offset_y: float = 0.0
    shadow_color: str = ""
    shadow_pattern: int = 0
    children: list = field(default_factory=list)
    sub_shapes: list = field(default_factory=list)
    layer_member: str = ""
    foreign_data: ForeignData | None = None
    connection_points: list = field(default_factory=list)
    text_block_bg: str = ""
    text_block_margin_left: float = 0.0
    text_block_margin_right: float = 0.0
    text_block_margin_top: float = 0.0
    text_block_margin_bottom: float = 0.0
    text_block_valign: int = 0  # 0=top, 1=middle, 2=bottom


@dataclass
class VsdPage:
    page_id: int = 0
    name: str = ""
    width: float = 8.5
    height: float = 11.0
    shapes: list = field(default_factory=list)
    background: bool = False


@dataclass
class VsdDocument:
    pages: list = field(default_factory=list)
    colors: list = field(default_factory=list)
    fonts: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)  # id -> name string
    stencil_pages: list = field(default_factory=list)  # master shapes


# Binary reading helpers
def _read_u8(data, offset):
    if offset >= len(data): return 0, offset + 1
    return data[offset], offset + 1

def _read_u16(data, offset):
    if offset + 2 > len(data): return 0, offset + 2
    return struct.unpack_from('<H', data, offset)[0], offset + 2

def _read_u32(data, offset):
    if offset + 4 > len(data): return 0, offset + 4
    return struct.unpack_from('<I', data, offset)[0], offset + 4

def _read_s32(data, offset):
    if offset + 4 > len(data): return 0, offset + 4
    return struct.unpack_from('<i', data, offset)[0], offset + 4

def _read_double(data, offset):
    if offset + 8 > len(data): return 0.0, offset + 8
    return struct.unpack_from('<d', data, offset)[0], offset + 8

def _read_s16(data, offset):
    if offset + 2 > len(data): return 0, offset + 2
    return struct.unpack_from('<h', data, offset)[0], offset + 2


@dataclass
class ChunkHeader:
    chunk_type: int = 0
    record_id: int = 0
    list_flag: int = 0
    data_length: int = 0
    level: int = 0
    unknown: int = 0
    trailer: int = 0


def _parse_chunk_header(data, offset):
    while offset < len(data) and data[offset] == 0:
        offset += 1
    if offset + 19 > len(data):
        return None, offset
    hdr = ChunkHeader()
    hdr.chunk_type, offset = _read_u32(data, offset)
    hdr.record_id, offset = _read_u32(data, offset)
    hdr.list_flag, offset = _read_u32(data, offset)
    hdr.trailer = 0
    if hdr.list_flag != 0 or hdr.chunk_type in _LIST_TRAILER_TYPES:
        hdr.trailer += 8
    hdr.data_length, offset = _read_u32(data, offset)
    hdr.level, offset = _read_u16(data, offset)
    hdr.unknown, offset = _read_u8(data, offset)
    if (hdr.list_flag != 0 or
        (hdr.level == 2 and hdr.unknown == 0x55) or
        (hdr.level == 2 and hdr.unknown == 0x54 and hdr.chunk_type == 0xAA) or
        (hdr.level == 3 and hdr.unknown not in (0x50, 0x54))):
        hdr.trailer += 4
    for tt in _TRAILER_TYPES:
        if hdr.chunk_type == tt and hdr.trailer not in (12, 4):
            hdr.trailer += 4
            break
    if hdr.chunk_type in _NO_TRAILER_TYPES:
        hdr.trailer = 0
    return hdr, offset


@dataclass
class Pointer:
    ptr_type: int = 0
    offset: int = 0
    length: int = 0
    fmt: int = 0


def _read_pointer(data, offset):
    ptr = Pointer()
    ptr.ptr_type, offset = _read_u32(data, offset)
    offset += 4
    ptr.offset, offset = _read_u32(data, offset)
    ptr.length, offset = _read_u32(data, offset)
    ptr.fmt, offset = _read_u16(data, offset)
    return ptr, offset


class VsdParser:
    """Parse a .vsd binary Visio file."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.doc = VsdDocument()
        self._current_page = None
        self._current_shape = None
        self._current_geom = None
        self._shape_stack: list[VsdShape] = []  # for nested groups
        self._current_level = 0
        self._page_is_background = False

    def parse(self) -> VsdDocument:
        try:
            import olefile
        except ImportError:
            raise ImportError(
                "olefile is required for .vsd parsing. "
                "Install it with: pip install olefile"
            )
        if not olefile.isOleFile(self.data):
            raise ValueError("Not a valid OLE2/Compound Binary file")
        ole = olefile.OleFileIO(BytesIO(self.data))
        try:
            self._parse_ole(ole)
        finally:
            ole.close()
        return self.doc

    def _parse_ole(self, ole):
        if ole.exists("VisioDocument"):
            stream_data = ole.openstream("VisioDocument").read()
            self._parse_visio_document(stream_data)

    def _parse_visio_document(self, data):
        if len(data) < 0x36:
            return
        # Try pointer-based parsing first
        try:
            trailer_ptr, _ = _read_pointer(data, 0x24)
            if 0 < trailer_ptr.offset < len(data):
                compressed = (trailer_ptr.fmt & 2) == 2
                if compressed:
                    trailer_data = self._decompress_stream(data, trailer_ptr.offset, trailer_ptr.length)
                else:
                    end = min(trailer_ptr.offset + trailer_ptr.length, len(data))
                    trailer_data = data[trailer_ptr.offset:end]
                if trailer_data and len(trailer_data) > 8:
                    self._parse_pointer_tree(data, trailer_data, compressed)
                    if self.doc.pages:
                        return
        except Exception:
            pass
        # Fallback: linear chunk scanning
        self._parse_chunks_linear(data, 0x36)

    def _decompress_stream(self, data, offset, length):
        import zlib
        end = min(offset + length, len(data))
        compressed_data = data[offset:end]
        if len(compressed_data) < 4:
            return compressed_data
        try:
            return zlib.decompress(compressed_data[4:], -15)
        except zlib.error:
            try:
                return zlib.decompress(compressed_data[4:])
            except zlib.error:
                return compressed_data

    def _parse_pointer_tree(self, full_data, trailer_data, compressed):
        shift = 4 if compressed else 0
        if len(trailer_data) < shift + 4:
            return
        offset_val, _ = _read_u32(trailer_data, shift)
        seek_pos = offset_val + shift - 4
        if seek_pos + 12 > len(trailer_data):
            return
        list_size, pos = _read_u32(trailer_data, seek_pos)
        pointer_count, pos = _read_s32(trailer_data, pos)
        pos += 4

        # Separate pointers by type for ordered processing
        font_faces = {}
        name_lists = {}
        name_idx = {}
        other_ptrs = {}

        for i in range(max(0, pointer_count)):
            if pos + 18 > len(trailer_data):
                break
            ptr, pos = _read_pointer(trailer_data, pos)
            if ptr.ptr_type == 0:
                continue
            if ptr.ptr_type == VSD_FONTFACES:
                font_faces[i] = ptr
            elif ptr.ptr_type == VSD_NAME_LIST2:
                name_lists[i] = ptr
            elif ptr.ptr_type in (VSD_NAMEIDX, VSD_NAMEIDX123):
                name_idx[i] = ptr
            else:
                other_ptrs[i] = ptr

        # Read ordering list
        pointer_order = []
        if list_size > 1:
            for _ in range(list_size):
                if pos + 4 <= len(trailer_data):
                    val, pos = _read_u32(trailer_data, pos)
                    pointer_order.append(val)

        # Process name lists first, then fonts, then others
        for _, ptr in name_lists.items():
            self._handle_stream_pointer(full_data, ptr)
        for _, ptr in name_idx.items():
            self._handle_stream_pointer(full_data, ptr)
        for _, ptr in font_faces.items():
            self._handle_stream_pointer(full_data, ptr)

        if pointer_order:
            for idx in pointer_order:
                if idx in other_ptrs:
                    self._handle_stream_pointer(full_data, other_ptrs.pop(idx))
        for _, ptr in other_ptrs.items():
            self._handle_stream_pointer(full_data, ptr)

    def _handle_stream_pointer(self, full_data, ptr):
        if ptr.offset >= len(full_data):
            return
        compressed = (ptr.fmt & 2) == 2
        if compressed:
            stream_data = self._decompress_stream(full_data, ptr.offset, ptr.length)
        else:
            end = min(ptr.offset + ptr.length, len(full_data))
            stream_data = full_data[ptr.offset:end]
        if not stream_data:
            return
        fmt_high = ptr.fmt >> 4

        if ptr.ptr_type == VSD_PAGES:
            self._parse_pages_stream(full_data, stream_data, compressed)
        elif ptr.ptr_type == VSD_PAGE:
            self._flush_shape()
            page = VsdPage(page_id=ptr.record_id if hasattr(ptr, 'record_id') else 0)
            # Check if background page (Format bit 0 == 0 means background)
            if not (ptr.fmt & 0x1):
                page.background = True
                self._page_is_background = True
            else:
                self._page_is_background = False
            self._current_page = page
            self.doc.pages.append(page)
            if fmt_high in (0xD, 0xC, 0x8):
                self._parse_chunks_stream(stream_data)
            else:
                self._parse_blob(stream_data, compressed)
                if fmt_high == 0x5:
                    self._parse_sub_pointers(full_data, stream_data, compressed)
            self._flush_shape()
        elif ptr.ptr_type == VSD_STENCILS:
            self._parse_stencils_stream(full_data, stream_data, compressed)
        elif ptr.ptr_type == VSD_STENCIL_PAGE:
            self._flush_shape()
            page = VsdPage(page_id=0)
            self._current_page = page
            self.doc.stencil_pages.append(page)
            if fmt_high in (0xD, 0xC, 0x8):
                self._parse_chunks_stream(stream_data)
            else:
                self._parse_blob(stream_data, compressed)
            self._flush_shape()
            self._current_page = None
        elif ptr.ptr_type == VSD_COLORS:
            self._parse_colors_stream(stream_data)
        elif fmt_high in (0xD, 0xC, 0x8):
            self._parse_chunks_stream(stream_data)
        elif fmt_high in (0x4, 0x5, 0x0):
            self._parse_blob(stream_data, compressed)

    def _parse_colors_stream(self, stream_data):
        """Parse the color table."""
        offset = 0
        while offset + 4 <= len(stream_data):
            r, offset = _read_u8(stream_data, offset)
            g, offset = _read_u8(stream_data, offset)
            b, offset = _read_u8(stream_data, offset)
            _a, offset = _read_u8(stream_data, offset)
            self.doc.colors.append(f"#{r:02X}{g:02X}{b:02X}")

    def _parse_stencils_stream(self, full_data, stream_data, compressed):
        """Parse stencils container for master shapes."""
        shift = 4 if compressed else 0
        if len(stream_data) < shift + 4:
            return
        offset_val, _ = _read_u32(stream_data, shift)
        seek_pos = offset_val + shift - 4
        if seek_pos + 12 > len(stream_data):
            return
        list_size, pos = _read_u32(stream_data, seek_pos)
        pointer_count, pos = _read_s32(stream_data, pos)
        pos += 4
        for _ in range(max(0, pointer_count)):
            if pos + 18 > len(stream_data):
                break
            ptr, pos = _read_pointer(stream_data, pos)
            if ptr.ptr_type == VSD_STENCIL_PAGE:
                self._handle_stream_pointer(full_data, ptr)

    def _parse_pages_stream(self, full_data, stream_data, compressed):
        shift = 4 if compressed else 0
        if len(stream_data) < shift + 4:
            return
        offset_val, _ = _read_u32(stream_data, shift)
        seek_pos = offset_val + shift - 4
        if seek_pos + 12 > len(stream_data):
            return
        list_size, pos = _read_u32(stream_data, seek_pos)
        pointer_count, pos = _read_s32(stream_data, pos)
        pos += 4
        for _ in range(max(0, pointer_count)):
            if pos + 18 > len(stream_data):
                break
            ptr, pos = _read_pointer(stream_data, pos)
            if ptr.ptr_type == VSD_PAGE:
                self._handle_page_pointer(full_data, ptr)

    def _handle_page_pointer(self, full_data, ptr):
        if ptr.offset >= len(full_data):
            return
        compressed = (ptr.fmt & 2) == 2
        if compressed:
            stream_data = self._decompress_stream(full_data, ptr.offset, ptr.length)
        else:
            end = min(ptr.offset + ptr.length, len(full_data))
            stream_data = full_data[ptr.offset:end]
        if not stream_data:
            return
        self._flush_shape()
        page = VsdPage(page_id=ptr.ptr_type)
        # Background page detection
        if not (ptr.fmt & 0x1):
            page.background = True
            self._page_is_background = True
        else:
            self._page_is_background = False
        self._current_page = page
        self.doc.pages.append(page)
        fmt_high = ptr.fmt >> 4
        if fmt_high in (0xD, 0xC, 0x8):
            self._parse_chunks_stream(stream_data)
        else:
            self._parse_blob(stream_data, compressed)
            if fmt_high == 0x5:
                self._parse_sub_pointers(full_data, stream_data, compressed)
        self._flush_shape()

    def _parse_sub_pointers(self, full_data, stream_data, compressed):
        shift = 4 if compressed else 0
        if len(stream_data) < shift + 4:
            return
        offset_val, _ = _read_u32(stream_data, shift)
        seek_pos = offset_val + shift - 4
        if seek_pos + 12 > len(stream_data):
            return
        list_size, pos = _read_u32(stream_data, seek_pos)
        pointer_count, pos = _read_s32(stream_data, pos)
        pos += 4
        for _ in range(max(0, pointer_count)):
            if pos + 18 > len(stream_data):
                break
            ptr, pos = _read_pointer(stream_data, pos)
            if ptr.ptr_type != 0:
                self._handle_stream_pointer(full_data, ptr)

    def _parse_blob(self, stream_data, compressed):
        shift = 4 if compressed else 0
        if len(stream_data) <= shift:
            return
        self._parse_chunks_stream(stream_data[shift:])

    def _parse_chunks_linear(self, data, start_offset):
        offset = start_offset
        while offset < len(data) - 19:
            hdr, offset = _parse_chunk_header(data, offset)
            if hdr is None:
                break
            end_pos = offset + hdr.data_length + hdr.trailer
            if end_pos > len(data):
                break
            chunk_data = data[offset:offset + hdr.data_length]
            self._handle_chunk(hdr, chunk_data)
            offset = end_pos

    def _parse_chunks_stream(self, data):
        offset = 0
        while offset < len(data) - 19:
            hdr, offset = _parse_chunk_header(data, offset)
            if hdr is None:
                break
            end_pos = offset + hdr.data_length + hdr.trailer
            if end_pos > len(data):
                end_pos = min(offset + hdr.data_length, len(data))
            chunk_data = data[offset:min(offset + hdr.data_length, len(data))]
            self._handle_chunk(hdr, chunk_data)
            offset = max(offset + 1, end_pos)

    def _flush_shape(self):
        if self._current_shape and self._current_page:
            self._current_page.shapes.append(self._current_shape)
            self._current_shape = None
        self._current_geom = None

    def _handle_chunk(self, hdr, data):
        ct = hdr.chunk_type
        if ct in (VSD_SHAPE_GROUP, VSD_SHAPE_SHAPE, VSD_SHAPE_FOREIGN):
            self._flush_shape()
            shape = VsdShape(shape_id=hdr.record_id)
            if ct == VSD_SHAPE_GROUP:
                shape.shape_type = "Group"
            elif ct == VSD_SHAPE_FOREIGN:
                shape.shape_type = "Foreign"
            self._read_shape_header(data, shape)
            self._current_shape = shape
            self._current_geom = None
        elif ct == VSD_XFORM_DATA:
            self._read_xform_data(data)
        elif ct == VSD_TEXT_XFORM:
            self._read_text_xform(data)
        elif ct == VSD_XFORM_1D:
            self._read_xform_1d(data)
        elif ct == VSD_TEXT:
            self._read_text(data)
        elif ct == VSD_TEXT_BLOCK:
            self._read_text_block(data)
        elif ct == VSD_PAGE_PROPS:
            self._read_page_props(data)
        elif ct == VSD_GEOMETRY:
            self._read_geometry(data)
        elif ct == VSD_MOVE_TO:
            self._read_move_to(data)
        elif ct == VSD_LINE_TO:
            self._read_line_to(data)
        elif ct == VSD_ARC_TO:
            self._read_arc_to(data)
        elif ct == VSD_ELLIPSE:
            self._read_ellipse(data)
        elif ct == VSD_ELLIPTICAL_ARC_TO:
            self._read_elliptical_arc_to(data)
        elif ct == VSD_NURBS_TO:
            self._read_nurbs_to(data)
        elif ct == VSD_POLYLINE_TO:
            self._read_polyline_to(data)
        elif ct == VSD_SPLINE_START:
            self._read_spline_start(data)
        elif ct == VSD_SPLINE_KNOT:
            self._read_spline_knot(data)
        elif ct == VSD_INFINITE_LINE:
            self._read_infinite_line(data)
        elif ct == VSD_LINE:
            self._read_line_fmt(data)
        elif ct == VSD_FILL_AND_SHADOW:
            self._read_fill(data)
        elif ct == VSD_CHAR_IX:
            self._read_char_ix(data)
        elif ct == VSD_PARA_IX:
            self._read_para_ix(data)
        elif ct == VSD_LAYER_MEMBERSHIP:
            self._read_layer_membership(data)
        elif ct == VSD_CONNECTION_POINTS:
            self._read_connection_points(data)
        elif ct == VSD_FOREIGN_DATA_TYPE:
            self._read_foreign_data_type(data)
        elif ct == VSD_FOREIGN_DATA:
            self._read_foreign_data(data)
        elif ct == VSD_FONTFACE:
            self._read_fontface(data, hdr.record_id)
        elif ct == VSD_NAME:
            self._read_name(data, hdr.record_id)
        elif ct == VSD_NAME2:
            self._read_name(data, hdr.record_id)

    def _read_shape_header(self, data, shape):
        if len(data) < 4: return
        offset = 0
        shape.parent, offset = _read_u32(data, offset)
        if offset + 4 <= len(data):
            shape.master_page, offset = _read_u32(data, offset)
        if offset + 4 <= len(data):
            shape.master_shape, offset = _read_u32(data, offset)

    def _read_xform_data(self, data):
        if not self._current_shape: return
        xf = self._current_shape.xform
        offset = 0
        try:
            offset += 1; xf.pin_x, offset = _read_double(data, offset)
            offset += 1; xf.pin_y, offset = _read_double(data, offset)
            offset += 1; xf.width, offset = _read_double(data, offset)
            offset += 1; xf.height, offset = _read_double(data, offset)
            offset += 1; xf.loc_pin_x, offset = _read_double(data, offset)
            offset += 1; xf.loc_pin_y, offset = _read_double(data, offset)
            offset += 1; xf.angle, offset = _read_double(data, offset)
            if offset < len(data): xf.flip_x = data[offset] != 0; offset += 1
            if offset < len(data): xf.flip_y = data[offset] != 0
        except (struct.error, IndexError):
            pass

    def _read_text_xform(self, data):
        """Parse text block transform (TxtXForm)."""
        if not self._current_shape: return
        txf = TextXForm()
        offset = 0
        try:
            offset += 1; txf.txt_pin_x, offset = _read_double(data, offset)
            offset += 1; txf.txt_pin_y, offset = _read_double(data, offset)
            offset += 1; txf.txt_width, offset = _read_double(data, offset)
            offset += 1; txf.txt_height, offset = _read_double(data, offset)
            offset += 1; txf.txt_loc_pin_x, offset = _read_double(data, offset)
            offset += 1; txf.txt_loc_pin_y, offset = _read_double(data, offset)
            offset += 1; txf.txt_angle, offset = _read_double(data, offset)
        except (struct.error, IndexError):
            pass
        self._current_shape.text_xform = txf

    def _read_xform_1d(self, data):
        """Parse 1D connector endpoint transform."""
        if not self._current_shape: return
        xf1d = XForm1D()
        offset = 0
        try:
            offset += 1; xf1d.begin_x, offset = _read_double(data, offset)
            offset += 1; xf1d.begin_y, offset = _read_double(data, offset)
            offset += 1; xf1d.end_x, offset = _read_double(data, offset)
            offset += 1; xf1d.end_y, offset = _read_double(data, offset)
        except (struct.error, IndexError):
            pass
        self._current_shape.xform_1d = xf1d

    def _read_text(self, data):
        if not self._current_shape or len(data) < 8: return
        text_data = data[8:]
        if not text_data: return
        # Try UTF-16LE first (most common in .vsd)
        try:
            text = text_data.decode('utf-16-le', errors='replace').rstrip('\x00')
            # Check if it looks like valid text (not garbled)
            if text and not all(c == '\ufffd' for c in text):
                self._current_shape.text = text
                return
        except (UnicodeDecodeError, ValueError):
            pass
        # Try UTF-8
        try:
            text = text_data.decode('utf-8', errors='replace').rstrip('\x00')
            if text:
                self._current_shape.text = text
                return
        except (UnicodeDecodeError, ValueError):
            pass
        # Fallback to latin-1
        try:
            self._current_shape.text = text_data.decode('latin-1', errors='replace').rstrip('\x00')
        except Exception:
            pass

    def _read_text_block(self, data):
        """Parse text block format (margins, vertical alignment, background)."""
        if not self._current_shape or len(data) < 4: return
        offset = 0
        try:
            # Text block margins (left, right, top, bottom)
            offset += 1; self._current_shape.text_block_margin_left, offset = _read_double(data, offset)
            offset += 1; self._current_shape.text_block_margin_right, offset = _read_double(data, offset)
            offset += 1; self._current_shape.text_block_margin_top, offset = _read_double(data, offset)
            offset += 1; self._current_shape.text_block_margin_bottom, offset = _read_double(data, offset)
            # Vertical alignment
            offset += 1
            if offset < len(data):
                self._current_shape.text_block_valign = data[offset]
                offset += 1
            # Background color
            offset += 1
            if offset + 3 <= len(data):
                r, offset = _read_u8(data, offset)
                g, offset = _read_u8(data, offset)
                b, offset = _read_u8(data, offset)
                if r != 0 or g != 0 or b != 0:
                    self._current_shape.text_block_bg = f"#{r:02X}{g:02X}{b:02X}"
        except (struct.error, IndexError):
            pass

    def _read_page_props(self, data):
        if not self._current_page: return
        offset = 0
        try:
            offset += 1; self._current_page.width, offset = _read_double(data, offset)
            offset += 1; self._current_page.height, offset = _read_double(data, offset)
        except (struct.error, IndexError):
            pass

    def _read_geometry(self, data):
        if not self._current_shape: return
        geom = GeomSection()
        if len(data) >= 1:
            flags = data[0]
            geom.no_fill = bool(flags & 1)
            geom.no_line = bool(flags & 2)
            geom.no_show = bool(flags & 4)
        self._current_geom = geom
        self._current_shape.geometry.append(geom)

    def _ensure_geom(self):
        if not self._current_shape: return False
        if self._current_geom is None:
            self._current_geom = GeomSection()
            self._current_shape.geometry.append(self._current_geom)
        return True

    def _read_move_to(self, data):
        if not self._ensure_geom(): return
        row = GeomRow(row_type="MoveTo")
        offset = 1
        try:
            row.x, offset = _read_double(data, offset)
            offset += 1; row.y, offset = _read_double(data, offset)
        except (struct.error, IndexError): pass
        self._current_geom.rows.append(row)

    def _read_line_to(self, data):
        if not self._ensure_geom(): return
        row = GeomRow(row_type="LineTo")
        offset = 1
        try:
            row.x, offset = _read_double(data, offset)
            offset += 1; row.y, offset = _read_double(data, offset)
        except (struct.error, IndexError): pass
        self._current_geom.rows.append(row)

    def _read_arc_to(self, data):
        if not self._ensure_geom(): return
        row = GeomRow(row_type="ArcTo")
        offset = 1
        try:
            row.x, offset = _read_double(data, offset)
            offset += 1; row.y, offset = _read_double(data, offset)
            offset += 1; row.a, offset = _read_double(data, offset)
        except (struct.error, IndexError): pass
        self._current_geom.rows.append(row)

    def _read_ellipse(self, data):
        if not self._ensure_geom(): return
        row = GeomRow(row_type="Ellipse")
        offset = 1
        try:
            row.x, offset = _read_double(data, offset)
            offset += 1; row.y, offset = _read_double(data, offset)
            offset += 1; row.a, offset = _read_double(data, offset)
            offset += 1; row.b, offset = _read_double(data, offset)
            offset += 1; row.c, offset = _read_double(data, offset)
            offset += 1; row.d, offset = _read_double(data, offset)
        except (struct.error, IndexError): pass
        self._current_geom.rows.append(row)

    def _read_elliptical_arc_to(self, data):
        if not self._ensure_geom(): return
        row = GeomRow(row_type="EllipticalArcTo")
        offset = 1
        try:
            row.x, offset = _read_double(data, offset)
            offset += 1; row.y, offset = _read_double(data, offset)
            offset += 1; row.a, offset = _read_double(data, offset)
            offset += 1; row.b, offset = _read_double(data, offset)
            offset += 1; row.c, offset = _read_double(data, offset)
            offset += 1; row.d, offset = _read_double(data, offset)
        except (struct.error, IndexError): pass
        self._current_geom.rows.append(row)

    def _read_nurbs_to(self, data):
        """Parse NURBSTo geometry row.

        Format: x, y, knotLast, degree, xType, yType, then
        alternating knot/weight/x/y data.
        """
        if not self._ensure_geom(): return
        row = GeomRow(row_type="NURBSTo")
        offset = 1
        try:
            row.x, offset = _read_double(data, offset)
            offset += 1; row.y, offset = _read_double(data, offset)
            offset += 1; row.knot_last, offset = _read_double(data, offset)
            offset += 1; row.degree, offset = _read_u16(data, offset)
            row.x_type, offset = _read_u8(data, offset)
            row.y_type, offset = _read_u8(data, offset)
            # Read control points: knot, weight, x, y
            points = []
            while offset + 32 <= len(data):
                knot, offset = _read_double(data, offset)
                weight, offset = _read_double(data, offset)
                px, offset = _read_double(data, offset)
                py, offset = _read_double(data, offset)
                points.append((px, py, knot, weight))
            row.points = points
        except (struct.error, IndexError):
            pass
        self._current_geom.rows.append(row)

    def _read_polyline_to(self, data):
        """Parse PolylineTo geometry row.

        Format: x, y, then xType, yType, then point pairs.
        """
        if not self._ensure_geom(): return
        row = GeomRow(row_type="PolylineTo")
        offset = 1
        try:
            row.x, offset = _read_double(data, offset)
            offset += 1; row.y, offset = _read_double(data, offset)
            offset += 1
            row.x_type, offset = _read_u8(data, offset)
            row.y_type, offset = _read_u8(data, offset)
            # Read points
            points = []
            while offset + 16 <= len(data):
                px, offset = _read_double(data, offset)
                py, offset = _read_double(data, offset)
                points.append((px, py))
            row.points = points
        except (struct.error, IndexError):
            pass
        self._current_geom.rows.append(row)

    def _read_spline_start(self, data):
        """Parse SplineStart geometry row."""
        if not self._ensure_geom(): return
        row = GeomRow(row_type="SplineStart")
        offset = 1
        try:
            row.x, offset = _read_double(data, offset)
            offset += 1; row.y, offset = _read_double(data, offset)
            offset += 1; row.a, offset = _read_double(data, offset)  # second knot
            offset += 1; row.b, offset = _read_double(data, offset)  # first knot
            offset += 1; row.c, offset = _read_double(data, offset)  # last knot
            offset += 1
            if offset < len(data):
                row.degree = data[offset]
        except (struct.error, IndexError):
            pass
        self._current_geom.rows.append(row)

    def _read_spline_knot(self, data):
        """Parse SplineKnot geometry row."""
        if not self._ensure_geom(): return
        row = GeomRow(row_type="SplineKnot")
        offset = 1
        try:
            row.x, offset = _read_double(data, offset)
            offset += 1; row.y, offset = _read_double(data, offset)
            offset += 1; row.a, offset = _read_double(data, offset)  # knot value
        except (struct.error, IndexError):
            pass
        self._current_geom.rows.append(row)

    def _read_infinite_line(self, data):
        """Parse InfiniteLine geometry row (two points defining a line)."""
        if not self._ensure_geom(): return
        row = GeomRow(row_type="InfiniteLine")
        offset = 1
        try:
            row.x, offset = _read_double(data, offset)
            offset += 1; row.y, offset = _read_double(data, offset)
            offset += 1; row.a, offset = _read_double(data, offset)
            offset += 1; row.b, offset = _read_double(data, offset)
        except (struct.error, IndexError):
            pass
        self._current_geom.rows.append(row)

    def _read_line_fmt(self, data):
        if not self._current_shape: return
        offset = 0
        try:
            offset += 1
            self._current_shape.line_weight, offset = _read_double(data, offset)
            offset += 1
            r, offset = _read_u8(data, offset)
            g, offset = _read_u8(data, offset)
            b, offset = _read_u8(data, offset)
            offset += 1
            self._current_shape.line_color = f"#{r:02X}{g:02X}{b:02X}"
            if offset + 1 <= len(data):
                self._current_shape.line_pattern = data[offset]
        except (struct.error, IndexError): pass

    def _read_fill(self, data):
        if not self._current_shape: return
        offset = 0
        try:
            offset += 1
            r, offset = _read_u8(data, offset)
            g, offset = _read_u8(data, offset)
            b, offset = _read_u8(data, offset)
            offset += 1
            self._current_shape.fill_foreground = f"#{r:02X}{g:02X}{b:02X}"
            offset += 1
            r, offset = _read_u8(data, offset)
            g, offset = _read_u8(data, offset)
            b, offset = _read_u8(data, offset)
            offset += 1
            self._current_shape.fill_background = f"#{r:02X}{g:02X}{b:02X}"
            if offset + 1 <= len(data):
                self._current_shape.fill_pattern = data[offset]
                offset += 1
            # Shadow data follows fill
            if offset + 12 <= len(data):
                offset += 1  # shadow type byte
                r, offset = _read_u8(data, offset)
                g, offset = _read_u8(data, offset)
                b, offset = _read_u8(data, offset)
                self._current_shape.shadow_color = f"#{r:02X}{g:02X}{b:02X}"
                offset += 1
                self._current_shape.shadow_pattern = data[offset] if offset < len(data) else 0
                offset += 1
                if offset + 16 <= len(data):
                    offset += 1
                    self._current_shape.shadow_offset_x, offset = _read_double(data, offset)
                    offset += 1
                    self._current_shape.shadow_offset_y, offset = _read_double(data, offset)
        except (struct.error, IndexError): pass

    def _read_char_ix(self, data):
        if not self._current_shape or len(data) < 12: return
        fmt = CharFormat()
        offset = 0
        try:
            fmt.char_count, offset = _read_u32(data, offset)
            fmt.font_id, offset = _read_u16(data, offset)
            offset += 1
            fmt.color_r, offset = _read_u8(data, offset)
            fmt.color_g, offset = _read_u8(data, offset)
            fmt.color_b, offset = _read_u8(data, offset)
            offset += 1  # alpha
            font_mod, offset = _read_u8(data, offset)
            fmt.bold = bool(font_mod & 1)
            fmt.italic = bool(font_mod & 2)
            fmt.underline = bool(font_mod & 4)
            offset += 4  # skip font mods + scale
            fmt.font_size, offset = _read_double(data, offset)
        except (struct.error, IndexError): pass
        self._current_shape.char_formats.append(fmt)

    def _read_para_ix(self, data):
        """Parse paragraph format."""
        if not self._current_shape or len(data) < 8: return
        pf = ParaFormat()
        offset = 0
        try:
            pf.char_count, offset = _read_u32(data, offset)
            offset += 1; pf.indent_first, offset = _read_double(data, offset)
            offset += 1; pf.indent_left, offset = _read_double(data, offset)
            offset += 1; pf.indent_right, offset = _read_double(data, offset)
            offset += 1; pf.spacing_line, offset = _read_double(data, offset)
            offset += 1; pf.spacing_before, offset = _read_double(data, offset)
            offset += 1; pf.spacing_after, offset = _read_double(data, offset)
            offset += 1
            if offset < len(data):
                pf.horiz_align = data[offset]
                offset += 1
            offset += 1
            if offset < len(data):
                pf.bullet = data[offset]
        except (struct.error, IndexError):
            pass
        self._current_shape.para_formats.append(pf)

    def _read_layer_membership(self, data):
        """Parse layer membership string."""
        if not self._current_shape or len(data) < 2: return
        try:
            text = data.decode('utf-16-le', errors='replace').rstrip('\x00').strip()
            self._current_shape.layer_member = text
        except Exception:
            pass

    def _read_connection_points(self, data):
        """Parse connection point data."""
        if not self._current_shape or len(data) < 16: return
        offset = 0
        try:
            offset += 1
            x, offset = _read_double(data, offset)
            offset += 1
            y, offset = _read_double(data, offset)
            self._current_shape.connection_points.append(ConnectionPoint(x=x, y=y))
        except (struct.error, IndexError):
            pass

    def _read_foreign_data_type(self, data):
        """Parse foreign data type (image format info)."""
        if not self._current_shape or len(data) < 4: return
        if self._current_shape.foreign_data is None:
            self._current_shape.foreign_data = ForeignData()
        offset = 0
        try:
            img_off_x, offset = _read_double(data, offset) if len(data) >= 8 else (0, 0)
            img_off_y, offset = _read_double(data, offset) if offset + 8 <= len(data) else (0, offset)
            img_w, offset = _read_double(data, offset) if offset + 8 <= len(data) else (0, offset)
            img_h, offset = _read_double(data, offset) if offset + 8 <= len(data) else (0, offset)
            if offset + 2 <= len(data):
                img_type, offset = _read_u16(data, offset)
                fmt_map = {0: "emf", 1: "wmf", 2: "bmp", 3: "ole",
                           4: "jpg", 5: "png", 6: "gif", 7: "tiff"}
                if img_type in fmt_map:
                    self._current_shape.foreign_data.img_format = fmt_map[img_type]
                    self._current_shape.foreign_data.data_type = "ole" if img_type == 3 else "img"
        except (struct.error, IndexError):
            pass

    def _read_foreign_data(self, data):
        """Parse foreign data (actual image bytes)."""
        if not self._current_shape: return
        if self._current_shape.foreign_data is None:
            self._current_shape.foreign_data = ForeignData()
        self._current_shape.foreign_data.data = data

    def _read_fontface(self, data, record_id):
        """Parse font face name."""
        if len(data) < 2: return
        try:
            # Skip initial bytes, then read UTF-16LE name
            name = data.decode('utf-16-le', errors='replace').rstrip('\x00').strip()
            if name:
                self.doc.fonts[record_id] = name
        except Exception:
            pass

    def _read_name(self, data, record_id):
        """Parse name string (page names, etc)."""
        if len(data) < 2: return
        try:
            name = data.decode('utf-16-le', errors='replace').rstrip('\x00').strip()
            if name:
                self.doc.names[record_id] = name
        except Exception:
            pass


def _vsd_shape_to_dict(shape):
    """Convert VsdShape to dict matching _parse_single_shape format."""
    cells = {}
    xf = shape.xform
    cells["PinX"] = {"V": str(xf.pin_x), "F": ""}
    cells["PinY"] = {"V": str(xf.pin_y), "F": ""}
    cells["Width"] = {"V": str(xf.width), "F": ""}
    cells["Height"] = {"V": str(xf.height), "F": ""}
    cells["LocPinX"] = {"V": str(xf.loc_pin_x), "F": ""}
    cells["LocPinY"] = {"V": str(xf.loc_pin_y), "F": ""}
    cells["Angle"] = {"V": str(xf.angle), "F": ""}
    if xf.flip_x: cells["FlipX"] = {"V": "1", "F": ""}
    if xf.flip_y: cells["FlipY"] = {"V": "1", "F": ""}
    cells["LineWeight"] = {"V": str(shape.line_weight), "F": ""}
    cells["LineColor"] = {"V": shape.line_color, "F": ""}
    cells["LinePattern"] = {"V": str(shape.line_pattern), "F": ""}
    if shape.fill_foreground:
        cells["FillForegnd"] = {"V": shape.fill_foreground, "F": ""}
    if shape.fill_background:
        cells["FillBkgnd"] = {"V": shape.fill_background, "F": ""}
    cells["FillPattern"] = {"V": str(shape.fill_pattern), "F": ""}

    # Text transform
    if shape.text_xform:
        txf = shape.text_xform
        cells["TxtPinX"] = {"V": str(txf.txt_pin_x), "F": ""}
        cells["TxtPinY"] = {"V": str(txf.txt_pin_y), "F": ""}
        cells["TxtWidth"] = {"V": str(txf.txt_width), "F": ""}
        cells["TxtHeight"] = {"V": str(txf.txt_height), "F": ""}
        cells["TxtLocPinX"] = {"V": str(txf.txt_loc_pin_x), "F": ""}
        cells["TxtLocPinY"] = {"V": str(txf.txt_loc_pin_y), "F": ""}
        cells["TxtAngle"] = {"V": str(txf.txt_angle), "F": ""}

    # 1D connector endpoints
    if shape.xform_1d:
        xf1d = shape.xform_1d
        cells["BeginX"] = {"V": str(xf1d.begin_x), "F": ""}
        cells["BeginY"] = {"V": str(xf1d.begin_y), "F": ""}
        cells["EndX"] = {"V": str(xf1d.end_x), "F": ""}
        cells["EndY"] = {"V": str(xf1d.end_y), "F": ""}

    # Text block
    if shape.text_block_margin_left or shape.text_block_margin_right:
        cells["LeftMargin"] = {"V": str(shape.text_block_margin_left), "F": ""}
        cells["RightMargin"] = {"V": str(shape.text_block_margin_right), "F": ""}
    if shape.text_block_margin_top or shape.text_block_margin_bottom:
        cells["TopMargin"] = {"V": str(shape.text_block_margin_top), "F": ""}
        cells["BottomMargin"] = {"V": str(shape.text_block_margin_bottom), "F": ""}
    if shape.text_block_valign:
        cells["VerticalAlign"] = {"V": str(shape.text_block_valign), "F": ""}
    if shape.text_block_bg:
        cells["TextBkgnd"] = {"V": shape.text_block_bg, "F": ""}

    # Shadow
    if shape.shadow_color:
        cells["ShdwForegnd"] = {"V": shape.shadow_color, "F": ""}
    if shape.shadow_offset_x:
        cells["ShapeShdwOffsetX"] = {"V": str(shape.shadow_offset_x), "F": ""}
    if shape.shadow_offset_y:
        cells["ShapeShdwOffsetY"] = {"V": str(shape.shadow_offset_y), "F": ""}
    if shape.shadow_pattern:
        cells["ShdwPattern"] = {"V": str(shape.shadow_pattern), "F": ""}

    # Layer membership
    if shape.layer_member:
        cells["LayerMember"] = {"V": shape.layer_member, "F": ""}

    geometry = []
    for geom in shape.geometry:
        geo_dict = {"no_fill": geom.no_fill, "no_line": geom.no_line,
                    "no_show": geom.no_show, "ix": "0", "rows": []}
        for row in geom.rows:
            row_dict = {"type": row.row_type, "ix": "",
                        "cells": {"X": {"V": str(row.x), "F": ""},
                                  "Y": {"V": str(row.y), "F": ""}}}
            if row.row_type in ("ArcTo", "Ellipse", "EllipticalArcTo",
                                "SplineStart", "SplineKnot", "InfiniteLine"):
                row_dict["cells"]["A"] = {"V": str(row.a), "F": ""}
                row_dict["cells"]["B"] = {"V": str(row.b), "F": ""}
            if row.row_type in ("Ellipse", "EllipticalArcTo", "SplineStart"):
                row_dict["cells"]["C"] = {"V": str(row.c), "F": ""}
                row_dict["cells"]["D"] = {"V": str(row.d), "F": ""}
            if row.row_type == "NURBSTo":
                row_dict["cells"]["A"] = {"V": str(row.knot_last), "F": ""}
                row_dict["cells"]["B"] = {"V": str(row.degree), "F": ""}
                row_dict["cells"]["C"] = {"V": str(row.x_type), "F": ""}
                row_dict["cells"]["D"] = {"V": str(row.y_type), "F": ""}
                # Encode control points as E cell (NURBS formula)
                if row.points:
                    pts_str = ";".join(f"{p[0]},{p[1]},{p[2]},{p[3]}" for p in row.points)
                    row_dict["cells"]["E"] = {"V": pts_str, "F": "NURBS(...)"}
            if row.row_type == "PolylineTo":
                # Encode points as formula
                if row.points:
                    pts_str = ";".join(f"{p[0]},{p[1]}" for p in row.points)
                    row_dict["cells"]["A"] = {"V": pts_str, "F": "POLYLINE(...)"}
            if row.row_type == "SplineStart":
                row_dict["cells"]["D"] = {"V": str(row.degree), "F": ""}
            geo_dict["rows"].append(row_dict)
        geometry.append(geo_dict)

    char_formats = {}
    for i, cf in enumerate(shape.char_formats):
        char_formats[str(i)] = {
            "Size": str(cf.font_size / 72.0) if cf.font_size > 0 else "0.1111",
            "Color": f"#{cf.color_r:02X}{cf.color_g:02X}{cf.color_b:02X}",
            "Style": str((1 if cf.bold else 0) | (2 if cf.italic else 0) | (4 if cf.underline else 0)),
            "Font": "",
        }

    para_formats = {}
    for i, pf in enumerate(shape.para_formats):
        para_formats[str(i)] = {
            "IndFirst": str(pf.indent_first),
            "IndLeft": str(pf.indent_left),
            "IndRight": str(pf.indent_right),
            "SpLine": str(pf.spacing_line),
            "SpBefore": str(pf.spacing_before),
            "SpAfter": str(pf.spacing_after),
            "HorzAlign": str(pf.horiz_align),
            "Bullet": str(pf.bullet),
        }

    # Foreign data (embedded images)
    foreign_data_dict = None
    if shape.foreign_data and shape.foreign_data.data:
        import base64
        fd = shape.foreign_data
        mime_map = {"png": "image/png", "jpg": "image/jpeg", "bmp": "image/bmp",
                    "emf": "image/x-emf", "wmf": "image/x-wmf", "gif": "image/gif",
                    "tiff": "image/tiff"}
        mime = mime_map.get(fd.img_format, "application/octet-stream")
        foreign_data_dict = {
            "type": fd.data_type,
            "format": fd.img_format,
            "mime": mime,
            "data": base64.b64encode(fd.data).decode('ascii'),
        }

    # Connection points
    connections = {}
    for i, cp in enumerate(shape.connection_points):
        connections[str(i)] = {
            "X": {"V": str(cp.x), "F": ""},
            "Y": {"V": str(cp.y), "F": ""},
        }

    return {
        "id": str(shape.shape_id), "name": "", "name_u": "",
        "type": shape.shape_type,
        "master": str(shape.master_page) if shape.master_page >= 0 else "",
        "master_shape": str(shape.master_shape) if shape.master_shape >= 0 else "",
        "cells": cells, "geometry": geometry,
        "text": shape.text,
        "text_parts": [{"text": shape.text, "cp": "0", "pp": "0"}] if shape.text else [],
        "char_formats": char_formats, "para_formats": para_formats,
        "sub_shapes": [_vsd_shape_to_dict(s) for s in shape.sub_shapes],
        "controls": {}, "connections": connections, "user": {},
        "foreign_data": foreign_data_dict,
        "line_style": "", "fill_style": "", "text_style": "",
    }


def parse_vsd_file(file_path: str | Path) -> VsdDocument:
    """Parse a .vsd file and return a VsdDocument."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    data = path.read_bytes()
    parser = VsdParser(data)
    return parser.parse()


def parse_vsd_to_dicts(file_path: str | Path) -> list[dict]:
    """Parse a .vsd file and return page data compatible with the VSDX SVG renderer.

    Returns list of dicts with page_width, page_height, shapes, name.
    """
    doc = parse_vsd_file(file_path)
    pages = []
    for page in doc.pages:
        # Skip background pages (they should be used as backgrounds, not standalone)
        if page.background:
            continue
        shape_dicts = [_vsd_shape_to_dict(s) for s in page.shapes]
        pages.append({
            "page_width": page.width,
            "page_height": page.height,
            "shapes": shape_dicts,
            "name": page.name,
        })
    # If all pages were background, include them anyway
    if not pages:
        for page in doc.pages:
            shape_dicts = [_vsd_shape_to_dict(s) for s in page.shapes]
            pages.append({
                "page_width": page.width,
                "page_height": page.height,
                "shapes": shape_dicts,
                "name": page.name,
            })
    return pages
