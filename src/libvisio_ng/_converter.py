"""Convert Visio files (.vsdx, .vsd, .vstx, .vssx, .vss) to SVG.

Backend priority:
1. libvisio (vsd2xhtml) — lightweight, accurate
2. Built-in .vsdx XML parser — zero dependencies, .vsdx only

Author: Daniel Nylander <daniel@danielnylander.se>
"""

import base64

import math
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

def _(s): return s

# Visio XML namespaces
_NS = {
    "v": "http://schemas.microsoft.com/office/visio/2012/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_VNS = _NS["v"]
_VTAG = f"{{{_VNS}}}"

# Supported file extensions
VISIO_EXTENSIONS = {".vsd", ".vsdx", ".vsdm"}
TEMPLATE_EXTENSIONS = {".vst", ".vstx", ".vstm"}
STENCIL_EXTENSIONS = {".vss", ".vssx", ".vssm"}
ALL_EXTENSIONS = VISIO_EXTENSIONS | TEMPLATE_EXTENSIONS | STENCIL_EXTENSIONS
# XML-based (ZIP) formats that use the built-in parser
_XML_EXTENSIONS = {".vsdx", ".vsdm", ".vssx", ".vssm", ".vstx", ".vstm"}

# Visio color index table (standard colors)
_VISIO_COLORS = {
    0: "#000000",  # Black
    1: "#FFFFFF",  # White
    2: "#FF0000",  # Red
    3: "#00FF00",  # Green
    4: "#0000FF",  # Blue
    5: "#FFFF00",  # Yellow
    6: "#FF00FF",  # Magenta
    7: "#00FFFF",  # Cyan
    8: "#800000",  # Dark Red
    9: "#008000",  # Dark Green
    10: "#000080", # Dark Blue
    11: "#808000", # Dark Yellow (Olive)
    12: "#800080", # Dark Magenta (Purple)
    13: "#008080", # Dark Cyan (Teal)
    14: "#C0C0C0", # Light Gray
    15: "#808080", # Dark Gray
    16: "#993366", # Rose
    17: "#333399", # Indigo
    18: "#333333", # Charcoal
    19: "#003300", # Forest
    20: "#003366", # Marine
    21: "#993300", # Brown
    22: "#993366", # Plum
    23: "#333399", # Navy
    24: "#E6E6E6", # Pale Gray
}

# Visio line patterns
_LINE_PATTERNS = {
    0: "none",           # No line
    1: "",               # Solid
    2: "4,3",            # Dash
    3: "1,3",            # Dot
    4: "4,3,1,3",        # Dash-dot
    5: "4,3,1,3,1,3",   # Dash-dot-dot
    6: "8,3",            # Long dash
    7: "1,1",            # Dense dot
    8: "8,3,1,3",        # Long dash-dot
    9: "8,3,1,3,1,3",   # Long dash-dot-dot
    10: "12,6",          # Extra-long dash
    16: "6,3,6,3",       # Dash-dash
}

# Inches to SVG pixels conversion
_INCH_TO_PX = 72.0

# Arrow size lookup (BeginArrowSize/EndArrowSize 0-6 -> scale factor)
_ARROW_SIZES = {0: 0.6, 1: 0.8, 2: 1.0, 3: 1.2, 4: 1.6, 5: 2.0, 6: 2.5}

# MIME types for embedded images
_IMAGE_MIMETYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".emf": "image/x-emf",
    ".wmf": "image/x-wmf",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".svg": "image/svg+xml",
}

# Relationship namespace
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _lighten_color(hex_color: str, factor: float = 0.7) -> str:
    """Lighten a hex color by blending towards white.

    factor=0.0 returns original, factor=1.0 returns white.
    """
    hex_color = hex_color.strip().lstrip("#")
    if len(hex_color) != 6:
        return "#E8E8E8"
    try:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
    except ValueError:
        return "#E8E8E8"
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02X}{g:02X}{b:02X}"


def _is_black(color: str) -> bool:
    """Check if a color is black or near-black."""
    if not color:
        return False
    c = color.strip().upper()
    return c in ("#000000", "#000", "0")


def _is_dark_color(color: str) -> bool:
    """Check if a color is dark (luminance < 0.4)."""
    if not color or color == "none":
        return False
    c = color.strip().lstrip("#")
    if len(c) == 6:
        try:
            r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
            # Relative luminance
            lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            return lum < 0.4
        except ValueError:
            pass
    return False


def _hsl_to_rgb(h: int, s: int, l: int) -> str:
    """Convert Visio HSL (h=0-255, s=0-255, l=0-255) to #RRGGBB."""
    # Normalize to 0-1 range
    hf = (h / 255.0) * 360.0
    sf = s / 255.0
    lf = l / 255.0
    # HSL to RGB conversion
    if sf == 0:
        r = g = b = lf
    else:
        def hue2rgb(p, q, t):
            if t < 0: t += 1
            if t > 1: t -= 1
            if t < 1/6: return p + (q - p) * 6 * t
            if t < 1/2: return q
            if t < 2/3: return p + (q - p) * (2/3 - t) * 6
            return p
        q = lf * (1 + sf) if lf < 0.5 else lf + sf - lf * sf
        p = 2 * lf - q
        hn = hf / 360.0
        r = hue2rgb(p, q, hn + 1/3)
        g = hue2rgb(p, q, hn)
        b = hue2rgb(p, q, hn - 1/3)
    return f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"


def _resolve_color(val: str, theme_colors: dict[str, str] | None = None) -> str:
    """Convert a Visio color value to an SVG color string.

    Handles: color index, #RRGGBB, RGB(r,g,b), HSL(h,s,l), THEMEVAL(), etc.
    Returns empty string for unresolvable values (caller decides default).
    """
    if not val:
        return ""
    val = val.strip()

    # THEMEVAL or formula — try to resolve from theme colors
    if "THEMEVAL" in val or "THEMEGUARD" in val:
        if theme_colors:
            # Extract THEMEVAL argument: THEMEVAL("accent1",0) or THEMEVAL(0)
            m = re.search(r'THEMEVAL\s*\(\s*"?(\w+)"?', val, re.IGNORECASE)
            if m:
                key = m.group(1).lower()
                if key in theme_colors:
                    return theme_colors[key]
                # Try numeric
                try:
                    idx = int(key)
                    if str(idx) in theme_colors:
                        return theme_colors[str(idx)]
                except ValueError:
                    pass
            # THEMEGUARD(THEMEVAL(...))
            m2 = re.search(r'THEMEVAL\s*\(\s*(\d+)', val, re.IGNORECASE)
            if m2:
                idx = m2.group(1) # type: ignore[assignment]
                if idx in theme_colors:
                    return theme_colors[idx] # type: ignore[index]
        return ""

    if val == "Inh" or val.startswith("=") or "THEME" in val:
        return ""

    # #RRGGBB or #RGB
    if val.startswith("#"):
        return val

    # RGB(r,g,b) function
    m = re.match(r"RGB\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", val, re.IGNORECASE)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"#{r:02X}{g:02X}{b:02X}"

    # HSL(h,s,l) function — Visio uses 0-255 range
    m = re.match(r"HSL\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", val, re.IGNORECASE)
    if m:
        return _hsl_to_rgb(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # Numeric index
    try:
        idx = int(val)
        return _VISIO_COLORS.get(idx, "")
    except ValueError:
        pass

    # Try float index
    try:
        idx = int(float(val))
        return _VISIO_COLORS.get(idx, "")
    except (ValueError, TypeError):
        pass

    return ""


def _get_dash_array(pattern: int, weight: float) -> str:
    """Get SVG stroke-dasharray for a Visio line pattern."""
    if pattern == 0:
        return "none"  # No line
    p = _LINE_PATTERNS.get(pattern, "")
    if not p or p == "none":
        # For unknown patterns 2-23, generate a reasonable dash pattern
        if 2 <= pattern <= 23:
            # Generate based on pattern number
            if pattern % 3 == 0:
                p = "1,2"  # dot-like
            elif pattern % 3 == 1:
                p = "6,3"  # dash-like
            else:
                p = "6,3,1,3"  # dash-dot
        else:
            return ""
    # Scale dash pattern by stroke weight
    scale = max(weight, 0.5)
    parts = [str(round(float(x) * scale, 1)) for x in p.split(",")]
    return ",".join(parts)


def _safe_float(val: str | None, default: float = 0.0) -> float:
    """Parse a float value, returning default on failure."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _escape_xml(text: str) -> str:
    """Escape text for XML/SVG output."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ---------------------------------------------------------------------------
# Embedded image support
# ---------------------------------------------------------------------------

def _apply_color_transforms(elem: ET.Element, base_color: str, dml_ns: str) -> str:
    """Apply DrawingML color transform children (tint, shade, lumMod, lumOff, satMod, alpha).

    These are sub-elements like <a:tint val="50000"/> meaning 50% tint.
    """
    if not base_color or not base_color.startswith("#") or len(base_color) != 7:
        return base_color
    try:
        r = int(base_color[1:3], 16)
        g = int(base_color[3:5], 16)
        b = int(base_color[5:7], 16)
    except ValueError:
        return base_color

    for child in elem:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        val = int(child.get("val", "0"))
        pct = val / 100000.0  # DrawingML uses 1/100000 units

        if tag == "tint":
            # Tint: blend towards white
            r = int(r + (255 - r) * pct)
            g = int(g + (255 - g) * pct)
            b = int(b + (255 - b) * pct)
        elif tag == "shade":
            # Shade: blend towards black
            r = int(r * pct)
            g = int(g * pct)
            b = int(b * pct)
        elif tag == "lumMod":
            # Luminance modulation
            r = min(255, int(r * pct))
            g = min(255, int(g * pct))
            b = min(255, int(b * pct))
        elif tag == "lumOff":
            # Luminance offset
            off = int(255 * pct)
            r = min(255, max(0, r + off))
            g = min(255, max(0, g + off))
            b = min(255, max(0, b + off))
        elif tag == "satMod":
            # Saturation modulation — convert to HSL, modify, convert back
            pass  # Complex; skip for now

    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return f"#{r:02X}{g:02X}{b:02X}"


def _parse_theme(zf: zipfile.ZipFile) -> dict[str, str]:
    """Parse theme colors from visio/theme/theme1.xml.

    Returns a dict mapping theme color names to #RRGGBB values.
    Keys: dk1, lt1, dk2, lt2, accent1-6, hlink, folHlink
    Also maps numeric indices used by Visio THEMEVAL:
      0->dk1, 1->lt1, 2->dk2, 3->lt2, 4->accent1, ..., 9->accent6,
      10->hlink, 11->folHlink
    """
    _DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
    theme_colors: dict[str, str] = {}

    # Try theme1.xml first, then theme2.xml
    for theme_file in ("visio/theme/theme1.xml", "visio/theme/theme2.xml"):
        try:
            theme_xml = zf.read(theme_file)
        except (KeyError, zipfile.BadZipFile):
            continue

        try:
            root = ET.fromstring(theme_xml)
        except ET.ParseError:
            continue

        # Find clrScheme
        for clr_scheme in root.iter(f"{{{_DML_NS}}}clrScheme"):
            color_names = [
                "dk1", "lt1", "dk2", "lt2",
                "accent1", "accent2", "accent3", "accent4",
                "accent5", "accent6", "hlink", "folHlink",
            ]
            for cname in color_names:
                elem = clr_scheme.find(f"{{{_DML_NS}}}{cname}")
                if elem is None:
                    continue
                # Look for srgbClr or sysClr
                srgb = elem.find(f"{{{_DML_NS}}}srgbClr")
                if srgb is not None:
                    val = srgb.get("val", "")
                    if val:
                        theme_colors[cname] = f"#{val}"
                else:
                    sys_clr = elem.find(f"{{{_DML_NS}}}sysClr")
                    if sys_clr is not None:
                        val = sys_clr.get("lastClr", "") or sys_clr.get("val", "")
                        if val and len(val) == 6:
                            theme_colors[cname] = f"#{val}"

            break  # Only use the first clrScheme found

        if theme_colors:
            break  # Got colors from this theme file

    # Build numeric index mapping (Visio theme color indices)
    _idx_map = {
        0: "dk1", 1: "lt1", 2: "dk2", 3: "lt2",
        4: "accent1", 5: "accent2", 6: "accent3", 7: "accent4",
        8: "accent5", 9: "accent6", 10: "hlink", 11: "folHlink",
    }
    for idx, name in _idx_map.items():
        if name in theme_colors:
            theme_colors[str(idx)] = theme_colors[name]

    return theme_colors


def _parse_layers(page_xml_root: ET.Element) -> dict[str, dict]:
    """Parse layer definitions from a page's PageSheet.

    Returns {layer_index: {"name": str, "visible": bool}} .
    """
    layers: dict[str, dict] = {}
    page_sheet = page_xml_root.find(f"{_VTAG}PageSheet")
    if page_sheet is None:
        return layers

    for section in page_sheet.findall(f"{_VTAG}Section"):
        if section.get("N") != "Layer":
            continue
        for row in section.findall(f"{_VTAG}Row"):
            ix = row.get("IX", "")
            cells = {}
            for cell in row.findall(f"{_VTAG}Cell"):
                cells[cell.get("N", "")] = cell.get("V", "")
            visible = cells.get("Visible", "1") != "0"
            name = cells.get("Name", f"Layer {ix}")
            layers[ix] = {"name": name, "visible": visible}

    return layers



def _parse_stylesheets(zf: zipfile.ZipFile) -> dict[str, dict]:
    """Parse StyleSheet elements from visio/document.xml.

    Returns {style_id: {"cells": {name: {"V": val, "F": formula}},
                         "line_style": parent_id, "fill_style": parent_id,
                         "text_style": parent_id}} .
    """
    styles: dict[str, dict] = {}
    try:
        doc_xml = zf.read("visio/document.xml")
    except (KeyError, zipfile.BadZipFile):
        return styles
    try:
        root = ET.fromstring(doc_xml)
    except ET.ParseError:
        return styles

    for ss in root.findall(f".//{_VTAG}StyleSheet"):
        sid = ss.get("ID", "")
        if not sid:
            continue
        cells = {}
        for cell in ss.findall(f"{_VTAG}Cell"):
            n = cell.get("N", "")
            v = cell.get("V", "")
            f_attr = cell.get("F", "")
            cells[n] = {"V": v, "F": f_attr}
        styles[sid] = {
            "cells": cells,
            "line_style": ss.get("LineStyle", ""),
            "fill_style": ss.get("FillStyle", ""),
            "text_style": ss.get("TextStyle", ""),
        }
    return styles


def _resolve_style_cell(styles: dict, style_id: str, cell_name: str,
                         category: str = "line", _depth: int = 0) -> str:
    """Walk the StyleSheet inheritance chain to resolve a cell value."""
    if _depth > 10 or not style_id or style_id not in styles:
        return ""
    ss = styles[style_id]
    cell = ss["cells"].get(cell_name, {})
    val = cell.get("V", "")
    formula = cell.get("F", "")

    if val and formula != "Inh" and val != "Themed":
        return val

    parent_key = {"line": "line_style", "fill": "fill_style",
                  "text": "text_style"}.get(category, "line_style")
    parent_id = ss.get(parent_key, "")
    if parent_id and parent_id != style_id:
        return _resolve_style_cell(styles, parent_id, cell_name, category, _depth + 1)
    return val


def _extract_media(zf: zipfile.ZipFile) -> dict[str, bytes]:
    """Extract all files from visio/media/ in the ZIP.

    Returns {filename: bytes} e.g. {"image1.png": b"..."}
    """
    media = {}
    for name in zf.namelist():
        if name.startswith("visio/media/"):
            fname = name.split("/")[-1]
            if fname:
                try:
                    media[fname] = zf.read(name)
                except (KeyError, zipfile.BadZipFile):
                    pass
    return media


def _parse_rels(zf: zipfile.ZipFile, page_file: str) -> dict[str, str]:
    """Parse relationship file for a page to map rId -> target path.

    For visio/pages/page1.xml, the rels file is
    visio/pages/_rels/page1.xml.rels
    """
    page_dir = os.path.dirname(page_file)
    page_basename = os.path.basename(page_file)
    rels_path = f"{page_dir}/_rels/{page_basename}.rels"

    rels = {}
    try:
        rels_xml = zf.read(rels_path)
        root = ET.fromstring(rels_xml)
        for rel in root.findall(f"{{{_RELS_NS}}}Relationship"):
            rid = rel.get("Id", "")
            target = rel.get("Target", "")
            if rid and target:
                rels[rid] = target
    except (KeyError, ET.ParseError):
        pass
    return rels



def _convert_emf_to_svg(data: bytes, ext: str) -> bytes | None:
    """Convert EMF/WMF data to SVG using inkscape. Returns SVG bytes or None."""
    inkscape = shutil.which("inkscape")
    if not inkscape:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_in:
            tmp_in.write(data)
            tmp_in_path = tmp_in.name
        tmp_out_path = tmp_in_path + ".svg"
        result = subprocess.run(
            [inkscape, tmp_in_path, "--export-type=svg",
             f"--export-filename={tmp_out_path}"],
            capture_output=True, timeout=15
        )
        if result.returncode == 0 and os.path.exists(tmp_out_path):
            with open(tmp_out_path, "rb") as f:
                svg_data = f.read()
            os.unlink(tmp_out_path)
            os.unlink(tmp_in_path)
            return svg_data
        os.unlink(tmp_in_path)
        if os.path.exists(tmp_out_path):
            os.unlink(tmp_out_path)
    except Exception:
        pass
    return None


def _image_to_data_uri(data: bytes, filename: str) -> str:
    """Convert image bytes to a base64 data URI."""
    ext = os.path.splitext(filename)[1].lower()
    # Convert BMP to PNG for data URI (BMP not widely supported in SVG)
    if ext in (".bmp", ".dib"):
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            data = buf.getvalue()
            ext = ".png"
        except Exception:
            pass
    # EMF/WMF: convert to SVG via inkscape for high-quality rendering
    if ext in (".emf", ".wmf"):
        svg_data = _convert_emf_to_svg(data, ext)
        if svg_data:
            b64 = base64.b64encode(svg_data).decode("ascii")
            return f"data:image/svg+xml;base64,{b64}"
        return ""  # Skip unsupported format
    mime = _IMAGE_MIMETYPES.get(ext, "image/png")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _save_image_file(data: bytes, filename: str, output_dir: str) -> str:
    """Save image to output directory and return relative filename.

    Converts BMP/EMF/WMF to PNG for broad SVG renderer compatibility.
    """
    ext = os.path.splitext(filename)[1].lower()
    # Convert BMP to PNG for compatibility
    if ext in (".bmp", ".dib"):
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data))
            png_filename = os.path.splitext(filename)[0] + ".png"
            dest = os.path.join(output_dir, png_filename)
            img.save(dest, "PNG")
            return png_filename
        except Exception:
            pass  # Fall through to raw save
    # EMF/WMF: try to convert with PIL (limited support)
    if ext in (".emf", ".wmf"):
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data))
            png_filename = os.path.splitext(filename)[0] + ".png"
            dest = os.path.join(output_dir, png_filename)
            img.save(dest, "PNG")
            return png_filename
        except Exception:
            pass
        return ""  # Don't save unsupported format
    dest = os.path.join(output_dir, filename)
    with open(dest, "wb") as f:
        f.write(data)
    return filename


def _parse_foreign_data(shape_elem: ET.Element) -> dict | None:
    """Parse ForeignData element from a shape.

    Returns {"type": "bitmap"|"metafile", "data": base64_str, "rel_id": rIdN}
    or None if no foreign data.
    """
    fd = shape_elem.find(f"{_VTAG}ForeignData")
    if fd is None:
        return None

    info = {
        "foreign_type": fd.get("ForeignType", ""),
        "compression": fd.get("CompressionType", ""),
        "data": None,
        "rel_id": None,
    }

    # Check for Rel element (can be in Visio namespace or r: namespace)
    rel_elem = fd.find(f"{_VTAG}Rel")
    if rel_elem is None:
        rel_elem = fd.find(f"{{{_NS['r']}}}Rel")
    if rel_elem is not None:
        # The r:id attribute may use full namespace
        info["rel_id"] = rel_elem.get(f"{{{_NS['r']}}}id", "")
        if not info["rel_id"]:
            info["rel_id"] = rel_elem.get("r:id", "")
        if not info["rel_id"]:
            for attr_name, attr_val in rel_elem.attrib.items():
                if attr_name.endswith("}id") or attr_name == "id":
                    info["rel_id"] = attr_val
                    break
    else:
        # Inline data
        text = fd.text
        if text and text.strip():
            info["data"] = text.strip()

    return info


# ---------------------------------------------------------------------------
# Arrow marker SVG generation
# ---------------------------------------------------------------------------

def _arrow_marker_defs(used_markers: set[str]) -> list[str]:
    """Generate SVG <defs> for arrow markers.

    used_markers: set of marker IDs like "arrow_end_3", "arrow_start_2"
    """
    if not used_markers:
        return []

    lines = ["<defs>"]
    for marker_id in sorted(used_markers):
        # Parse: arrow_{start|end}_{size}_{color}
        parts = marker_id.split("_", 3)
        direction = parts[1] if len(parts) > 1 else "end"
        size_idx = int(parts[2]) if len(parts) > 2 else 3
        color = f"#{parts[3]}" if len(parts) > 3 else "#333333"

        scale = _ARROW_SIZES.get(size_idx, 1.0)
        marker_w = 10 * scale
        marker_h = 7 * scale

        if direction == "start":
            # Reverse triangle for start
            lines.append(
                f'<marker id="{marker_id}" markerWidth="{marker_w:.1f}" '
                f'markerHeight="{marker_h:.1f}" refX="0" refY="{marker_h/2:.1f}" '
                f'orient="auto" markerUnits="userSpaceOnUse">'
                f'<polygon points="{marker_w:.1f} 0, 0 {marker_h/2:.1f}, '
                f'{marker_w:.1f} {marker_h:.1f}" fill="{color}"/>'
                f'</marker>'
            )
        else:
            # Forward triangle for end
            lines.append(
                f'<marker id="{marker_id}" markerWidth="{marker_w:.1f}" '
                f'markerHeight="{marker_h:.1f}" refX="{marker_w:.1f}" '
                f'refY="{marker_h/2:.1f}" orient="auto" markerUnits="userSpaceOnUse">'
                f'<polygon points="0 0, {marker_w:.1f} {marker_h/2:.1f}, '
                f'0 {marker_h:.1f}" fill="{color}"/>'
                f'</marker>'
            )

    lines.append("</defs>")
    return lines


def _fill_pattern_defs(patterns: dict[str, dict]) -> list[str]:
    """Generate SVG <defs> for hatching/crosshatch fill patterns.

    patterns: {pattern_id: {"fg": color, "bg": color, "type": int}}
    """
    if not patterns:
        return []
    lines = []
    for pid, p in sorted(patterns.items()):
        fg = p.get("fg", "#000000")
        bg = p.get("bg", "#FFFFFF")
        pat_type = p.get("type", 2)
        spacing = 6
        stroke_w = 1.0

        if pat_type in (2, 3, 4, 5):
            # Horizontal/vertical/diagonal lines
            if pat_type == 2:
                # Horizontal lines
                lines.append(
                    f'<pattern id="{pid}" patternUnits="userSpaceOnUse" '
                    f'width="{spacing}" height="{spacing}">'
                    f'<rect width="{spacing}" height="{spacing}" fill="{bg}"/>'
                    f'<line x1="0" y1="{spacing/2}" x2="{spacing}" y2="{spacing/2}" '
                    f'stroke="{fg}" stroke-width="{stroke_w}"/>'
                    f'</pattern>'
                )
            elif pat_type == 3:
                # Vertical lines
                lines.append(
                    f'<pattern id="{pid}" patternUnits="userSpaceOnUse" '
                    f'width="{spacing}" height="{spacing}">'
                    f'<rect width="{spacing}" height="{spacing}" fill="{bg}"/>'
                    f'<line x1="{spacing/2}" y1="0" x2="{spacing/2}" y2="{spacing}" '
                    f'stroke="{fg}" stroke-width="{stroke_w}"/>'
                    f'</pattern>'
                )
            elif pat_type == 4:
                # Forward diagonal
                lines.append(
                    f'<pattern id="{pid}" patternUnits="userSpaceOnUse" '
                    f'width="{spacing}" height="{spacing}">'
                    f'<rect width="{spacing}" height="{spacing}" fill="{bg}"/>'
                    f'<line x1="0" y1="{spacing}" x2="{spacing}" y2="0" '
                    f'stroke="{fg}" stroke-width="{stroke_w}"/>'
                    f'</pattern>'
                )
            elif pat_type == 5:
                # Backward diagonal
                lines.append(
                    f'<pattern id="{pid}" patternUnits="userSpaceOnUse" '
                    f'width="{spacing}" height="{spacing}">'
                    f'<rect width="{spacing}" height="{spacing}" fill="{bg}"/>'
                    f'<line x1="0" y1="0" x2="{spacing}" y2="{spacing}" '
                    f'stroke="{fg}" stroke-width="{stroke_w}"/>'
                    f'</pattern>'
                )
        elif pat_type in (6, 7, 8, 9):
            # Crosshatch patterns
            lines.append(
                f'<pattern id="{pid}" patternUnits="userSpaceOnUse" '
                f'width="{spacing}" height="{spacing}">'
                f'<rect width="{spacing}" height="{spacing}" fill="{bg}"/>'
                f'<line x1="0" y1="{spacing/2}" x2="{spacing}" y2="{spacing/2}" '
                f'stroke="{fg}" stroke-width="{stroke_w}"/>'
                f'<line x1="{spacing/2}" y1="0" x2="{spacing/2}" y2="{spacing}" '
                f'stroke="{fg}" stroke-width="{stroke_w}"/>'
                f'</pattern>'
            )
        elif pat_type in (10, 11, 12):
            # Diagonal crosshatch
            lines.append(
                f'<pattern id="{pid}" patternUnits="userSpaceOnUse" '
                f'width="{spacing}" height="{spacing}">'
                f'<rect width="{spacing}" height="{spacing}" fill="{bg}"/>'
                f'<line x1="0" y1="0" x2="{spacing}" y2="{spacing}" '
                f'stroke="{fg}" stroke-width="{stroke_w}"/>'
                f'<line x1="0" y1="{spacing}" x2="{spacing}" y2="0" '
                f'stroke="{fg}" stroke-width="{stroke_w}"/>'
                f'</pattern>'
            )
        else:
            # Dense patterns (13-24) - use dotted/denser versions
            dot_spacing = max(3, spacing - (pat_type - 12))
            lines.append(
                f'<pattern id="{pid}" patternUnits="userSpaceOnUse" '
                f'width="{dot_spacing}" height="{dot_spacing}">'
                f'<rect width="{dot_spacing}" height="{dot_spacing}" fill="{bg}"/>'
                f'<circle cx="{dot_spacing/2}" cy="{dot_spacing/2}" r="0.8" fill="{fg}"/>'
                f'</pattern>'
            )
    return lines


def _gradient_defs(gradients: dict[str, dict]) -> list[str]:
    """Generate SVG <defs> for gradient fills.

    gradients: {grad_id: {"start": color, "end": color, "dir": angle_deg}}
    """
    if not gradients:
        return []
    lines = []
    for gid, g in sorted(gradients.items()):
        if g.get("radial"):
            lines.append(
                f'<radialGradient id="{gid}" cx="50%" cy="50%" r="50%">'
                f'<stop offset="0%" stop-color="{g["start"]}"/>'
                f'<stop offset="100%" stop-color="{g["end"]}"/>'
                f'</radialGradient>'
            )
        else:
            angle = g.get("dir", 0)
            # Convert angle to x1,y1,x2,y2 for linearGradient
            rad = math.radians(angle)
            x1 = 50 - 50 * math.cos(rad)
            y1 = 50 + 50 * math.sin(rad)
            x2 = 50 + 50 * math.cos(rad)
            y2 = 50 - 50 * math.sin(rad)
            lines.append(
                f'<linearGradient id="{gid}" '
                f'x1="{x1:.1f}%" y1="{y1:.1f}%" x2="{x2:.1f}%" y2="{y2:.1f}%">'
                f'<stop offset="0%" stop-color="{g["start"]}"/>'
                f'<stop offset="100%" stop-color="{g["end"]}"/>'
                f'</linearGradient>'
            )
    return lines


def _shadow_filter_def() -> str:
    """Return SVG filter definition for drop shadows."""
    return (
        '<filter id="shadow" x="-10%" y="-10%" width="130%" height="130%">'
        '<feDropShadow dx="2" dy="2" stdDeviation="1.5" flood-color="#00000040"/>'
        '</filter>'
    )


# ---------------------------------------------------------------------------
# Master shape parsing
# ---------------------------------------------------------------------------

def _parse_master_shapes(zf: zipfile.ZipFile) -> dict[str, dict]:
    """Parse full shape data from master files.

    Returns {master_id: {shape_id: shape_dict, ...}, ...}
    Each shape_dict has: cells, geometry, text, char_formats, para_formats, sub_shapes
    """
    # First, read masters.xml to map Master ID -> rel ID,
    # then masters.xml.rels to map rel ID -> master file.
    master_id_to_file = {}  # Master ID -> master file number
    try:
        masters_xml = zf.read("visio/masters/masters.xml")
        root = ET.fromstring(masters_xml)

        # Parse rels to map rId -> filename
        rid_to_file = {}
        try:
            rels_xml = zf.read("visio/masters/_rels/masters.xml.rels")
            rels_root = ET.fromstring(rels_xml)
            for rel in rels_root:
                rid = rel.get("Id", "")
                target = rel.get("Target", "")
                # target is like "master2.xml"
                fname = Path(target).stem.replace("master", "")
                rid_to_file[rid] = fname
        except (KeyError, ET.ParseError):
            pass

        for master_el in root.findall(f"{_VTAG}Master"):
            mid = master_el.get("ID", "")
            # Find the Rel element — it's in the Visio namespace, not the rels namespace
            rel_el = master_el.find(f"{_VTAG}Rel")
            if rel_el is None:
                rel_el = master_el.find(f"{{{_NS['r']}}}Rel")
            if rel_el is not None:
                # The r:id attribute uses the relationships namespace
                rid = rel_el.get(f"{{{_NS['r']}}}id", "")
                if rid and rid in rid_to_file:
                    master_id_to_file[mid] = rid_to_file[rid]
                    continue
            # Fallback: assume master ID matches file number
            master_id_to_file[mid] = mid
    except (KeyError, ET.ParseError):
        pass

    # Parse all master files keyed by file number
    file_to_shapes = {}
    masters = {}
    for name in zf.namelist():
        if not (name.startswith("visio/masters/master") and name.endswith(".xml")):
            continue
        if "masters.xml" in name:
            continue
        master_num = Path(name).stem.replace("master", "")
        try:
            root = ET.fromstring(zf.read(name))
        except (ET.ParseError, KeyError):
            continue

        shapes_data = {}
        for shape in root.iter(f"{_VTAG}Shape"):
            sd = _parse_single_shape(shape)
            shapes_data[sd["id"]] = sd

        if shapes_data:
            file_to_shapes[master_num] = shapes_data

    # Re-key by Master ID using the mapping
    for mid, fnum in master_id_to_file.items():
        if fnum in file_to_shapes:
            masters[mid] = file_to_shapes[fnum]

    # For any file not mapped (e.g. missing rels), add by file number as fallback
    mapped_files = set(master_id_to_file.values())
    for fnum, shapes_data in file_to_shapes.items():
        if fnum not in mapped_files:
            masters[fnum] = shapes_data

    return masters


def _parse_single_shape(shape_elem: ET.Element) -> dict:
    """Parse a single <Shape> element into a rich dict."""
    sd: dict = {
        "id": shape_elem.get("ID", ""),
        "name": shape_elem.get("Name", ""),
        "name_u": shape_elem.get("NameU", ""),
        "type": shape_elem.get("Type", "Shape"),
        "master": shape_elem.get("Master", ""),
        "master_shape": shape_elem.get("MasterShape", ""),
        "cells": {},
        "geometry": [],
        "text": "",
        "text_parts": [],
        "char_formats": {},
        "para_formats": {},
        "sub_shapes": [],
        "controls": {},      # Row_N -> {X, Y, ...}
        "connections": {},    # IX -> {X, Y, ...}
        "user": {},           # User-defined cells (e.g., msvStructureType)
        "foreign_data": None, # ForeignData info for embedded images
        "hyperlinks": [],    # List of {description, address, sub_address, frame}
        "line_style": shape_elem.get("LineStyle", ""),
        "fill_style": shape_elem.get("FillStyle", ""),
        "text_style": shape_elem.get("TextStyle", ""),
    }

    # Parse top-level cells
    for cell in shape_elem.findall(f"{_VTAG}Cell"):
        n = cell.get("N", "")
        v = cell.get("V", "")
        f = cell.get("F", "")
        sd["cells"][n] = {"V": v, "F": f}

    # Parse Section elements
    for section in shape_elem.findall(f"{_VTAG}Section"):
        sec_name = section.get("N", "")

        if sec_name == "Geometry":
            geo = _parse_geometry_section(section)
            if geo:
                sd["geometry"].append(geo)

        elif sec_name == "Controls":
            for row in section.findall(f"{_VTAG}Row"):
                row_ix = row.get("IX", "0")
                ctrl = {}
                for cell in row.findall(f"{_VTAG}Cell"):
                    ctrl[cell.get("N", "")] = cell.get("V", "")
                sd["controls"][f"Row_{row_ix}"] = ctrl

        elif sec_name == "User":
            for row in section.findall(f"{_VTAG}Row"):
                row_name = row.get("N", "")
                user_vals = {}
                for cell in row.findall(f"{_VTAG}Cell"):
                    user_vals[cell.get("N", "")] = cell.get("V", "")
                sd.setdefault("user", {})[row_name] = user_vals

        elif sec_name == "Connection":
            for row in section.findall(f"{_VTAG}Row"):
                row_ix = row.get("IX", "0")
                conn = {}
                for cell in row.findall(f"{_VTAG}Cell"):
                    conn[cell.get("N", "")] = cell.get("V", "")
                sd["connections"][row_ix] = conn

        elif sec_name == "Character":
            for row in section.findall(f"{_VTAG}Row"):
                row_ix = row.get("IX", "0")
                fmt = {}
                for cell in row.findall(f"{_VTAG}Cell"):
                    fmt[cell.get("N", "")] = cell.get("V", "")
                sd["char_formats"][row_ix] = fmt

        elif sec_name == "Paragraph":
            for row in section.findall(f"{_VTAG}Row"):
                row_ix = row.get("IX", "0")
                fmt = {}
                for cell in row.findall(f"{_VTAG}Cell"):
                    fmt[cell.get("N", "")] = cell.get("V", "")
                sd["para_formats"][row_ix] = fmt

        elif sec_name == "FillGradientDef":
            # Multi-stop gradient definitions
            grad_stops = []
            for row in section.findall(f"{_VTAG}Row"):
                stop_cells = {}
                for cell in row.findall(f"{_VTAG}Cell"):
                    stop_cells[cell.get("N", "")] = cell.get("V", "")
                pos = _safe_float(stop_cells.get("GradientStopPosition", "0"))
                color = stop_cells.get("GradientStopColor", "")
                trans = _safe_float(stop_cells.get("GradientStopTransparency", "0"))
                if color:
                    grad_stops.append((pos * 100, color))
            if grad_stops:
                sd["_gradient_stops"] = [grad_stops]

    # Also parse Geom sections that are direct children (alternative format)
    for geom_idx in range(20):  # Max 20 geometry sections
        geom_section = shape_elem.find(f"{_VTAG}Geom")
        if geom_section is not None and geom_section not in []:
            break

    # Parse text
    text_elem = shape_elem.find(f"{_VTAG}Text")
    if text_elem is not None:
        sd["text"] = "".join(text_elem.itertext()).strip()
        sd["text_parts"] = _parse_text_element(text_elem)
        sd["_has_text_elem"] = True  # Mark that Text element exists (even if empty)

    # Parse sub-shapes (for groups)
    shapes_container = shape_elem.find(f"{_VTAG}Shapes")
    if shapes_container is not None:
        for sub_shape in shapes_container.findall(f"{_VTAG}Shape"):
            sd["sub_shapes"].append(_parse_single_shape(sub_shape))

    # Parse ForeignData (embedded images)
    fd_info = _parse_foreign_data(shape_elem)
    if fd_info:
        sd["foreign_data"] = fd_info

    # Parse hyperlinks (cross-page references, external links)
    for section in shape_elem.findall(f"{_VTAG}Section"):
        if section.get("N") == "Hyperlink":
            for row in section.findall(f"{_VTAG}Row"):
                link = {}
                for cell in row.findall(f"{_VTAG}Cell"):
                    n = cell.get("N", "")
                    v = cell.get("V", "")
                    if n == "Description":
                        link["description"] = v
                    elif n == "Address":
                        link["address"] = v
                    elif n == "SubAddress":
                        link["sub_address"] = v
                    elif n == "Frame":
                        link["frame"] = v
                if link:
                    sd["hyperlinks"].append(link)

    return sd


def _parse_geometry_section(section: ET.Element) -> dict:
    """Parse a Geometry section into a list of geometry rows."""
    geo = {"rows": [], "no_fill": False, "no_line": False, "no_show": False}

    # Check section-level cells
    for cell in section.findall(f"{_VTAG}Cell"):
        n = cell.get("N", "")
        v = cell.get("V", "0")
        if n == "NoFill" and v == "1":
            geo["no_fill"] = True
        elif n == "NoLine" and v == "1":
            geo["no_line"] = True
        elif n == "NoShow" and v == "1":
            geo["no_show"] = True

    for row in section.findall(f"{_VTAG}Row"):
        row_type = row.get("T", "")
        row_ix = row.get("IX", "")
        row_data = {"type": row_type, "cells": {}, "ix": row_ix}
        for cell in row.findall(f"{_VTAG}Cell"):
            n = cell.get("N", "")
            v = cell.get("V", "")
            f = cell.get("F", "")
            row_data["cells"][n] = {"V": v, "F": f} # type: ignore[index]
        geo["rows"].append(row_data) # type: ignore[attr-defined]

    # Store section IX for merging
    geo["ix"] = section.get("IX", "0")

    return geo


def _parse_text_element(text_elem: ET.Element) -> list:
    """Parse a <Text> element into parts with formatting references."""
    parts = []
    current_cp = "0"
    current_pp = "0"

    # Process text content with inline elements
    if text_elem.text:
        parts.append({"text": text_elem.text, "cp": current_cp, "pp": current_pp})

    for child in text_elem:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "cp":
            current_cp = child.get("IX", "0")
        elif tag == "pp":
            current_pp = child.get("IX", "0")
        elif tag == "fld":
            # Field element — extract text
            field_text = "".join(child.itertext()).strip()
            if field_text:
                parts.append({"text": field_text, "cp": current_cp, "pp": current_pp})
        if child.tail:
            parts.append({"text": child.tail, "cp": current_cp, "pp": current_pp})

    return parts


# ---------------------------------------------------------------------------
# Geometry to SVG path conversion
# ---------------------------------------------------------------------------

def _geometry_to_path(geo: dict, w: float, h: float,
                      master_w: float = 0.0, master_h: float = 0.0) -> str:
    """Convert a parsed Geometry section to an SVG path 'd' attribute.

    Coordinates are in local shape space (inches), will be scaled to px.
    w, h are shape width/height in inches for relative coordinates.
    master_w, master_h: if geometry was inherited from a master, these are
    the master's original dimensions for coordinate scaling.
    """
    if geo.get("no_show"):
        return ""

    # Use absolute dimensions for coordinate calculations — 1D connectors
    # can have negative Width/Height (e.g. Height=-0.867 when EndY < BeginY).
    abs_w = abs(w) if abs(w) > 1e-10 else 0.0
    abs_h = abs(h) if abs(h) > 1e-10 else 0.0

    # Compute scale factors if geometry came from a master with different dims
    abs_mw = abs(master_w)
    abs_mh = abs(master_h)
    sx = abs_w / abs_mw if abs_mw > 1e-6 and abs(abs_mw - abs_w) > 1e-6 else 1.0
    sy = abs_h / abs_mh if abs_mh > 1e-6 and abs(abs_mh - abs_h) > 1e-6 else 1.0

    d_parts = []
    cx, cy = 0.0, 0.0  # Current point (inches)

    for _row_idx, row in enumerate(geo["rows"]):
        rt = row["type"]
        cells = row["cells"]

        # Skip geometry rows where all coordinate cells are truly empty
        # (spurious rows from connectors with partial geometry)
        # Note: "0" IS a valid coordinate, so only skip if V is None/empty
        if rt in ("LineTo", "ArcTo"):
            _has_any = False
            for _cn in ("X", "Y"):
                _cv = cells.get(_cn, {}).get("V")
                if _cv is not None and _cv != "":
                    _has_any = True
                    break
            # Also check if there's a formula (F attribute) — inherited rows
            if not _has_any:
                for _cn in ("X", "Y"):
                    _cf = cells.get(_cn, {}).get("F", "")
                    if _cf and _cf != "Inh":
                        _has_any = True
                        break
            if not _has_any:
                continue

        if rt == "MoveTo":
            x = _safe_float(cells.get("X", {}).get("V")) * sx
            y = _safe_float(cells.get("Y", {}).get("V")) * sy
            d_parts.append(f"M {x * _INCH_TO_PX:.2f} {(abs_h - y) * _INCH_TO_PX:.2f}")
            cx, cy = x, y
            # Detect oval: MoveTo followed by all ArcTo with nonzero bulge
            remaining = geo["rows"][_row_idx + 1:]
            remaining_types = [r.get("type", "") for r in remaining if r.get("type", "")]
            if len(remaining_types) >= 3 and all(t == "ArcTo" for t in remaining_types[:len(remaining_types)]):
                # Collect all ArcTo endpoints
                arc_points = [(x, y)]
                for ar in remaining:
                    art = ar.get("T", ar.get("type", ""))
                    if art != "ArcTo":
                        break
                    ac = ar.get("cells", {})
                    ax = _safe_float(ac.get("X", {}).get("V")) * sx
                    ay = _safe_float(ac.get("Y", {}).get("V")) * sy
                    arc_points.append((ax, ay))
                # Check if it closes back to start and has enough arcs for an oval
                if len(arc_points) >= 4:
                    first = arc_points[0]
                    last = arc_points[-1]
                    dist = ((first[0]-last[0])**2 + (first[1]-last[1])**2)**0.5
                    if dist < 0.01:  # Closed shape
                        all_x = [p[0] for p in arc_points]
                        all_y = [p[1] for p in arc_points]
                        ecx = (min(all_x) + max(all_x)) / 2 * _INCH_TO_PX
                        ecy = (abs_h - (min(all_y) + max(all_y)) / 2) * _INCH_TO_PX
                        erx = (max(all_x) - min(all_x)) / 2 * _INCH_TO_PX
                        ery = (max(all_y) - min(all_y)) / 2 * _INCH_TO_PX
                        if erx > 0.5 and ery > 0.5:
                            d_parts.clear()
                            d_parts.append(f"M {ecx - erx:.2f} {ecy:.2f}")
                            d_parts.append(f"A {erx:.2f} {ery:.2f} 0 1 0 {ecx + erx:.2f} {ecy:.2f}")
                            d_parts.append(f"A {erx:.2f} {ery:.2f} 0 1 0 {ecx - erx:.2f} {ecy:.2f}")
                            d_parts.append("Z")
                            break  # skip remaining geometry rows

        elif rt == "RelMoveTo":
            x = _safe_float(cells.get("X", {}).get("V"))
            y = _safe_float(cells.get("Y", {}).get("V"))
            ax, ay = x * abs_w, y * abs_h
            d_parts.append(f"M {ax * _INCH_TO_PX:.2f} {(abs_h - ay) * _INCH_TO_PX:.2f}")
            cx, cy = ax, ay

        elif rt == "LineTo":
            x = _safe_float(cells.get("X", {}).get("V")) * sx
            y = _safe_float(cells.get("Y", {}).get("V")) * sy
            d_parts.append(f"L {x * _INCH_TO_PX:.2f} {(abs_h - y) * _INCH_TO_PX:.2f}")
            cx, cy = x, y

        elif rt == "RelLineTo":
            x = _safe_float(cells.get("X", {}).get("V"))
            y = _safe_float(cells.get("Y", {}).get("V"))
            ax, ay = x * abs_w, y * abs_h
            d_parts.append(f"L {ax * _INCH_TO_PX:.2f} {(abs_h - ay) * _INCH_TO_PX:.2f}")
            cx, cy = ax, ay

        elif rt == "ArcTo":
            x = _safe_float(cells.get("X", {}).get("V")) * sx
            y = _safe_float(cells.get("Y", {}).get("V")) * sy
            a = _safe_float(cells.get("A", {}).get("V")) * sy  # bulge scales with Y
            # A is the bulge/sagitta of the arc
            _append_arc(d_parts, cx, cy, x, y, a, abs_h)
            cx, cy = x, y

        elif rt == "EllipticalArcTo":
            x = _safe_float(cells.get("X", {}).get("V")) * sx
            y = _safe_float(cells.get("Y", {}).get("V")) * sy
            a = _safe_float(cells.get("A", {}).get("V")) * sx  # control point X
            b = _safe_float(cells.get("B", {}).get("V")) * sy  # control point Y
            c_angle = _safe_float(cells.get("C", {}).get("V"))  # angle of major axis (radians)
            d_ratio = _safe_float(cells.get("D", {}).get("V"))  # ratio major/minor axis
            _append_elliptical_arc(d_parts, cx, cy, x, y, a, b, d_ratio, c_angle, abs_h)
            cx, cy = x, y

        elif rt == "RelEllipticalArcTo":
            # Same as EllipticalArcTo but with relative coordinates (0-1)
            x = _safe_float(cells.get("X", {}).get("V")) * abs_w
            y = _safe_float(cells.get("Y", {}).get("V")) * abs_h
            a = _safe_float(cells.get("A", {}).get("V")) * abs_w  # control point X
            b = _safe_float(cells.get("B", {}).get("V")) * abs_h  # control point Y
            c_angle = _safe_float(cells.get("C", {}).get("V"))  # angle (radians)
            d_ratio = _safe_float(cells.get("D", {}).get("V"))  # ratio major/minor
            _append_elliptical_arc(d_parts, cx, cy, x, y, a, b, d_ratio, c_angle, abs_h)
            cx, cy = x, y

        elif rt == "NURBSTo":
            x = _safe_float(cells.get("X", {}).get("V")) * sx
            y = _safe_float(cells.get("Y", {}).get("V")) * sy
            # Parse NURBS formula from E cell for control points
            e_val = cells.get("E", {}).get("V", "")
            nurbs_pts = _parse_nurbs_formula(e_val, cx, cy, x, y, sx, sy)
            if nurbs_pts and len(nurbs_pts) >= 2:
                # Use quadratic or cubic Bézier approximation
                if len(nurbs_pts) == 2:
                    # Two control points → cubic Bézier
                    cp1x, cp1y = nurbs_pts[0]
                    cp2x, cp2y = nurbs_pts[1]
                    d_parts.append(
                        f"C {cp1x * _INCH_TO_PX:.2f} {(abs_h - cp1y) * _INCH_TO_PX:.2f} "
                        f"{cp2x * _INCH_TO_PX:.2f} {(abs_h - cp2y) * _INCH_TO_PX:.2f} "
                        f"{x * _INCH_TO_PX:.2f} {(abs_h - y) * _INCH_TO_PX:.2f}"
                    )
                else:
                    # Multiple points → approximate with lines through them
                    for px_val, py_val in nurbs_pts:
                        d_parts.append(f"L {px_val * _INCH_TO_PX:.2f} {(abs_h - py_val) * _INCH_TO_PX:.2f}")
                    d_parts.append(f"L {x * _INCH_TO_PX:.2f} {(abs_h - y) * _INCH_TO_PX:.2f}")
            else:
                d_parts.append(f"L {x * _INCH_TO_PX:.2f} {(abs_h - y) * _INCH_TO_PX:.2f}")
            cx, cy = x, y

        elif rt in ("RelCurveTo", "RelCubBezTo"):
            x = _safe_float(cells.get("X", {}).get("V"))
            y = _safe_float(cells.get("Y", {}).get("V"))
            a = _safe_float(cells.get("A", {}).get("V"))
            b = _safe_float(cells.get("B", {}).get("V"))
            c = _safe_float(cells.get("C", {}).get("V"))
            dd = _safe_float(cells.get("D", {}).get("V"))
            # Cubic bezier with relative coordinates
            cp1x, cp1y = a * abs_w, b * abs_h
            cp2x, cp2y = c * abs_w, dd * abs_h
            ex, ey = x * abs_w, y * abs_h
            d_parts.append(
                f"C {cp1x * _INCH_TO_PX:.2f} {(abs_h - cp1y) * _INCH_TO_PX:.2f} "
                f"{cp2x * _INCH_TO_PX:.2f} {(abs_h - cp2y) * _INCH_TO_PX:.2f} "
                f"{ex * _INCH_TO_PX:.2f} {(abs_h - ey) * _INCH_TO_PX:.2f}"
            )
            cx, cy = ex, ey

        elif rt == "Ellipse":
            # Full ellipse: center (X,Y), point on major axis (A,B), point on minor axis (C,D)
            ex = _safe_float(cells.get("X", {}).get("V")) * sx
            ey = _safe_float(cells.get("Y", {}).get("V")) * sy
            ea = _safe_float(cells.get("A", {}).get("V")) * sx
            eb = _safe_float(cells.get("B", {}).get("V")) * sy
            ec = _safe_float(cells.get("C", {}).get("V")) * sx
            ed = _safe_float(cells.get("D", {}).get("V")) * sy
            rx = math.sqrt((ea - ex) ** 2 + (eb - ey) ** 2)
            ry = math.sqrt((ec - ex) ** 2 + (ed - ey) ** 2)
            if rx < 0.001:
                rx = 0.001
            if ry < 0.001:
                ry = 0.001
            cpx = ex * _INCH_TO_PX
            cpy = (abs_h - ey) * _INCH_TO_PX
            rpx = rx * _INCH_TO_PX
            rpy = ry * _INCH_TO_PX
            # SVG ellipse as two arcs
            d_parts.append(
                f"M {cpx - rpx:.2f} {cpy:.2f} "
                f"A {rpx:.2f} {rpy:.2f} 0 1 0 {cpx + rpx:.2f} {cpy:.2f} "
                f"A {rpx:.2f} {rpy:.2f} 0 1 0 {cpx - rpx:.2f} {cpy:.2f} Z"
            )

        elif rt == "PolylineTo":
            x = _safe_float(cells.get("X", {}).get("V")) * sx
            y = _safe_float(cells.get("Y", {}).get("V")) * sy
            # Try to parse the formula for intermediate points
            a_cell = cells.get("A", {})
            formula = a_cell.get("F", "")
            pts = _parse_polyline_formula(formula, abs_w, abs_h)
            if not pts:
                # Try V cell: semicolon-separated "x,y" pairs from VSD parser
                v_val = a_cell.get("V", "")
                if ";" in v_val or "," in v_val:
                    pts = []
                    for pair in v_val.split(";"):
                        parts = pair.strip().split(",")
                        if len(parts) >= 2:
                            try:
                                pts.append((_safe_float(parts[0]) * sx,
                                            _safe_float(parts[1]) * sy))
                            except (ValueError, IndexError):
                                pass
            if pts:
                for px_val, py_val in pts:
                    d_parts.append(f"L {px_val * _INCH_TO_PX:.2f} {(abs_h - py_val) * _INCH_TO_PX:.2f}")
            d_parts.append(f"L {x * _INCH_TO_PX:.2f} {(abs_h - y) * _INCH_TO_PX:.2f}")
            cx, cy = x, y

        elif rt == "SplineStart":
            x = _safe_float(cells.get("X", {}).get("V")) * sx
            y = _safe_float(cells.get("Y", {}).get("V")) * sy
            d_parts.append(f"M {x * _INCH_TO_PX:.2f} {(abs_h - y) * _INCH_TO_PX:.2f}")
            cx, cy = x, y

        elif rt == "SplineKnot":
            x = _safe_float(cells.get("X", {}).get("V")) * sx
            y = _safe_float(cells.get("Y", {}).get("V")) * sy
            d_parts.append(f"L {x * _INCH_TO_PX:.2f} {(abs_h - y) * _INCH_TO_PX:.2f}")
            cx, cy = x, y

        elif rt == "InfiniteLine":
            x = _safe_float(cells.get("X", {}).get("V")) * sx
            y = _safe_float(cells.get("Y", {}).get("V")) * sy
            a = _safe_float(cells.get("A", {}).get("V")) * sx
            b = _safe_float(cells.get("B", {}).get("V")) * sy
            d_parts.append(f"M {x * _INCH_TO_PX:.2f} {(abs_h - y) * _INCH_TO_PX:.2f}")
            d_parts.append(f"L {a * _INCH_TO_PX:.2f} {(abs_h - b) * _INCH_TO_PX:.2f}")
            cx, cy = a, b

    result = " ".join(d_parts)
    # Ensure path starts with M (MoveTo) — invalid paths crash renderers
    if result and not result.startswith("M"):
        result = f"M 0.00 0.00 {result}"
    # Auto-close path if the last point is very close to the first MoveTo point
    # This ensures proper fill rendering for closed shapes
    if result and "Z" not in result and len(d_parts) >= 3:
        first_m = re.match(r'M\s+([-+]?[\d.]+)\s+([-+]?[\d.]+)', result)
        if first_m:
            last_part = d_parts[-1]
            last_coords = re.findall(r'([-+]?[\d.]+)', last_part)
            if len(last_coords) >= 2:
                sx_f, sy_f = float(first_m.group(1)), float(first_m.group(2))
                ex_f, ey_f = float(last_coords[-2]), float(last_coords[-1])
                if abs(sx_f - ex_f) < 0.5 and abs(sy_f - ey_f) < 0.5:
                    result += " Z"
    return result


def _append_arc(d_parts: list, cx: float, cy: float, x: float, y: float,
                bulge: float, h: float):
    """Append an arc segment (ArcTo) using SVG arc command.

    bulge (A) is the sagitta — distance from the midpoint of the chord to the arc.
    If bulge is 0, it's a straight line.
    """
    if abs(bulge) < 1e-6:
        d_parts.append(f"L {x * _INCH_TO_PX:.2f} {(h - y) * _INCH_TO_PX:.2f}")
        return

    # Compute arc from chord and sagitta
    dx = x - cx
    dy = y - cy
    chord = math.sqrt(dx * dx + dy * dy)
    if chord < 1e-10:
        return

    # Radius from sagitta: r = (chord²/4 + sagitta²) / (2 * |sagitta|)
    sagitta = abs(bulge)
    radius = (chord * chord / 4 + sagitta * sagitta) / (2 * sagitta)
    # Clamp radius to max 5x chord length to prevent absurdly large arcs
    max_radius = chord * 5.0
    if radius > max_radius:
        radius = max_radius
    radius_px = radius * _INCH_TO_PX

    # Determine sweep direction
    large_arc = 1 if sagitta > chord / 2 else 0
    sweep = 0 if bulge > 0 else 1

    d_parts.append(
        f"A {radius_px:.2f} {radius_px:.2f} 0 {large_arc} {sweep} "
        f"{x * _INCH_TO_PX:.2f} {(h - y) * _INCH_TO_PX:.2f}"
    )


def _append_elliptical_arc(d_parts: list, cx: float, cy: float,
                           x: float, y: float, a: float, b: float,
                           d_ratio: float, c_angle: float, h: float):
    """Append an elliptical arc segment (EllipticalArcTo).

    (a,b) = control point, d_ratio = aspect ratio (D cell),
    c_angle = rotation angle (C cell).
    Approximate with SVG arc.
    """
    # Compute approximate radius from control point
    mid_x = (cx + x) / 2
    mid_y = (cy + y) / 2
    dist_to_control = math.sqrt((a - mid_x) ** 2 + (b - mid_y) ** 2)
    chord = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)

    if chord < 1e-10:
        return

    sagitta = dist_to_control
    if sagitta < 1e-6:
        d_parts.append(f"L {x * _INCH_TO_PX:.2f} {(h - y) * _INCH_TO_PX:.2f}")
        return

    rx = (chord * chord / 4 + sagitta * sagitta) / (2 * sagitta)
    ry = rx / d_ratio if d_ratio > 0.001 else rx  # d_ratio is major/minor ratio
    angle_deg = math.degrees(c_angle) if c_angle else 0

    rx_px = abs(rx * _INCH_TO_PX)
    ry_px = abs(ry * _INCH_TO_PX)
    if rx_px < 0.1:
        rx_px = 0.1
    if ry_px < 0.1:
        ry_px = 0.1

    # Determine arc direction from control point position relative to chord.
    cross = (x - cx) * (b - cy) - (y - cy) * (a - cx)
    sweep = 0 if cross < 0 else 1
    large_arc = 0

    d_parts.append(
        f"A {rx_px:.2f} {ry_px:.2f} {angle_deg:.1f} {large_arc} {sweep} "
        f"{x * _INCH_TO_PX:.2f} {(h - y) * _INCH_TO_PX:.2f}"
    )


def _evaluate_nurbs_curve(ctrl_pts: list[tuple[float, float, float]],
                          knots: list[float], degree: int,
                          num_samples: int = 40) -> list[tuple[float, float]]:
    """Evaluate a NURBS curve using De Boor's algorithm.

    ctrl_pts: list of (x, y, weight) control points
    knots: knot vector
    degree: curve degree (typically 3)
    num_samples: number of output points for tessellation

    Returns list of (x, y) points along the curve.
    """
    n = len(ctrl_pts)
    if n < 2:
        return [(p[0], p[1]) for p in ctrl_pts]

    # Convert to homogeneous weighted coordinates
    weighted = [(p[0] * p[2], p[1] * p[2], p[2]) for p in ctrl_pts]

    # Parameter range from knots
    p = degree
    t_min = knots[p] if p < len(knots) else 0.0
    t_max = knots[n] if n < len(knots) else knots[-1]
    if abs(t_max - t_min) < 1e-10:
        return [(ctrl_pts[0][0], ctrl_pts[0][1]),
                (ctrl_pts[-1][0], ctrl_pts[-1][1])]

    result = []
    for step in range(num_samples + 1):
        t = t_min + (t_max - t_min) * step / num_samples
        # Clamp to valid range
        t = max(t_min, min(t, t_max - 1e-10))

        # Find knot span
        span = p
        for j in range(p, n):
            if j + 1 < len(knots) and knots[j + 1] > t:
                span = j
                break
        else:
            span = n - 1

        # De Boor recursion
        d = []
        for j in range(p + 1):
            idx = span - p + j
            if 0 <= idx < n:
                d.append(list(weighted[idx]))
            else:
                d.append([0.0, 0.0, 1.0])

        for r in range(1, p + 1):
            for j in range(p, r - 1, -1):
                left = span - p + j
                ki = left + p + 1 - r
                if ki < len(knots) and left < len(knots):
                    denom = knots[ki] - knots[left]
                    if abs(denom) > 1e-10:
                        alpha = (t - knots[left]) / denom
                    else:
                        alpha = 0.0
                else:
                    alpha = 0.0
                for k in range(3):
                    d[j][k] = (1.0 - alpha) * d[j - 1][k] + alpha * d[j][k]

        w = d[p][2]
        if abs(w) > 1e-10:
            result.append((d[p][0] / w, d[p][1] / w))
        elif result:
            result.append(result[-1])

    return result


def _parse_nurbs_formula(e_val: str, cx: float, cy: float,
                         ex: float, ey: float,
                         sx: float = 1.0, sy: float = 1.0) -> list[tuple[float, float]]:
    """Parse a NURBS formula and return intermediate control points.

    NURBS format: NURBS(knotLast, degree, xType, yType, x1,y1,k1,w1, x2,y2,k2,w2, ...)
    For degree=3 (cubic), we extract control points and scale them.
    Returns list of (x, y) control points in shape coordinates.
    """
    if not e_val:
        return []
    m = re.match(r"NURBS\s*\((.*)\)", e_val, re.IGNORECASE)
    if not m:
        return []
    try:
        vals = [float(v.strip()) for v in m.group(1).split(",")]
    except (ValueError, IndexError):
        return []
    if len(vals) < 8:
        return []
    knot_last = vals[0]
    degree = int(vals[1])
    x_type = int(vals[2])  # 0 = fraction of Width, 1 = absolute
    y_type = int(vals[3])  # 0 = fraction of Height, 1 = absolute
    # Extract control points (groups of 4: x, y, knot, weight)
    points = []
    for i in range(4, len(vals) - 3, 4):
        px = vals[i]
        py = vals[i + 1]
        # knot = vals[i + 2], weight = vals[i + 3] — not used for simple approx
        # When x_type/y_type=0, coords are fractions (0-1) — leave as-is
        # When =1, coords are absolute inches — apply scaling
        if x_type == 1:
            px *= sx
        if y_type == 1:
            py *= sy
        points.append((px, py))
    # Map NURBS control points to absolute coordinates.
    # When type=0 (fractional), the control points are fractions of the
    # overall curve parameter space from start to end.
    result = []
    for px, py in points:
        if x_type == 0:
            bx = cx + px * (ex - cx)
        else:
            bx = px  # already in absolute shape coords
        if y_type == 0:
            by = cy + py * (ey - cy)
        else:
            by = py  # already in absolute shape coords
        result.append((bx, by))
    return result


def _parse_polyline_formula(formula: str, w: float, h: float) -> list[tuple[float, float]]:
    """Parse a POLYLINE formula to extract points."""
    # Format: POLYLINE(0, 0, x1, y1, x2, y2, ...)
    pts: list = []
    m = re.match(r"POLYLINE\s*\((.*)\)", formula, re.IGNORECASE)
    if not m:
        return pts
    try:
        vals = [float(v.strip()) for v in m.group(1).split(",")]
        # Skip first two values (flags), then pairs
        for i in range(2, len(vals) - 1, 2):
            pts.append((vals[i], vals[i + 1]))
    except (ValueError, IndexError):
        pass
    return pts


# ---------------------------------------------------------------------------
# Shape merging (master inheritance)
# ---------------------------------------------------------------------------

def _merge_shape_with_master(shape: dict, masters: dict,
                              parent_master_id: str = "") -> dict:
    """Merge a shape with its master, local values override master values.

    For sub-shapes in groups, parent_master_id is the group's Master ID,
    and the sub-shape's master_shape references a shape within that master.
    """
    master_id = shape.get("master", "") or parent_master_id
    master_shape_id = shape.get("master_shape", "")

    if not master_id or master_id not in masters:
        return shape

    master_shapes = masters[master_id]

    # Find the right master shape
    master_sd = None
    if master_shape_id and master_shape_id in master_shapes:
        master_sd = master_shapes[master_shape_id]
    elif master_shapes:
        # Use first shape in master
        master_sd = next(iter(master_shapes.values()))

    if not master_sd:
        return shape

    # Merge cells: master provides defaults, local overrides.
    # Keep local cells that have either a value (V) or a formula (F),
    # since F="Inh" with V="" means "inherit from master" while F="" with
    # a concrete V means "override master".
    merged_cells = dict(master_sd.get("cells", {}))
    for k, v in shape["cells"].items():
        if v.get("V") or v.get("F"):
            merged_cells[k] = v
    shape["cells"] = merged_cells

    # Merge geometry: use local if present, otherwise master.
    # If local geometry has fewer rows than master (partial override with F='Inh'),
    # merge row-by-row using IX as key.
    master_geos = master_sd.get("geometry", [])
    if not shape["geometry"] and master_geos:
        shape["geometry"] = master_geos
        # Store master's original dimensions for geometry coordinate scaling
        master_w_val = master_sd.get("cells", {}).get("Width", {}).get("V")
        master_h_val = master_sd.get("cells", {}).get("Height", {}).get("V")
        if master_w_val:
            shape["_master_w"] = _safe_float(master_w_val)
        if master_h_val:
            shape["_master_h"] = _safe_float(master_h_val)
    elif shape["geometry"] and master_geos:
        # Mark that this shape had its own geometry (important for 1D connectors)
        shape["_has_own_geometry"] = True

        # Check if this is a 1D connector -- connectors use their own geometry
        # directly (routed paths), don't merge row-by-row with master.
        is_1d_shape = bool(
            shape["cells"].get("BeginX", {}).get("V")
            and shape["cells"].get("EndX", {}).get("V")
        ) or shape["cells"].get("ObjType", {}).get("V") == "2"

        if not is_1d_shape:
            # IX-based geometry section merge: local shape may override only
            # specific geometry sections (by IX).  Missing sections come from
            # the master, preserving the full shape geometry.
            local_by_section_ix = {g.get("ix", str(i)): g for i, g in enumerate(shape["geometry"])}
            merged_geos = []
            master_ixs_seen = set()
            # Track whether the merged result uses master-space coordinates
            # (needing scaling) or instance-space coordinates (no scaling).
            needs_master_scaling = False

            for mi, master_geo in enumerate(master_geos):
                mix = master_geo.get("ix", str(mi))
                master_ixs_seen.add(mix)
                if mix in local_by_section_ix:
                    local_geo = local_by_section_ix[mix]
                    local_rows = local_geo.get("rows", [])
                    master_rows = master_geo.get("rows", [])

                    # Build IX->row map for local row overrides
                    local_rows_by_ix = {}
                    for r in local_rows:
                        rix = r.get("ix", "")
                        if rix:
                            local_rows_by_ix[rix] = r

                    if local_rows_by_ix and len(local_rows) < len(master_rows):
                        # Partial row override -- merge master rows with local overrides
                        merged_rows = []
                        _has_master_only_rows = False
                        for mr in master_rows:
                            mrix = mr.get("ix", "")
                            if mrix and mrix in local_rows_by_ix:
                                lr = local_rows_by_ix[mrix]
                                merged_cells_r = dict(mr["cells"])
                                for cn, cv in lr["cells"].items():
                                    if cv.get("V"):
                                        merged_cells_r[cn] = cv
                                merged_row = {"type": lr["type"] or mr["type"],
                                              "cells": merged_cells_r,
                                              "ix": mrix}
                                merged_rows.append(merged_row)
                            else:
                                merged_rows.append(mr)
                                _has_master_only_rows = True
                        local_geo["rows"] = merged_rows
                        # Only need master scaling if there are rows that came
                        # entirely from master (in master coordinate space)
                        if _has_master_only_rows:
                            needs_master_scaling = True
                    else:
                        # Local has same or more rows than master — local
                        # geometry values are already in instance coordinate
                        # space (V values match instance Width/Height), so
                        # no master-to-instance scaling is needed.
                        pass
                    # Inherit NoFill/NoLine/NoShow from master if not set locally
                    for flag in ("no_fill", "no_line", "no_show"):
                        if not local_geo.get(flag) and master_geo.get(flag):
                            local_geo[flag] = master_geo[flag]
                    merged_geos.append(local_geo)
                else:
                    # Section not overridden locally -- use master section
                    merged_geos.append(master_geo)
                    needs_master_scaling = True

            # Add any local-only sections (IX not in master)
            for lix, lg in local_by_section_ix.items():
                if lix not in master_ixs_seen:
                    merged_geos.append(lg)

            shape["geometry"] = merged_geos
            # Only store master dims for scaling when the merged geometry
            # contains rows in master coordinate space (not instance space).
            if needs_master_scaling:
                master_w_val = master_sd.get("cells", {}).get("Width", {}).get("V")
                master_h_val = master_sd.get("cells", {}).get("Height", {}).get("V")
                if master_w_val:
                    shape["_master_w"] = _safe_float(master_w_val)
                if master_h_val:
                    shape["_master_h"] = _safe_float(master_h_val)

    # Merge text: use local if present, otherwise master
    if not shape["text"] and not shape.get("_has_text_elem") and master_sd.get("text") and shape.get("type") != "Group":
        txt = master_sd["text"]
        if txt not in ("Label", "Abc", "Table", "Entity", "Class"):
            shape["text"] = txt
            if not shape["text_parts"] and master_sd.get("text_parts"):
                shape["text_parts"] = master_sd["text_parts"]

    # Merge character and paragraph formats
    if not shape["char_formats"] and master_sd.get("char_formats"):
        shape["char_formats"] = master_sd["char_formats"]
    if not shape["para_formats"] and master_sd.get("para_formats"):
        shape["para_formats"] = master_sd["para_formats"]

    # Merge controls, connections, and user cells
    if not shape.get("controls") and master_sd.get("controls"):
        shape["controls"] = master_sd["controls"]
    if not shape.get("connections") and master_sd.get("connections"):
        shape["connections"] = master_sd["connections"]
    if not shape.get("user") and master_sd.get("user"):
        shape["user"] = master_sd["user"]

    # Merge foreign data (embedded images) from master
    if not shape.get("foreign_data") and master_sd.get("foreign_data"):
        shape["foreign_data"] = master_sd["foreign_data"]

    return shape


# ---------------------------------------------------------------------------
# Shape to SVG rendering
# ---------------------------------------------------------------------------

def _get_cell_val(shape: dict, name: str, default: str = "") -> str:
    """Get a cell value from a shape."""
    cell = shape.get("cells", {}).get(name, {})
    return cell.get("V", default)


def _get_cell_float(shape: dict, name: str, default: float = 0.0) -> float:
    """Get a cell value as float."""
    return _safe_float(_get_cell_val(shape, name), default)


def _resolve_quickstyle_color(qs_fill_color: int,
                              theme_colors: dict[str, str]) -> str:
    """Map QuickStyleFillColor index to a theme color.

    Visio QuickStyle indices:
      0=dk1, 1=lt1, 2=dk2, 3=lt2, 4=accent1, ..., 9=accent6
      100=dk1, 101=lt1, 102=dk2(tinted), 103-108=accent1-6(tinted)
    """
    _qs_map = {
        0: "dk1", 1: "lt1", 2: "dk2", 3: "lt2",
        4: "accent1", 5: "accent2", 6: "accent3",
        7: "accent4", 8: "accent5", 9: "accent6",
        100: "dk1", 101: "lt1", 102: "dk2",
        103: "accent1", 104: "accent2", 105: "accent3",
        106: "accent4", 107: "accent5", 108: "accent6",
    }
    name = _qs_map.get(qs_fill_color)
    if name and name in theme_colors:
        return theme_colors[name]
    # Default to accent1 for unknown values
    return theme_colors.get("accent1", "")


def _compute_transform(shape: dict, page_h: float) -> str:
    """Compute SVG transform for a shape.

    Handles PinX/PinY positioning, LocPinX/LocPinY, rotation, and flipping.
    Returns SVG transform attribute value.
    """
    pin_x = _get_cell_float(shape, "PinX") * _INCH_TO_PX
    pin_y = (page_h - _get_cell_float(shape, "PinY")) * _INCH_TO_PX
    w = _get_cell_float(shape, "Width")
    h = _get_cell_float(shape, "Height")
    # Default LocPinX/Y to center of shape if not specified (Visio default)
    _lpx_val = _get_cell_val(shape, "LocPinX")
    loc_pin_x = (_safe_float(_lpx_val) if _lpx_val else abs(w) * 0.5) * _INCH_TO_PX
    _lpy_val = _get_cell_val(shape, "LocPinY")
    loc_pin_y_raw = _safe_float(_lpy_val) if _lpy_val else abs(h) * 0.5
    loc_pin_y = (abs(h) - loc_pin_y_raw) * _INCH_TO_PX  # Flip Y for local pin

    angle = _get_cell_float(shape, "Angle")
    flip_x = _get_cell_val(shape, "FlipX") == "1"
    flip_y = _get_cell_val(shape, "FlipY") == "1"

    parts = []

    # Translate so pin point is at correct page position
    tx = pin_x - loc_pin_x
    ty = pin_y - loc_pin_y

    parts.append(f"translate({tx:.2f},{ty:.2f})")

    # Apply rotation around local pin
    if abs(angle) > 1e-6:
        angle_deg = -math.degrees(angle)  # Visio angles are CCW, SVG CW
        parts.append(f"rotate({angle_deg:.2f},{loc_pin_x:.2f},{loc_pin_y:.2f})")

    # Apply flips around local pin
    if flip_x or flip_y:
        sx = -1 if flip_x else 1
        sy = -1 if flip_y else 1
        # Translate to origin, scale, translate back
        parts.append(f"translate({loc_pin_x:.2f},{loc_pin_y:.2f})")
        parts.append(f"scale({sx},{sy})")
        parts.append(f"translate({-loc_pin_x:.2f},{-loc_pin_y:.2f})")

    return " ".join(parts)



def _eval_simple_formula(formula: str, shape: dict, default: float = 0.0) -> float:
    """Evaluate simple Visio ShapeSheet formulas.

    Handles: GUARD(expr), IF(cond,t,f), Width*N, Height*N, simple arithmetic.
    """
    if not formula:
        return default

    f = formula.strip()
    while f.upper().startswith("GUARD(") and f.endswith(")"):
        f = f[6:-1].strip()
    while f.upper().startswith("THEMEGUARD(") and f.endswith(")"):
        f = f[11:-1].strip()

    if_match = re.match(r"IF\s*\((.+),(.+),(.+)\)", f, re.IGNORECASE)
    if if_match:
        return _eval_simple_formula(if_match.group(2).strip(), shape, default)

    w = _get_cell_float(shape, "Width", 1.0)
    h = _get_cell_float(shape, "Height", 1.0)

    expr = f
    expr = re.sub(r"\bWidth\b", str(w), expr, flags=re.IGNORECASE)
    expr = re.sub(r"\bHeight\b", str(h), expr, flags=re.IGNORECASE)

    if "THEMEVAL" in expr.upper():
        return default

    if re.match(r"^[\d\s+\-*/.()]+$", expr):
        try:
            return float(eval(expr))
        except Exception:
            pass

    try:
        return float(f)
    except (ValueError, TypeError):
        return default


def _render_shape_svg(shape: dict, page_h: float, masters: dict,
                       parent_master_id: str = "",
                       _depth: int = 0,
                       media: dict | None = None,
                       page_rels: dict | None = None,
                       used_markers: set | None = None,
                       output_dir: str | None = None,
                       theme_colors: dict | None = None,
                       layers: dict | None = None,
                       gradients: dict | None = None,
                       has_shadow: set | None = None,
                       text_layer: list | None = None) -> list[str]:
    """Render a single shape as SVG elements. Returns list of SVG strings."""
    shape = _merge_shape_with_master(shape, masters, parent_master_id)
    if media is None:
        media = {}
    if page_rels is None:
        page_rels = {}
    if used_markers is None:
        used_markers = set()
    if theme_colors is None:
        theme_colors = {}
    if layers is None:
        layers = {}
    if gradients is None:
        gradients = {}
    if has_shadow is None:
        has_shadow = set()

    lines: list[str] = []
    # text_layer collects text SVG to render on top of all geometry
    # Only collect for top-level shapes (depth 0); sub-shapes render
    # text within their group transform to get correct positioning
    _collect_text = text_layer is not None and _depth == 0

    # Skip shapes that are invisible or purely connection/control metadata
    vis_val = _get_cell_val(shape, "Visible")
    if vis_val == "0":
        return lines

    # Layer visibility check
    layer_member = _get_cell_val(shape, "LayerMember")
    if layer_member and layers:
        # LayerMember can be "0", "1", "0;1" etc.
        layer_ids = [lm.strip() for lm in layer_member.split(";")]
        all_hidden = True
        for lid in layer_ids:
            layer_info = layers.get(lid, {})
            if layer_info.get("visible", True):
                all_hidden = False
                break
        if all_hidden:
            return lines

    # Skip shapes with only connection points and no geometry/text (connection markers)
    if (shape.get("connections") and not shape.get("geometry")
            and not shape.get("text") and not shape.get("sub_shapes")):
        return lines

    # Handle shape type
    shape_type = shape.get("type", "Shape")

    w_inch = _get_cell_float(shape, "Width")
    h_inch = _get_cell_float(shape, "Height")
    w_px = abs(w_inch) * _INCH_TO_PX
    h_px = abs(h_inch) * _INCH_TO_PX

    # --- Style ---
    line_weight = _get_cell_float(shape, "LineWeight", 0.01) * _INCH_TO_PX
    if line_weight < 0.5:
        line_weight = 1.5  # Minimum visible stroke width
    elif line_weight > 20:
        line_weight = 20

    line_color = _resolve_color(_get_cell_val(shape, "LineColor"), theme_colors) or "#333333"
    fill_foregnd = _resolve_color(_get_cell_val(shape, "FillForegnd"), theme_colors)
    fill_bkgnd = _resolve_color(_get_cell_val(shape, "FillBkgnd"), theme_colors)

    # Also try resolving via formula if value is a color index
    _ff_formula = shape.get("cells", {}).get("FillForegnd", {}).get("F", "")
    _fb_formula = shape.get("cells", {}).get("FillBkgnd", {}).get("F", "")
    _lc_formula = shape.get("cells", {}).get("LineColor", {}).get("F", "")

    # Resolve THEMEVAL formulas and QuickStyle colors from theme
    qs_fill_color_val = _get_cell_val(shape, "QuickStyleFillColor")
    if theme_colors and qs_fill_color_val:
        qs_fill_color = int(_safe_float(qs_fill_color_val, -1))
        _theme_fill = _resolve_quickstyle_color(qs_fill_color, theme_colors) if qs_fill_color >= 0 else ""

        # When FillForegnd has THEMEVAL("FillColor",...), resolve from theme
        if _ff_formula and "THEMEVAL" in _ff_formula and "FillColor" in _ff_formula:
            if _theme_fill:
                fill_foregnd = _theme_fill
        # When FillBkgnd has THEMEVAL("FillColor2",...), resolve from theme
        if _fb_formula and "THEMEVAL" in _fb_formula and "FillColor2" in _fb_formula:
            if _theme_fill:
                fill_bkgnd = _lighten_color(_theme_fill, 0.85)

        # When FillForegnd is completely absent but QuickStyleFillColor exists,
        # the shape relies entirely on theme for its fill color
        if not fill_foregnd and not _ff_formula and _theme_fill and not _is_black(_theme_fill):
            fill_foregnd = _theme_fill

    # GUARD(color_index) in Visio stencils are theme accent placeholders.
    # Replace magenta (#FF00FF, color 6) with theme accent or sensible default.
    _default_accent = "#5B9BD5"  # Visio default accent blue
    if "GUARD" in _ff_formula and fill_foregnd == "#FF00FF":
        fill_foregnd = theme_colors.get("accent1", _default_accent)
    if "GUARD" in _fb_formula and fill_bkgnd == "#FF00FF":
        fill_bkgnd = theme_colors.get("accent1", _default_accent)

    # When THEMEVAL formula resolves to black (color index 0) but we have no
    # theme colors, the shape likely wants a theme-derived color, not black.
    # Use Visio's default accent blue as fallback.
    if _ff_formula and "THEMEVAL" in _ff_formula and _is_black(fill_foregnd):
        fill_foregnd = theme_colors.get("accent1", _default_accent)
    if _fb_formula and "THEMEVAL" in _fb_formula and _is_black(fill_bkgnd):
        fill_bkgnd = theme_colors.get("accent1", _default_accent)

    # GUARD(0) in stencils: color index 0 = black, but in stencil context
    # this is a theme placeholder. Replace with accent color.
    if "GUARD" in _ff_formula and _is_black(fill_foregnd):
        fill_foregnd = theme_colors.get("accent1", _default_accent)
    if "GUARD" in _fb_formula and _is_black(fill_bkgnd):
        fill_bkgnd = theme_colors.get("accent1", _default_accent)

    # If fill colors are still empty but formulas reference theme, use accent1
    if not fill_foregnd and _ff_formula and ("THEME" in _ff_formula or "GUARD" in _ff_formula):
        fill_foregnd = theme_colors.get("accent1", _default_accent)
    if not fill_bkgnd and _fb_formula and ("THEME" in _fb_formula or "GUARD" in _fb_formula):
        fill_bkgnd = theme_colors.get("accent1", _default_accent)

    # Handle F="Inh" (inherited from theme) — if value couldn't be resolved,
    # use theme colors as fallback
    if not fill_foregnd and _ff_formula == "Inh" and theme_colors:
        fill_foregnd = theme_colors.get("accent1", "")
    if not fill_bkgnd and _fb_formula == "Inh" and theme_colors:
        fill_bkgnd = theme_colors.get("accent1", "")
    if _lc_formula and (_lc_formula == "Inh" or "THEME" in _lc_formula):
        if theme_colors:
            line_color = theme_colors.get("dk1", line_color)

    # QuickStyleLineColor — resolve line color from theme
    qs_line_color_val = _get_cell_val(shape, "QuickStyleLineColor")
    if theme_colors and qs_line_color_val:
        qs_line_color = int(_safe_float(qs_line_color_val, -1))
        if qs_line_color >= 0:
            _theme_line = _resolve_quickstyle_color(qs_line_color, theme_colors)
            if _theme_line and (_lc_formula and "THEMEVAL" in _lc_formula):
                line_color = _theme_line

    # QuickStyleFontColor — resolve text color from theme  
    qs_font_color_val = _get_cell_val(shape, "QuickStyleFontColor")
    if theme_colors and qs_font_color_val:
        qs_font_color = int(_safe_float(qs_font_color_val, -1))
        if qs_font_color >= 0:
            _theme_font = _resolve_quickstyle_color(qs_font_color, theme_colors)
            if _theme_font:
                # Store for text rendering
                shape["_theme_text_color"] = _theme_font
        elif "THEMEVAL" in _lc_formula and _is_black(line_color):
            # THEMEVAL line color defaulting to black — use dark accent instead
            line_color = "#1F477D"  # Dark blue, matches Visio default

    fill_pattern = _get_cell_val(shape, "FillPattern", "1")
    line_pattern = int(_safe_float(_get_cell_val(shape, "LinePattern", "1")))
    rounding = _get_cell_float(shape, "Rounding") * _INCH_TO_PX

    # Determine fill
    fill_pat_int = int(_safe_float(fill_pattern, 1))
    if fill_pat_int == 0:
        fill = "none"
    elif fill_pat_int == 1:
        # Solid fill
        fill = fill_foregnd or fill_bkgnd or "none"
    elif 25 <= fill_pat_int <= 40:
        # Gradient fill — Visio gradients go from FillBkgnd to FillForegnd
        start_color = fill_bkgnd or "#FFFFFF"
        end_color = fill_foregnd or fill_bkgnd or "#CCCCCC"
        # If both colors are the same, use a solid fill instead of gradient
        if start_color.upper() == end_color.upper():
            fill = start_color
        else:
            # If both are the same or start is white, create a visible gradient
            if start_color == end_color and end_color != "#FFFFFF":
                start_color = _lighten_color(end_color, 0.7)
            grad_dir = _get_cell_float(shape, "FillGradientDir")
            # Map Visio gradient direction to angle
            if grad_dir:
                grad_angle = grad_dir * 45
            else:
                # Map FillPattern to gradient direction when FillGradientDir
                # is not set (classic Visio gradient patterns)
                _pattern_angles = {
                    25: 0,    # Linear left-to-right
                    26: 90,   # Linear top-to-bottom
                    27: 45,   # Diagonal top-left to bottom-right
                    28: 315,  # Diagonal bottom-left to top-right
                    29: 0,    # Linear from center (radial approx)
                    30: 90,   # Vertical from center
                    33: 0,    # Horizontal
                    34: 90,   # Vertical
                    35: 45,   # Diagonal
                    36: 315,  # Reverse diagonal
                    40: 0,    # Horizontal
                }
                grad_angle = _pattern_angles.get(fill_pat_int, 0)
            grad_id = f"grad_{shape['id']}_{fill_pat_int}"
            is_radial = fill_pat_int in (29, 30, 31, 32, 37, 38, 39)
            gradients[grad_id] = {
                "start": start_color, "end": end_color,
                "dir": grad_angle, "radial": is_radial
            }
            fill = f"url(#{grad_id})"
    elif fill_pat_int >= 2:
        # Unknown pattern — approximate with blend
        if fill_bkgnd and not _is_black(fill_bkgnd):
            fill = fill_bkgnd
        elif fill_foregnd and not _is_black(fill_foregnd):
            fill = _lighten_color(fill_foregnd, 0.7)
        else:
            fill = "none"
    else:
        fill = "none"

    # Container detection
    is_container = False
    user_data = shape.get("user", {})
    structure_type = user_data.get("msvStructureType", {}).get("Value", "")
    if structure_type == "Container":
        is_container = True
    shape_name = (shape.get("name_u", "") or shape.get("name", "")).lower()
    if any(kw in shape_name for kw in ("dash square", "container", "swimlane")):
        is_container = True

    # Shadow support — per-shape shadow with offset, color, transparency
    shdw_pattern = _get_cell_val(shape, "ShdwPattern")
    shape_has_shadow = shdw_pattern and shdw_pattern != "0"
    shadow_attr = ""
    if shape_has_shadow:
        has_shadow.add("shadow")
        shadow_attr = ' filter="url(#shadow)"'

    # No line if pattern 0
    stroke = line_color if line_pattern != 0 else "none"
    stroke_width = line_weight

    dash_array = _get_dash_array(line_pattern, stroke_width)

    # Fill opacity from FillForegndTrans (0=opaque, 1=transparent)
    fill_trans = _get_cell_float(shape, "FillForegndTrans")
    if fill_trans > 0 and fill_trans <= 1:
        fill_opacity = 1.0 - fill_trans
    elif fill_trans > 1:
        fill_opacity = 1.0 - (fill_trans / 100.0)  # percentage
    else:
        fill_opacity = 1.0

    # Container style — ensure visibility but respect actual styles
    if is_container:
        if fill == "none" and not fill_foregnd:
            fill = "#F8F8F8"
            fill_opacity = 0.5
        elif fill != "none" and fill_opacity > 0.9:
            # Containers with opaque fills should be semi-transparent
            # so contents are visible
            fill_opacity = max(0.3, fill_opacity * 0.5)
        if not dash_array and line_pattern <= 1:
            dash_array = "8,4"
        if stroke == "none":
            stroke = "#AAAAAA"

    # Line transparency
    line_trans = _get_cell_float(shape, "LineColorTrans")
    if line_trans > 0 and line_trans <= 1:
        stroke_opacity = 1.0 - line_trans
    elif line_trans > 1:
        stroke_opacity = 1.0 - (line_trans / 100.0)
    else:
        stroke_opacity = 1.0

    # Build style string
    style_parts = [
        f'fill="{fill}"',
        f'stroke="{stroke}"',
        f'stroke-width="{stroke_width:.2f}"',
    ]
    if fill_opacity < 0.99:
        style_parts.append(f'fill-opacity="{fill_opacity:.2f}"')
    if stroke_opacity < 0.99:
        style_parts.append(f'stroke-opacity="{stroke_opacity:.2f}"')
    if dash_array:
        style_parts.append(f'stroke-dasharray="{dash_array}"')

    style_str = " ".join(style_parts)

    # --- Check for 1D shape (connector/line) ---
    begin_y = _get_cell_val(shape, "BeginY")
    end_y = _get_cell_val(shape, "EndY")

    # --- Check for 1D connector groups ---
    # Some shapes (e.g., BPMN Sequence Flow) are Group type but also 1D connectors
    # Render them as connectors if they have BeginX/EndX
    begin_x = _get_cell_val(shape, "BeginX")
    end_x = _get_cell_val(shape, "EndX")
    is_1d = bool(begin_x and end_x)
    obj_type = _get_cell_val(shape, "ObjType")
    is_1d_group = (shape_type == "Group" or bool(shape.get("sub_shapes"))) and is_1d

    # --- Group shapes ---
    if (shape_type == "Group" or shape.get("sub_shapes")) and not is_1d_group:
        transform = _compute_transform(shape, page_h)
        group_master_id = shape.get("master", "") or parent_master_id
        # Group's local coordinate system uses its own Width x Height
        group_h = h_inch

        # If group has no Width/Height, estimate from sub-shapes.
        # Sub-shapes may not yet be merged with master, so also check
        # corresponding master sub-shapes for PinX/PinY/Width/Height.
        if (abs(group_h) < 1e-6 or abs(w_inch) < 1e-6) and shape.get("sub_shapes"):
            master_shapes_map = masters.get(group_master_id, {}) if masters else {}
            max_sub_x = 0.0
            max_sub_y = 0.0
            for sub in shape["sub_shapes"]:
                sub_cells = sub.get("cells", {})
                # Try page sub-shape cells first, fall back to master
                sub_px = _safe_float(sub_cells.get("PinX", {}).get("V"))
                sub_py = _safe_float(sub_cells.get("PinY", {}).get("V"))
                sub_w = abs(_safe_float(sub_cells.get("Width", {}).get("V")))
                sub_h = abs(_safe_float(sub_cells.get("Height", {}).get("V")))
                if sub_px == 0.0 and sub_py == 0.0 and not sub_cells:
                    # Sub-shape has no cells — look up master sub-shape
                    ms_id = sub.get("master_shape", "")
                    ms = master_shapes_map.get(ms_id)
                    if ms:
                        mc = ms.get("cells", {})
                        sub_px = _safe_float(mc.get("PinX", {}).get("V"))
                        sub_py = _safe_float(mc.get("PinY", {}).get("V"))
                        sub_w = abs(_safe_float(mc.get("Width", {}).get("V")))
                        sub_h = abs(_safe_float(mc.get("Height", {}).get("V")))
                max_sub_x = max(max_sub_x, sub_px + sub_w / 2)
                max_sub_y = max(max_sub_y, sub_py + sub_h / 2)
            if max_sub_y > 0 and abs(group_h) < 1e-6:
                group_h = max_sub_y
                h_inch = group_h
                h_px = abs(group_h) * _INCH_TO_PX
            if max_sub_x > 0 and abs(w_inch) < 1e-6:
                w_inch = max_sub_x
                w_px = abs(w_inch) * _INCH_TO_PX

        # Apply clipping only for large groups (containers/swimlanes),
        # not for small stencil/icon groups where sub-shapes may extend
        # slightly beyond the nominal group bounds.
        _has_text_subs = any(sub.get("text") for sub in shape.get("sub_shapes", []))
        use_clip = w_px > 300 and h_px > 200 and not _has_text_subs
        clip_attr = ""
        if use_clip:
            clip_id = f"clip_{shape['id']}"
            # Add small padding (5%) to avoid cutting off edges
            pad_x = w_px * 0.12
            pad_y = h_px * 0.12
            lines.append(
                f'<defs><clipPath id="{clip_id}">'
                f'<rect x="{-pad_x:.2f}" y="{-pad_y:.2f}" '
                f'width="{w_px + 2*pad_x:.2f}" height="{h_px + 2*pad_y:.2f}"/>'
                f'</clipPath></defs>'
            )
            clip_attr = f' clip-path="url(#{clip_id})"'
        lines.append(
            f'<g transform="{transform}"{clip_attr}{shadow_attr}>'
        )
        # Render the group's own geometry (if any)
        if shape["geometry"]:
            master_w = shape.get("_master_w", 0.0)
            master_h = shape.get("_master_h", 0.0)
            for geo in shape["geometry"]:
                path_d = _geometry_to_path(geo, w_inch, h_inch, master_w, master_h)
                if not path_d:
                    continue
                geo_fill = fill
                geo_stroke = stroke
                if geo.get("no_fill"):
                    geo_fill = "none"
                # Detect open paths: if start != end, don't fill
                # (SVG auto-closes filled paths, creating visual artifacts)
                if geo_fill != "none" and "Z" not in path_d:
                    _coords = re.findall(r'[-+]?[\d.]+', path_d)
                    if len(_coords) >= 4:
                        _sx, _sy = float(_coords[0]), float(_coords[1])
                        _ex, _ey = float(_coords[-2]), float(_coords[-1])
                        if abs(_sx - _ex) > 2.0 or abs(_sy - _ey) > 2.0:
                            geo_fill = "none"
                if geo.get("no_line"):
                    geo_stroke = "none"
                geo_style = (
                    f'fill="{geo_fill}" stroke="{geo_stroke}" '
                    f'stroke-width="{stroke_width:.2f}"'
                )
                if fill_opacity < 0.99 and geo_fill != "none":
                    geo_style += f' fill-opacity="{fill_opacity:.2f}"'
                if stroke_opacity < 0.99 and geo_stroke != "none":
                    geo_style += f' stroke-opacity="{stroke_opacity:.2f}"'
                if dash_array:
                    geo_style += f' stroke-dasharray="{dash_array}"'
                lines.append(f'<path d="{path_d}" {geo_style}{shadow_attr}/>')
        # Render embedded image for the group
        fd = shape.get("foreign_data")
        if fd and media:
            img_href = None
            if fd.get("rel_id") and fd["rel_id"] in page_rels:
                target = page_rels[fd["rel_id"]]
                img_name = target.split("/")[-1]
                if img_name in media:
                    img_href = _image_to_data_uri(media[img_name], img_name)
            if img_href:
                img_w_px = w_px
                img_h_px = h_px
                # Enforce minimum icon size
                if img_w_px > 0 and img_h_px > 0:
                    if img_w_px < 24 or img_h_px < 24:
                        scale = max(24.0 / img_w_px, 24.0 / img_h_px)
                        img_w_px *= scale
                        img_h_px *= scale
                # Use ImgOffsetX/Y for correct positioning within group
                img_off_x = _get_cell_float(shape, "ImgOffsetX") * _INCH_TO_PX
                img_off_y = _get_cell_float(shape, "ImgOffsetY") * _INCH_TO_PX
                lines.append(
                    f'<image x="{img_off_x:.2f}" y="{img_off_y:.2f}" '
                    f'width="{img_w_px:.2f}" height="{img_h_px:.2f}" '
                    f'xlink:href="{img_href}" '
                    f'preserveAspectRatio="xMidYMid meet"/>'
                )
        for sub in shape.get("sub_shapes", []):
            lines.extend(_render_shape_svg(
                sub, group_h, masters, group_master_id, _depth + 1,
                media, page_rels, used_markers, output_dir,
                theme_colors, layers, gradients, has_shadow,
                text_layer=text_layer))
        lines.append('</g>')
        # Render text for the group itself (but not auto-generated name labels
        # for groups — sub-shapes already provide visible content)
        if shape["text"]:
            if _collect_text:
                _append_text_svg(text_layer, shape, page_h, w_px, h_px, theme_colors) # type: ignore[arg-type]
            else:
                _append_text_svg(lines, shape, page_h, w_px, h_px, theme_colors)
        return lines

    # --- Compute transform ---
    transform = _compute_transform(shape, page_h)

    # --- Geometry rendering ---
    has_geometry = bool(shape["geometry"])

    # If ALL geometry sections are NoShow, it's likely a conditional-visibility
    # master (e.g., mind map Topic shapes). Force-show the last section that
    # looks like a basic shape outline (has MoveTo + LineTo rows).
    if has_geometry and all(g.get("no_show") for g in shape["geometry"]):
        # Find the best fallback geometry section
        _fallback_geo = None
        for _fg in reversed(shape["geometry"]):
            _row_types = {r["type"] for r in _fg.get("rows", [])}
            if "MoveTo" in _row_types and ("LineTo" in _row_types or "ArcTo" in _row_types):
                _fallback_geo = _fg
                break
        if _fallback_geo is None and shape["geometry"]:
            # Use any section with rows
            for _fg in reversed(shape["geometry"]):
                if _fg.get("rows"):
                    _fallback_geo = _fg
                    break
        if _fallback_geo:
            _fallback_geo["no_show"] = False

    # For 1D connectors, use dedicated rendering even if they have master geometry
    is_connector = is_1d or obj_type == "2"

    if is_connector and is_1d:
        # Ensure connector lines are visible (minimum 2.0px)
        if stroke_width < 1.0:
            stroke_width = 1.5
        # 1D shape — check for geometry (routed connectors) first
        bx = _safe_float(begin_x) * _INCH_TO_PX
        by = (page_h - _safe_float(begin_y)) * _INCH_TO_PX
        ex_px = _safe_float(end_x) * _INCH_TO_PX
        ey_px = (page_h - _safe_float(end_y)) * _INCH_TO_PX

        # Arrow markers
        begin_arrow = int(_safe_float(_get_cell_val(shape, "BeginArrow", "0")))
        end_arrow = int(_safe_float(_get_cell_val(shape, "EndArrow", "0")))
        # Default to an arrow if the shape looks like a connector and has no
        # EndArrow cell at all. Check ObjType=2 or name contains "connector".
        _is_named_connector = "connector" in shape_name
        if end_arrow == 0 and (obj_type == "2" or _is_named_connector):
            _ea_cell = shape.get("cells", {}).get("EndArrow", {})
            if not _ea_cell or (not _ea_cell.get("V") and not _ea_cell.get("F")):
                end_arrow = 4
        begin_arrow_size = int(_safe_float(_get_cell_val(shape, "BeginArrowSize", "2")))
        end_arrow_size = int(_safe_float(_get_cell_val(shape, "EndArrowSize", "2")))
        marker_color = stroke.lstrip("#") if stroke != "none" else "333333"
        marker_attrs = ""
        if begin_arrow > 0:
            mid = f"arrow_start_{begin_arrow_size}_{marker_color}"
            used_markers.add(mid)
            marker_attrs += f' marker-start="url(#{mid})"'
        if end_arrow > 0:
            mid = f"arrow_end_{end_arrow_size}_{marker_color}"
            used_markers.add(mid)
            marker_attrs += f' marker-end="url(#{mid})"'

        # Convert connector geometry to page-coordinate polyline
        # For connectors without PinX, skip geometry and use BeginX→EndX directly
        if has_geometry and "PinX" not in shape.get("cells", {}):
            has_geometry = False  # Force straight line fallback
        if has_geometry:
            # Build polyline from geometry rows in page coordinates
            # Transform local geo coords to page space using PinX/PinY
            pin_x = _get_cell_float(shape, "PinX")
            pin_y = _get_cell_float(shape, "PinY")
            loc_pin_x = _get_cell_float(shape, "LocPinX")
            loc_pin_y = _get_cell_float(shape, "LocPinY")
            angle = _get_cell_float(shape, "Angle")
            cos_a = math.cos(angle) if abs(angle) > 1e-6 else 1.0
            sin_a = math.sin(angle) if abs(angle) > 1e-6 else 0.0

            points = []
            has_move_to = False
            for geo in shape["geometry"]:
                if geo.get("no_show"):
                    continue
                for row in geo["rows"]:
                    rt = row["type"]
                    cells = row["cells"]
                    if rt in ("MoveTo", "LineTo", "ArcTo",
                              "EllipticalArcTo", "NURBSTo",
                              "SplineStart", "SplineKnot"):
                        x_val = cells.get("X", {}).get("V")
                        y_val = cells.get("Y", {}).get("V")
                        # Skip rows with incomplete coordinates — both X
                        # and Y must be present for a valid connector point
                        if not x_val and x_val != "0":
                            continue
                        if not y_val and y_val != "0":
                            continue
                        if rt == "MoveTo":
                            has_move_to = True
                        lx = _safe_float(x_val)
                        ly = _safe_float(y_val)
                        # Local to page: translate by pin offset
                        dx = lx - loc_pin_x
                        dy = ly - loc_pin_y
                        px = pin_x + dx * cos_a - dy * sin_a
                        py = pin_y + dx * sin_a + dy * cos_a
                        # To SVG pixels
                        sx_px = px * _INCH_TO_PX
                        sy_px = (page_h - py) * _INCH_TO_PX
                        points.append((sx_px, sy_px))

            # If geometry had no MoveTo, insert the begin point as start
            if points and not has_move_to:
                points.insert(0, (bx, by))

            if len(points) >= 2:
                d_parts = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
                for pt in points[1:]:
                    d_parts.append(f"L {pt[0]:.2f} {pt[1]:.2f}")
                path_d = " ".join(d_parts)
                lines.append(
                    f'<path d="{path_d}" fill="none" stroke="{stroke}" '
                    f'stroke-width="{stroke_width:.2f}"'
                    + (f' stroke-dasharray="{dash_array}"' if dash_array else '')
                    + marker_attrs
                    + '/>'
                )
            else:
                # Fallback to straight line
                lines.append(
                    f'<line x1="{bx:.2f}" y1="{by:.2f}" x2="{ex_px:.2f}" y2="{ey_px:.2f}" '
                    f'stroke="{stroke}" stroke-width="{stroke_width:.2f}"'
                    + (f' stroke-dasharray="{dash_array}"' if dash_array else '')
                    + marker_attrs
                    + '/>'
                )
        else:
            # No geometry — simple straight line
            lines.append(
                f'<line x1="{bx:.2f}" y1="{by:.2f}" x2="{ex_px:.2f}" y2="{ey_px:.2f}" '
                f'stroke="{stroke}" stroke-width="{stroke_width:.2f}"'
                + (f' stroke-dasharray="{dash_array}"' if dash_array else '')
                + marker_attrs
                + '/>'
            )

    elif has_geometry:
        # 2D shape with geometry
        master_w = shape.get("_master_w", 0.0)
        master_h = shape.get("_master_h", 0.0)
        for geo in shape["geometry"]:
            path_d = _geometry_to_path(geo, w_inch, h_inch, master_w, master_h)
            if not path_d:
                continue

            geo_fill = fill
            geo_stroke = stroke
            if geo.get("no_fill"):
                geo_fill = "none"
            if geo.get("no_line"):
                geo_stroke = "none"

            geo_style = (
                f'fill="{geo_fill}" stroke="{geo_stroke}" '
                f'stroke-width="{stroke_width:.2f}"'
            )
            if fill_opacity < 0.99 and geo_fill != "none":
                geo_style += f' fill-opacity="{fill_opacity:.2f}"'
            if stroke_opacity < 0.99 and geo_stroke != "none":
                geo_style += f' stroke-opacity="{stroke_opacity:.2f}"'
            if dash_array:
                geo_style += f' stroke-dasharray="{dash_array}"'

            lines.append(
                f'<path d="{path_d}" {geo_style}{shadow_attr} '
                f'transform="{transform}"/>'
            )

    else:
        # No geometry, no 1D — fall back to outlined rectangle
        if w_px > 0 and h_px > 0 and (fill != "none" or shape.get("text")):
            rx_val = max(rounding, 4.0)  # Slightly rounded for aesthetics
            rx_attr = f' rx="{rx_val:.2f}"'
            # For fallback shapes, prefer outlined rectangle over filled
            fallback_fill = fill if fill != "none" else "#FAFAFA"
            fallback_stroke = stroke if stroke != "none" else (
                _resolve_color(_get_cell_val(shape, "LineColor"), theme_colors)
                or "#CCCCCC"
            )
            fallback_style = (
                f'fill="{fallback_fill}" stroke="{fallback_stroke}" '
                f'stroke-width="{max(stroke_width, 0.75):.2f}"'
            )
            if fill_opacity < 0.99 and fallback_fill != "none":
                fallback_style += f' fill-opacity="{fill_opacity:.2f}"'
            if stroke_opacity < 0.99 and fallback_stroke != "none":
                fallback_style += f' stroke-opacity="{stroke_opacity:.2f}"'
            if dash_array:
                fallback_style += f' stroke-dasharray="{dash_array}"'
            lines.append(
                f'<rect x="0" y="0" width="{w_px:.2f}" height="{h_px:.2f}" '
                f'{fallback_style}{rx_attr} transform="{transform}"/>'
            )
            # If shape has no text but has a name, show name as label
            # Only for top-level shapes (depth 0), not sub-shapes in stencils
            if not shape.get("text") and not shape.get("_has_text_elem") and _depth == 0:
                shape_label = shape.get("name_u", "") or shape.get("name", "")
                if shape_label and not shape_label.startswith("Sheet."):
                    # Clean up generic names
                    shape_label = shape_label.split(".")[-1] if "." in shape_label else shape_label
                    if shape_label and len(shape_label) < 30 and not shape_label.isdigit():
                        shape["text"] = shape_label

    # --- Embedded image rendering ---
    fd = shape.get("foreign_data")
    if fd and media:
        img_href = None
        if fd.get("rel_id") and fd["rel_id"] in page_rels:
            target = page_rels[fd["rel_id"]]
            img_name = target.split("/")[-1]
            if img_name in media:
                # Always use data URIs — cairosvg doesn't resolve file paths
                img_href = _image_to_data_uri(media[img_name], img_name)
        elif fd.get("data"):
            ext_map = {"PNG": ".png", "JPEG": ".jpeg", "BMP": ".bmp",
                       "GIF": ".gif", "TIFF": ".tiff"}
            comp = fd.get("compression", "PNG").upper()
            fake_ext = ext_map.get(comp, ".png")
            try:
                raw = base64.b64decode(fd["data"])
                fname = f"inline_{shape['id']}{fake_ext}"
                img_href = _image_to_data_uri(raw, fname)
            except Exception:
                pass

        if img_href:
            img_w = _get_cell_float(shape, "ImgWidth") or w_inch
            img_h = _get_cell_float(shape, "ImgHeight") or h_inch
            img_off_x = _get_cell_float(shape, "ImgOffsetX")
            img_off_y = _get_cell_float(shape, "ImgOffsetY")
            img_w_px = img_w * _INCH_TO_PX
            img_h_px = img_h * _INCH_TO_PX
            # Enforce minimum icon size of 24x24px
            if img_w_px > 0 and img_h_px > 0:
                if img_w_px < 24 or img_h_px < 24:
                    scale = max(24.0 / img_w_px, 24.0 / img_h_px)
                    img_w_px *= scale
                    img_h_px *= scale
            img_x_px = img_off_x * _INCH_TO_PX
            img_y_px = img_off_y * _INCH_TO_PX
            lines.append(
                f'<image x="{img_x_px:.2f}" y="{img_y_px:.2f}" '
                f'width="{img_w_px:.2f}" height="{img_h_px:.2f}" '
                f'xlink:href="{img_href}" '
                f'preserveAspectRatio="xMidYMid meet" '
                f'transform="{transform}"/>'
            )

    # --- Text rendering ---
    if shape["text"]:
        if _collect_text:
            _append_text_svg(text_layer, shape, page_h, w_px, h_px, theme_colors) # type: ignore[arg-type]
        else:
            _append_text_svg(lines, shape, page_h, w_px, h_px, theme_colors)

    # No fallback rectangle for shapes inside groups (sub-shapes)
    # is handled by skipping the else branch when geometry/1D absent
    # and the shape has no meaningful content

    return lines


# Font family mapping: Visio font names -> SVG-compatible font stacks
_FONT_MAP = {
    "angsana new": "Noto Sans Thai, Noto Serif Thai, sans-serif",
    "browallia new": "Noto Sans Thai, sans-serif",
    "cordia new": "Noto Sans Thai, sans-serif",
    "freesia upc": "Noto Sans Thai, sans-serif",
    "tahoma": "Tahoma, Noto Sans, sans-serif",
    "arial": "Arial, Noto Sans, sans-serif",
    "calibri": "Calibri, Noto Sans, sans-serif",
    "segoe ui": "Segoe UI, Noto Sans, sans-serif",
    "times new roman": "Times New Roman, Noto Serif, serif",
    "ms gothic": "Noto Sans JP, sans-serif",
    "ms mincho": "Noto Serif JP, serif",
    "simsun": "Noto Sans SC, sans-serif",
    "simhei": "Noto Sans SC, sans-serif",
    "microsoft yahei": "Noto Sans SC, sans-serif",
    "malgun gothic": "Noto Sans KR, sans-serif",
    "gulim": "Noto Sans KR, sans-serif",
}

def _map_font_family(font_name: str) -> str:
    """Map a Visio font name to an SVG-compatible font-family string."""
    if not font_name or font_name == "Themed":
        return "Noto Sans, sans-serif"
    key = font_name.lower().strip()
    if key in _FONT_MAP:
        return _FONT_MAP[key]
    # Keep original font with fallbacks
    return f"{font_name}, Noto Sans, sans-serif"


def _append_text_svg(lines: list, shape: dict, page_h: float,
                     w_px: float, h_px: float,
                     theme_colors: dict | None = None):
    """Append SVG text elements for a shape's text.

    Uses clipPath to constrain text within shape bounding box.
    Supports multi-format text (text_parts with different cp indices).
    """
    text = shape["text"]
    if not text:
        return

    # Text position
    pin_x = _get_cell_float(shape, "PinX") * _INCH_TO_PX
    pin_y = (page_h - _get_cell_float(shape, "PinY")) * _INCH_TO_PX
    w_inch = _get_cell_float(shape, "Width")
    h_inch = _get_cell_float(shape, "Height")
    loc_pin_x = _get_cell_float(shape, "LocPinX") * _INCH_TO_PX
    loc_pin_y = _get_cell_float(shape, "LocPinY") * _INCH_TO_PX
    # Default LocPin to center of shape when not explicitly set
    if loc_pin_x == 0 and "LocPinX" not in shape.get("cells", {}):
        loc_pin_x = abs(w_inch) * 0.5 * _INCH_TO_PX
    if loc_pin_y == 0 and "LocPinY" not in shape.get("cells", {}):
        loc_pin_y = abs(h_inch) * 0.5 * _INCH_TO_PX

    # Detect 1D connector shapes - offset text above the line
    _is_1d_shape = bool(_get_cell_val(shape, "BeginX") and _get_cell_val(shape, "EndX"))

    # Text block offset
    txt_pin_x = _get_cell_float(shape, "TxtPinX")
    txt_pin_y = _get_cell_float(shape, "TxtPinY")

    # Calculate text center in page coordinates
    # Use TxtPinX/Y when explicitly set (including negative values for below-shape labels)
    _has_txt_pin = ("TxtPinX" in shape.get("cells", {}) or
                    "TxtPinY" in shape.get("cells", {}))
    if _has_txt_pin or txt_pin_x != 0 or txt_pin_y != 0:
        shape_left = pin_x - loc_pin_x
        shape_top = pin_y - (abs(h_inch) * _INCH_TO_PX - loc_pin_y)
        _eff_txt_pin_x = txt_pin_x if txt_pin_x != 0 else abs(w_inch) * 0.5
        _eff_txt_pin_y = txt_pin_y
        tx = shape_left + _eff_txt_pin_x * _INCH_TO_PX
        ty = shape_top + (abs(h_inch) - _eff_txt_pin_y) * _INCH_TO_PX
    else:
        # Default to shape center
        shape_left = pin_x - loc_pin_x
        shape_top = pin_y - (abs(h_inch) * _INCH_TO_PX - loc_pin_y)
        tx = shape_left + abs(w_inch) * _INCH_TO_PX * 0.5
        ty = shape_top + abs(h_inch) * _INCH_TO_PX * 0.5

    # Get text formatting — support multi-format text via text_parts
    char_formats = shape.get("char_formats", {})
    char_fmt = char_formats.get("0", {})
    font_size = _safe_float(char_fmt.get("Size"), 0.1111) * _INCH_TO_PX
    if font_size < 6:
        font_size = 8
    elif font_size > 72:
        font_size = 72

    text_color = _resolve_color(char_fmt.get("Color", ""), theme_colors) or "#000000"
    # Use theme text color if available and char color is default
    if text_color == "#000000" and shape.get("_theme_text_color"):
        text_color = shape["_theme_text_color"]
    # Auto-contrast: ensure text is readable against fill
    if _is_dark_color(text_color):
        shape_fill = shape.get("cells", {}).get("FillForegnd", {}).get("V", "")
        resolved_fill = _resolve_color(shape_fill, theme_colors) if shape_fill else ""
        # Also check QuickStyleFillColor for theme-based fills
        if not resolved_fill:
            qs_fc = shape.get("cells", {}).get("QuickStyleFillColor", {}).get("V", "")
            if qs_fc and theme_colors:
                qs_idx = int(_safe_float(qs_fc, -1))
                if qs_idx >= 0:
                    resolved_fill = _resolve_quickstyle_color(qs_idx, theme_colors) or ""
        if resolved_fill and _is_dark_color(resolved_fill):
            text_color = "#FFFFFF"
    font_name = char_fmt.get("Font", "")
    font_family = _map_font_family(font_name)
    style_bits = int(_safe_float(char_fmt.get("Style", "0")))
    is_bold = bool(style_bits & 1)
    is_italic = bool(style_bits & 2)
    is_underline = bool(style_bits & 4)

    # Paragraph alignment
    para_fmt = shape.get("para_formats", {}).get("0", {})
    halign = int(_safe_float(para_fmt.get("HorzAlign", "1")))
    anchor_map = {0: "start", 1: "middle", 2: "end"}
    text_anchor = anchor_map.get(halign, "middle")

    # Adjust tx for non-center horizontal alignment
    shape_left = pin_x - loc_pin_x
    _text_pad = font_size * 0.4 if font_size > 0 else 4
    if halign == 0:
        # Left-aligned: position at left edge + padding
        tx = shape_left + _text_pad
    elif halign == 2:
        # Right-aligned: position at right edge - padding
        tx = shape_left + w_px - _text_pad

    # Vertical alignment (0=top, 1=middle, 2=bottom)
    vert_align = int(_safe_float(_get_cell_val(shape, "VerticalAlign", "1")))

    # Text rotation
    txt_angle = _get_cell_float(shape, "TxtAngle")
    txt_rotate = ""
    if abs(txt_angle) > 1e-6:
        txt_angle_deg = -math.degrees(txt_angle)
        txt_rotate = f' transform="rotate({txt_angle_deg:.1f},{tx:.2f},{ty:.2f})"'
        if abs(txt_angle_deg - 90) < 5 or abs(txt_angle_deg + 90) < 5:
            tx += font_size * 0.5

    # Bullet support
    bullet = int(_safe_float(para_fmt.get("Bullet", "0")))

    # Container detection for top-left label positioning
    user_data = shape.get("user", {})
    structure_type = user_data.get("msvStructureType", {}).get("Value", "")
    is_container = structure_type == "Container"
    shape_name_lower = (shape.get("name_u", "") or shape.get("name", "")).lower()
    if any(kw in shape_name_lower for kw in ("dash square", "container", "swimlane")):
        is_container = True

    # Font weight/style attributes
    fw = ' font-weight="bold"' if is_bold else ""
    fs = ' font-style="italic"' if is_italic else ""
    td = ' text-decoration="underline"' if is_underline else ""

    # Text width for wrapping - prefer TxtWidth over shape Width
    txt_width = _get_cell_float(shape, "TxtWidth")
    txt_width_px = txt_width * _INCH_TO_PX if txt_width > 0 else w_px
    # For very small shapes, use the larger of TxtWidth and shape Width
    if txt_width_px < 40 and w_px > txt_width_px:
        txt_width_px = w_px
    # For sub-shapes in groups, constrain text to actual shape width
    if w_px > 0 and txt_width_px > w_px * 2:
        txt_width_px = w_px * 0.92

    # clipPath for text clipping to shape bounds (prevents overlap)
    clip_attr = ""
    # Only clip text for shapes that are large enough to be containers
    # or have lots of text that would overflow. Small shapes should not clip.
    use_clip = w_px > 250 and h_px > 200
    if use_clip:
        clip_id = f"tclip_{shape['id']}"
        clip_x = pin_x - loc_pin_x
        clip_y = pin_y - (abs(h_inch) * _INCH_TO_PX - loc_pin_y)
        # Very generous padding to avoid cutting text at edges
        pad = font_size * 1.5
        lines.append(
            f'<defs><clipPath id="{clip_id}">'
            f'<rect x="{clip_x - pad:.2f}" y="{clip_y - pad:.2f}" '
            f'width="{w_px + 2*pad:.2f}" height="{h_px + 2*pad:.2f}"/>'
            f'</clipPath></defs>'
        )
        clip_attr = f' clip-path="url(#{clip_id})"'

    # Container text: position at top-left
    if is_container:
        vert_align = 0
        halign = 0
        text_anchor = "start"
        tx = pin_x - loc_pin_x + 8  # Left-aligned with padding
        ty = pin_y - (abs(h_inch) * _INCH_TO_PX - loc_pin_y) + font_size + 4

    # Build text lines with multi-format support
    text_parts = shape.get("text_parts", [])
    # Check if text_parts actually have visually different formatting
    has_multi_format = False
    if len(text_parts) > 1 and len(char_formats) > 1:
        # Compare visual properties across formats — only flag as multi-format
        # if there are real visible differences (font, color, style, size)
        base_font = char_fmt.get("Font", "")
        base_color = char_fmt.get("Color", "")
        base_style = char_fmt.get("Style", "0")
        base_size = char_fmt.get("Size", "")
        for cp_key, cfmt in char_formats.items():
            if cp_key == "0":
                continue
            cf = cfmt.get("Font", "")
            cc = cfmt.get("Color", "")
            cs = cfmt.get("Style", "0")
            csz = cfmt.get("Size", "")
            # Only compare fields that are set in BOTH formats
            if cf and base_font and cf != base_font:
                has_multi_format = True; break
            if cc and base_color and cc != base_color:
                has_multi_format = True; break
            if cs != base_style and cs != "Themed" and base_style != "Themed":
                has_multi_format = True; break
            if csz and base_size and csz != base_size:
                has_multi_format = True; break

    # Split text and wrap
    text_lines = text.split("\n")
    # Suppress master placeholder text (generic "text", "label", etc.)
    _PLACEHOLDERS = {"text", "label", "title", "name", "value", "type"}
    stripped = [t.strip() for t in text_lines if t.strip()]
    if len(stripped) == 1 and stripped[0].lower() in _PLACEHOLDERS:
        if shape.get("master"):
            return
    if bullet > 0:
        bullet_char = "• " if bullet == 1 else "‣ " if bullet == 2 else "– "
        text_lines = [bullet_char + tl if tl.strip() else tl for tl in text_lines]

    # Handle zero-height shapes (text-only labels in Visio)
    if h_px <= 0:
        h_px = len(text_lines) * font_size * 1.4  # Estimate height from text

    # Auto-reduce font size for small shapes with long text
    if txt_width_px > 0 and font_size > 0 and not is_container:
        avg_char_w = font_size * 0.55
        total_text_len = sum(len(tl) for tl in text_lines)
        # Horizontal overflow: reduce font if single-line text exceeds shape width
        max_line_len = max((len(tl) for tl in text_lines), default=0)
        est_line_w = max_line_len * avg_char_w
        if est_line_w > txt_width_px * 1.1 and max_line_len > 4:
            h_scale = txt_width_px * 0.95 / est_line_w
            font_size = max(5, font_size * h_scale)
            avg_char_w = font_size * 0.55
        # Estimate how many lines at current font size
        est_chars_per_line = max(4, int(txt_width_px / avg_char_w))
        est_lines = max(1, (total_text_len + est_chars_per_line - 1) // est_chars_per_line)
        est_text_height = est_lines * font_size * 1.2
        # If text would overflow height, reduce font size
        if est_text_height > h_px * 0.9 and h_px > 0:
            scale_factor = min(1.0, h_px * 0.85 / est_text_height)
            font_size = max(5, font_size * scale_factor)

    # Word-wrap with max line limit
    if txt_width_px > 0 and font_size > 0:
        avg_char_w = font_size * 0.55
        max_chars = max(4, int(txt_width_px / avg_char_w))
        # For very small shapes (< 30px wide), show abbreviated text
        if txt_width_px < 30 and not is_container:
            first_word = text_lines[0].split()[0] if text_lines and text_lines[0].split() else ""
            if first_word and len(first_word) > max_chars:
                first_word = first_word[:max(2, max_chars - 1)] + "…"
            text_lines = [first_word] if first_word else text_lines[:1]
        else:
            wrapped_lines = []
            for tline in text_lines:
                if len(tline) <= max_chars:
                    wrapped_lines.append(tline)
                else:
                    words = tline.split()
                    current = ""
                    for word in words:
                        if current and len(current) + 1 + len(word) > max_chars:
                            wrapped_lines.append(current)
                            current = word
                        else:
                            current = current + " " + word if current else word
                    if current:
                        wrapped_lines.append(current)
            text_lines = wrapped_lines
        # Limit to max lines based on available height
        # First try reducing font size to fit, then truncate as last resort
        if h_px > 0 and not is_container:
            needed_h = len(text_lines) * font_size * 1.2
            if needed_h > h_px and font_size > 5:
                # Try reducing font to fit all lines
                target_fs = h_px / (len(text_lines) * 1.2)
                if target_fs >= 5:
                    font_size = target_fs
                    # Re-wrap with new font size
                    avg_char_w = font_size * 0.55
                    max_chars = max(4, int(txt_width_px / avg_char_w))
                    re_wrapped = []
                    for tline in text.split("\n"):
                        if len(tline) <= max_chars:
                            re_wrapped.append(tline)
                        else:
                            words = tline.split()
                            current = ""
                            for word in words:
                                if current and len(current) + 1 + len(word) > max_chars:
                                    re_wrapped.append(current)
                                    current = word
                                else:
                                    current = current + " " + word if current else word
                            if current:
                                re_wrapped.append(current)
                    text_lines = re_wrapped
        max_lines = 3 if is_container else max(2, int(h_px / (font_size * 1.2))) if h_px > 0 else 4
        max_lines = min(max_lines, 8)  # absolute cap
        if len(text_lines) > max_lines:
            text_lines = text_lines[:max_lines]
            # Add ellipsis to last line
            last = text_lines[-1]
            if len(last) > 3:
                text_lines[-1] = last[:-3].rstrip() + "…"
            else:
                text_lines[-1] = last + "…"

    total_height = len(text_lines) * font_size * 1.2

    if has_multi_format and text_parts:
        # Multi-format text: render each part as a tspan
        # Collect parts into lines by splitting on newlines
        all_text = ""
        part_spans = []
        for part in text_parts:
            part_text = part.get("text", "")
            if not part_text:
                continue
            cp = part.get("cp", "0")
            cfmt = char_formats.get(cp, char_fmt)
            p_font_size = _safe_float(cfmt.get("Size"), 0.1111) * _INCH_TO_PX
            if p_font_size < 6:
                p_font_size = 8
            p_color = _resolve_color(cfmt.get("Color", ""), theme_colors) or text_color
            p_font = _map_font_family(cfmt.get("Font", font_name))
            p_style = int(_safe_float(cfmt.get("Style", "0")))
            p_bold = "bold" if p_style & 1 else "normal"
            p_italic = "italic" if p_style & 2 else "normal"
            part_spans.append({
                "text": part_text, "font": p_font, "size": p_font_size,
                "color": p_color, "bold": p_bold, "italic": p_italic
            })
            all_text += part_text

        # Render as single text element with tspans
        lines.append(
            f'<text x="{tx:.2f}" y="{ty:.2f}" '
            f'text-anchor="{text_anchor}" dominant-baseline="central" '
            f'font-family="{font_family}" font-size="{font_size:.1f}" '
            f'fill="{text_color}"{fw}{fs}{td}{txt_rotate}{clip_attr}>'
        )
        for span in part_spans:
            escaped = _escape_xml(span["text"])
            # Handle newlines within spans
            sub_lines = escaped.split("\n")
            for k, sl in enumerate(sub_lines):
                if not sl:
                    continue
                dy_attr = f' dy="{span["size"] * 1.2:.1f}" x="{tx:.2f}"' if k > 0 else ""
                lines.append(
                    f'<tspan font-family="{span["font"]}" '
                    f'font-size="{span["size"]:.1f}" fill="{span["color"]}" '
                    f'font-weight="{span["bold"]}" font-style="{span["italic"]}"{dy_attr}>'
                    f'{sl}</tspan>'
                )
        lines.append('</text>')
    elif len(text_lines) == 1:
        # Single line
        if not is_container:
            if vert_align == 0:
                ty = pin_y - h_px / 2 + font_size
            elif vert_align == 2:
                ty = pin_y + h_px / 2 - font_size * 0.3

        # Offset connector labels above the line
        if _is_1d_shape:
            ty -= font_size * 0.8 + 3

        escaped = _escape_xml(text_lines[0])
        # Add white background for connector labels (improves readability)
        if _is_1d_shape and escaped.strip():
            est_w = len(escaped) * font_size * 0.55
            lines.append(
                f'<rect x="{tx - est_w/2 - 2:.2f}" y="{ty - font_size * 0.6:.2f}" '
                f'width="{est_w + 4:.2f}" height="{font_size * 1.2:.2f}" '
                f'fill="white" fill-opacity="0.55" rx="1"/>'
            )
        # Only mark as noclip if shape has visible geometry (text is inside a shape)
        # Standalone text labels should still be subject to collision avoidance
        _has_visible_body = bool(shape.get("geometry")) and w_px > 10 and h_px > 10
        _noclip = ' data-noclip="1"' if (_has_visible_body and not _is_1d_shape) else ''
        lines.append(
            f'<text x="{tx:.2f}" y="{ty:.2f}" '
            f'text-anchor="{text_anchor}" dominant-baseline="central" '
            f'font-family="{font_family}" font-size="{font_size:.1f}" '
            f'fill="{text_color}"{fw}{fs}{td}{txt_rotate}{clip_attr}{_noclip}>'
            f'{escaped}</text>'
        )
    else:
        # Multi-line
        if not is_container:
            if vert_align == 0:
                start_y = pin_y - h_px / 2 + font_size
            elif vert_align == 2:
                start_y = pin_y + h_px / 2 - total_height + font_size * 0.6
            else:
                start_y = ty - total_height / 2 + font_size * 0.6
        else:
            start_y = ty
        # Offset connector labels above the line
        if _is_1d_shape:
            start_y -= font_size * 0.8 + 3

        for j, tline in enumerate(text_lines):
            if not tline.strip():
                continue
            escaped = _escape_xml(tline)
            ly = start_y + j * font_size * 1.2
            _has_visible_body = bool(shape.get("geometry")) and w_px > 10 and h_px > 10
            _noclip = ' data-noclip="1"' if (_has_visible_body and not _is_1d_shape) else ''
            lines.append(
                f'<text x="{tx:.2f}" y="{ly:.2f}" '
                f'text-anchor="{text_anchor}" '
                f'font-family="{font_family}" font-size="{font_size:.1f}" '
                f'fill="{text_color}"{fw}{fs}{td}{txt_rotate}{clip_attr}{_noclip}>'
                f'{escaped}</text>'
            )


def _append_name_label(lines: list, shape: dict, page_h: float,
                       w_px: float, h_px: float, label: str):
    """Append a shape name as a text label below the shape."""
    pin_x = _get_cell_float(shape, "PinX") * _INCH_TO_PX
    pin_y = (page_h - _get_cell_float(shape, "PinY")) * _INCH_TO_PX
    h_inch = _get_cell_float(shape, "Height")

    # Position label below the shape
    tx = pin_x
    ty = pin_y + h_inch * _INCH_TO_PX / 2 + 14

    # Scale font size relative to shape width (min 10px, max 48px)
    w_inch = _get_cell_float(shape, "Width")
    font_sz = max(10, min(48, w_inch * _INCH_TO_PX * 0.06))

    escaped = _escape_xml(label)
    lines.append(
        f'<text x="{tx:.2f}" y="{ty:.2f}" '
        f'text-anchor="middle" dominant-baseline="central" '
        f'font-family="sans-serif" font-size="{font_sz:.1f}" '
        f'fill="#333333">'
        f'{escaped}</text>'
    )


# ---------------------------------------------------------------------------
# Page dimension parsing
# ---------------------------------------------------------------------------

def _unit_to_inches(val: float, unit: str) -> float:
    """Convert a value in the given unit to inches."""
    u = unit.upper().strip()
    if u in ("FT", "F_I"):
        return val * 12.0
    elif u == "CM":
        return val / 2.54
    elif u == "MM":
        return val / 25.4
    elif u == "M":
        return val * 39.3701
    # IN, IN_F, or unknown → assume inches
    return val


def _normalize_page_dims(page_w: float, page_h: float,
                         units: dict[str, str] | None = None,
                         page_scale: float = 0.0, page_scale_u: str = "",
                         draw_scale: float = 0.0, draw_scale_u: str = "",
                         ) -> tuple[float, float]:
    """Normalize page dimensions to inches (in drawing coordinate space).

    When PageScale and DrawingScale are present, the page dimensions are
    in drawing units. Shapes are also in drawing units, so we keep them
    consistent but ensure the pixel size is reasonable.

    For scaled drawings (e.g., floorplans), the page might be 1728 FT wide
    but the drawing scale means shapes are positioned in those coordinates.
    We keep the coordinate space but cap the SVG pixel size.
    """
    # Visio XML stores all values in internal units (inches) regardless of
    # the U attribute (which is the display unit, not storage unit).
    # Do NOT convert by U — values are already in inches.
    return page_w, page_h


def _parse_page_dimensions(page_xml: bytes) -> tuple[float, float]:
    """Extract page width and height from a page XML.

    Returns (width_inches, height_inches).
    """
    try:
        root = ET.fromstring(page_xml)
    except ET.ParseError:
        return (8.5, 11.0)

    page_w = 8.5
    page_h = 11.0
    units: dict[str, str] = {}
    page_scale = draw_scale = 0.0
    page_scale_u = draw_scale_u = ""

    # Look for PageSheet
    page_sheet = root.find(f"{_VTAG}PageSheet")
    if page_sheet is not None:
        for cell in page_sheet.findall(f"{_VTAG}Cell"):
            n = cell.get("N", "")
            v = cell.get("V", "")
            u = cell.get("U", "")
            if n == "PageWidth":
                page_w = _safe_float(v, 8.5)
                if u:
                    units["PageWidth"] = u
            elif n == "PageHeight":
                page_h = _safe_float(v, 11.0)
                if u:
                    units["PageHeight"] = u
            elif n == "PageScale":
                page_scale = _safe_float(v)
                page_scale_u = u
            elif n == "DrawingScale":
                draw_scale = _safe_float(v)
                draw_scale_u = u

    return _normalize_page_dims(page_w, page_h, units,
                                page_scale, page_scale_u,
                                draw_scale, draw_scale_u)


def _parse_all_page_dimensions(zf: zipfile.ZipFile) -> list[tuple[float, float]]:
    """Parse page dimensions from pages.xml (the index file).

    Returns list of (width_inches, height_inches) per page.
    Falls back to individual page XML parsing.
    """
    dims = []
    try:
        pages_xml = zf.read("visio/pages/pages.xml")
        root = ET.fromstring(pages_xml)
        for page in root.findall(f"{_VTAG}Page"):
            pw, ph = 8.5, 11.0
            units: dict[str, str] = {}
            page_scale = draw_scale = 0.0
            page_scale_u = draw_scale_u = ""
            page_sheet = page.find(f"{_VTAG}PageSheet")
            if page_sheet is not None:
                for cell in page_sheet.findall(f"{_VTAG}Cell"):
                    n = cell.get("N", "")
                    v = cell.get("V", "")
                    u = cell.get("U", "")
                    if n == "PageWidth":
                        pw = _safe_float(v, 8.5)
                        if u:
                            units["PageWidth"] = u
                    elif n == "PageHeight":
                        ph = _safe_float(v, 11.0)
                        if u:
                            units["PageHeight"] = u
                    elif n == "PageScale":
                        page_scale = _safe_float(v)
                        page_scale_u = u
                    elif n == "DrawingScale":
                        draw_scale = _safe_float(v)
                        draw_scale_u = u
            dims.append(_normalize_page_dims(pw, ph, units,
                                             page_scale, page_scale_u,
                                             draw_scale, draw_scale_u))
    except (KeyError, ET.ParseError):
        pass
    return dims


# ---------------------------------------------------------------------------
# Main parser and SVG generation
# ---------------------------------------------------------------------------

def _parse_connects(page_xml_root: ET.Element) -> list[dict]:
    """Parse <Connect> elements from a page XML root."""
    connects: list[dict] = []
    connects_el = page_xml_root.find(f"{_VTAG}Connects")
    if connects_el is None:
        return connects
    for c in connects_el.findall(f"{_VTAG}Connect"):
        connects.append({
            "from_sheet": c.get("FromSheet", ""),
            "from_cell": c.get("FromCell", ""),
            "to_sheet": c.get("ToSheet", ""),
            "to_cell": c.get("ToCell", ""),
        })
    return connects


def _build_shape_index(shapes: list[dict]) -> dict[str, dict]:
    """Build a flat index of shape ID -> shape dict, including sub-shapes."""
    idx = {}
    for s in shapes:
        idx[s["id"]] = s
        for sub in s.get("sub_shapes", []):
            idx[sub["id"]] = sub
            # Also index deeper sub-shapes
            for subsub in sub.get("sub_shapes", []):
                idx[subsub["id"]] = subsub
    return idx


def _resolve_connection_point(shape: dict, cell_ref: str, page_h: float,
                               shape_index: dict) -> tuple[float, float] | None:
    """Resolve a connection cell reference to page coordinates (px).

    cell_ref like 'Controls.Row_1' or 'Connections.X1'
    """
    pin_x = _get_cell_float(shape, "PinX")
    pin_y = _get_cell_float(shape, "PinY")
    loc_pin_x = _get_cell_float(shape, "LocPinX")
    loc_pin_y = _get_cell_float(shape, "LocPinY")

    if cell_ref.startswith("Controls."):
        row_key = cell_ref.split(".", 1)[1]  # e.g. "Row_1"
        ctrl = shape.get("controls", {}).get(row_key)
        if ctrl:
            lx = _safe_float(ctrl.get("X"))
            ly = _safe_float(ctrl.get("Y"))
            # Local to page
            px = (pin_x - loc_pin_x + lx) * _INCH_TO_PX
            py = (page_h - (pin_y - loc_pin_y + ly)) * _INCH_TO_PX
            return (px, py)

    elif cell_ref.startswith("Connections."):
        # Parse "X1" -> row IX=0, "X2" -> IX=1, etc
        suffix = cell_ref.split(".", 1)[1]  # e.g. "X1"
        m = re.match(r"X(\d+)", suffix)
        if m:
            row_ix = str(int(m.group(1)) - 1)  # X1 -> IX=0
            conn = shape.get("connections", {}).get(row_ix)
            if conn:
                lx = _safe_float(conn.get("X"))
                ly = _safe_float(conn.get("Y"))
                px = (pin_x - loc_pin_x + lx) * _INCH_TO_PX
                py = (page_h - (pin_y - loc_pin_y + ly)) * _INCH_TO_PX
                return (px, py)

    return None


def _clip_line_to_rect(x1, y1, x2, y2, cx, cy, hw, hh):
    """Clip a line endpoint (x2,y2) to the edge of a rectangle centered at (cx,cy).
    
    hw, hh = half-width, half-height.
    Returns the clipped (x2, y2) on the rectangle edge.
    """
    dx = x2 - cx
    dy = y2 - cy
    if abs(dx) < 0.01 and abs(dy) < 0.01:
        return x2, y2
    # Direction from center to the other point
    ddx = x1 - cx
    ddy = y1 - cy
    if abs(ddx) < 0.01 and abs(ddy) < 0.01:
        return x2, y2
    # Find intersection with rectangle edges
    t_vals = []
    if abs(ddx) > 0.01:
        t_vals.append(hw / abs(ddx))
        t_vals.append(-hw / abs(ddx))
    if abs(ddy) > 0.01:
        t_vals.append(hh / abs(ddy))
        t_vals.append(-hh / abs(ddy))
    t = float('inf')
    for tv in t_vals:
        if tv > 0:
            px = cx + ddx * tv
            py = cy + ddy * tv
            if abs(px - cx) <= hw + 0.5 and abs(py - cy) <= hh + 0.5:
                t = min(t, tv)
    if t < float('inf'):
        return cx + ddx * t, cy + ddy * t
    return x2, y2


def _render_connections_svg(connects: list[dict], shape_index: dict,
                            page_h: float, masters: dict) -> list[str]:
    """Render connection lines as SVG elements."""
    lines = []
    # Track which connector shapes (1D) already render their own lines
    connector_sheets = set()
    for conn in connects:
        from_cell = conn.get("from_cell", "")
        if from_cell in ("BeginX", "EndX"):
            connector_sheets.add(conn["from_sheet"])

    for conn in connects:
        from_shape = shape_index.get(conn["from_sheet"])
        to_shape = shape_index.get(conn["to_sheet"])
        if not from_shape or not to_shape:
            continue

        # Skip if the from_sheet is a 1D connector (already rendered)
        if conn["from_sheet"] in connector_sheets:
            continue

        # Merge with masters for connections/controls data
        from_shape = _merge_shape_with_master(
            from_shape, masters, from_shape.get("master", ""))
        to_shape = _merge_shape_with_master(
            to_shape, masters, to_shape.get("master", ""))

        from_pt = _resolve_connection_point(
            from_shape, conn["from_cell"], page_h, shape_index)
        to_pt = _resolve_connection_point(
            to_shape, conn["to_cell"], page_h, shape_index)

        if from_pt and to_pt:
            # Clip endpoints to shape edges
            fw = abs(_get_cell_float(from_shape, "Width")) * _INCH_TO_PX / 2
            fh = abs(_get_cell_float(from_shape, "Height")) * _INCH_TO_PX / 2
            tw = abs(_get_cell_float(to_shape, "Width")) * _INCH_TO_PX / 2
            th = abs(_get_cell_float(to_shape, "Height")) * _INCH_TO_PX / 2
            
            if fw > 5 and fh > 5:
                fcx = (_get_cell_float(from_shape, "PinX")) * _INCH_TO_PX
                fcy = (page_h - _get_cell_float(from_shape, "PinY")) * _INCH_TO_PX
                from_pt = _clip_line_to_rect(to_pt[0], to_pt[1], from_pt[0], from_pt[1], fcx, fcy, fw, fh)
            if tw > 5 and th > 5:
                tcx = (_get_cell_float(to_shape, "PinX")) * _INCH_TO_PX
                tcy = (page_h - _get_cell_float(to_shape, "PinY")) * _INCH_TO_PX
                to_pt = _clip_line_to_rect(from_pt[0], from_pt[1], to_pt[0], to_pt[1], tcx, tcy, tw, th)

            lines.append(
                f'<line x1="{from_pt[0]:.2f}" y1="{from_pt[1]:.2f}" '
                f'x2="{to_pt[0]:.2f}" y2="{to_pt[1]:.2f}" '
                f'stroke="#555555" stroke-width="1.50" '
                f'marker-end="url(#arrow_end_2_555555)"/>'
            )
        elif to_pt:
            bus_y = (page_h - _get_cell_float(from_shape, "PinY")) * _INCH_TO_PX
            lines.append(
                f'<line x1="{to_pt[0]:.2f}" y1="{bus_y:.2f}" '
                f'x2="{to_pt[0]:.2f}" y2="{to_pt[1]:.2f}" '
                f'stroke="#555555" stroke-width="1.50"/>'
            )

    return lines


def _parse_vsdx_shapes(page_xml: bytes, master_texts: dict | None = None,
                       masters: dict | None = None) -> list[dict]:
    """Parse shapes from a Visio page XML into rich shape dicts.

    Args:
        page_xml: Raw XML bytes of a page file.
        master_texts: Legacy param (ignored, kept for API compat).
        masters: Full master shapes dict from _parse_master_shapes.
    """
    shapes: list[dict] = []
    try:
        root = ET.fromstring(page_xml)
    except ET.ParseError:
        return shapes

    # Find all top-level shapes (direct children of Shapes element)
    shapes_container = root.find(f"{_VTAG}Shapes")
    if shapes_container is None:
        return shapes

    for shape_elem in shapes_container.findall(f"{_VTAG}Shape"):
        sd = _parse_single_shape(shape_elem)
        shapes.append(sd)

    return shapes



def _avoid_text_collisions(text_elements: list[str]) -> list[str]:
    """Shift overlapping labels and optionally add background for readability.

    Only adds white background rectangles when text actually collides with
    other text, avoiding visual noise in sparse diagrams.
    """
    import re as _re

    _text_re = _re.compile(
        r'<text\s+x="([^"]+)"\s+y="([^"]+)"[^>]*font-size="([^"]+)"[^>]*>(.*?)</text>',
        _re.DOTALL
    )

    # First pass: parse all text elements to build boxes
    parsed = []  # list of (elem, tx, ty, fs, clean_txt, est_w, est_h, box_x, box_y, is_noclip)
    for elem in text_elements:
        m = _text_re.search(elem)
        if not m or 'data-noclip="1"' in elem:
            parsed.append((elem, None))
            continue
        try:
            tx = float(m.group(1))
            ty = float(m.group(2))
            fs = float(m.group(3))
            txt = m.group(4)
        except (ValueError, IndexError):
            parsed.append((elem, None))
            continue
        clean_txt = _re.sub(r'<[^>]+>', '', txt)
        if not clean_txt.strip():
            parsed.append((elem, None))
            continue
        est_w = len(clean_txt) * fs * 0.55
        est_h = fs * 1.3
        if 'text-anchor="start"' in elem:
            box_x = tx - 1
        elif 'text-anchor="end"' in elem:
            box_x = tx - est_w - 1
        else:
            box_x = tx - est_w / 2 - 1
        box_y = ty - est_h * 0.55
        parsed.append((elem, { # type: ignore[arg-type]
            "tx": tx, "ty": ty, "fs": fs, "orig_y": m.group(2),
            "clean_txt": clean_txt, "est_w": est_w, "est_h": est_h,
            "box_x": box_x, "box_y": box_y,
        }))

    # Determine diagram density for collision strategy tuning
    text_count = sum(1 for _, d in parsed if d is not None)
    is_dense = text_count > 40  # dense diagram threshold

    # For very dense diagrams, scale down font sizes to reduce collisions
    if text_count > 60:
        _scale = max(0.65, 40.0 / text_count)
        for i, (elem, data) in enumerate(parsed):
            if data is not None:
                data["fs"] *= _scale
                data["est_w"] = len(data["clean_txt"]) * data["fs"] * 0.55
                data["est_h"] = data["fs"] * 1.3
                if 'text-anchor="start"' in elem:
                    data["box_x"] = data["tx"] - 1
                elif 'text-anchor="end"' in elem:
                    data["box_x"] = data["tx"] - data["est_w"] - 1
                else:
                    data["box_x"] = data["tx"] - data["est_w"] / 2 - 1
                data["box_y"] = data["ty"] - data["est_h"] * 0.55
                # Update SVG element with scaled font size
                _orig_fs_str = f'font-size="{float(data["fs"] / _scale):.2f}'
                _new_fs_str = f'font-size="{data["fs"]:.2f}'
                parsed[i] = (elem.replace(_orig_fs_str, _new_fs_str, 1), data)

    placed_boxes: list[tuple[float, float, float, float]] = []
    collided_indices: set[int] = set()
    result = []

    for idx, (elem, data) in enumerate(parsed):
        if data is None:
            result.append(elem)
            continue

        tx, ty = data["tx"], data["ty"]
        fs = data["fs"]
        est_w, est_h = data["est_w"], data["est_h"]
        box_x, box_y = data["box_x"], data["box_y"]

        # Collision detection: shift text to avoid overlaps
        # For dense diagrams, try shifting in multiple directions
        max_attempts = 5 if is_dense else 3
        had_collision = False
        best_shift = None
        for attempt in range(max_attempts):
            collision = False
            for j, (px, py, pw, ph) in enumerate(placed_boxes):
                overlap_x = min(box_x + est_w + 2, px + pw) - max(box_x, px)
                overlap_y = min(box_y + est_h, py + ph) - max(box_y, py)
                if overlap_x > 0 and overlap_y > 0:
                    # Only shift if overlap is significant (>20% of text height)
                    if overlap_y > est_h * 0.2:
                        collision = True
                        had_collision = True
                        collided_indices.add(j)
                        # Alternate shift direction: down first, then try right
                        if attempt % 2 == 0:
                            ty += est_h + 2
                            box_y = ty - est_h * 0.55
                        else:
                            tx += est_w * 0.3
                            if 'text-anchor="start"' in elem:
                                box_x = tx - 1
                            elif 'text-anchor="end"' in elem:
                                box_x = tx - est_w - 1
                            else:
                                box_x = tx - est_w / 2 - 1
                        break
            if not collision:
                break

        if had_collision:
            collided_indices.add(len(placed_boxes))

        placed_boxes.append((box_x, box_y, est_w + 2, est_h))

        # Update y if shifted
        updated_elem = elem
        orig_y = data["orig_y"]
        if abs(ty - float(orig_y)) > 0.5:
            updated_elem = updated_elem.replace(
                f'y="{orig_y}"', f'y="{ty:.2f}"', 1)

        # Only add white background when this text actually collided with something
        # This avoids visual noise from unnecessary white rects
        if had_collision and fs >= 7.5:
            text_fill = ""
            _fill_m = _re.search(r'fill="([^"]+)"', elem)
            if _fill_m:
                text_fill = _fill_m.group(1).strip().upper()
            _is_light_text = False
            if text_fill.startswith("#") and len(text_fill) == 7:
                try:
                    _r = int(text_fill[1:3], 16)
                    _g = int(text_fill[3:5], 16)
                    _b = int(text_fill[5:7], 16)
                    _lum = _r * 0.299 + _g * 0.587 + _b * 0.114
                    _is_light_text = _lum > 160
                except ValueError:
                    pass
            elif text_fill in ("WHITE", "#FFF", "#FFFFFF"):
                _is_light_text = True

            if not _is_light_text:
                result.append(
                    f'<rect x="{box_x:.2f}" y="{box_y:.2f}" '
                    f'width="{est_w + 4:.2f}" height="{est_h:.2f}" '
                    f'fill="white" fill-opacity="0.7" rx="2"/>'
                )
        result.append(updated_elem)

    return result

def _shapes_to_svg(shapes: list[dict], page_w: float, page_h: float,
                   masters: dict | None = None,
                   connects: list[dict] | None = None,
                   media: dict | None = None,
                   page_rels: dict | None = None,
                   bg_shapes: list[dict] | None = None,
                   bg_connects: list[dict] | None = None,
                   output_dir: str | None = None,
                   theme_colors: dict | None = None,
                   layers: dict | None = None) -> str:
    """Generate SVG string from parsed shapes."""
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

    page_w_px = page_w * _INCH_TO_PX
    page_h_px = page_h * _INCH_TO_PX

    # Compute content bounding box for optimal viewBox.
    # This prevents clipped content (shapes near page edges) and removes
    # excessive whitespace (content only using part of the page).
    vb_x, vb_y = 0.0, 0.0
    vb_w, vb_h = page_w_px, page_h_px
    max_svg_px = 4000.0

    all_shapes = list(shapes)
    if bg_shapes:
        all_shapes.extend(bg_shapes)
    if all_shapes:
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        for s in all_shapes:
            px = _safe_float(s.get("cells", {}).get("PinX", {}).get("V")) * _INCH_TO_PX
            py = (page_h - _safe_float(s.get("cells", {}).get("PinY", {}).get("V"))) * _INCH_TO_PX
            sw = abs(_safe_float(s.get("cells", {}).get("Width", {}).get("V"))) * _INCH_TO_PX
            sh = abs(_safe_float(s.get("cells", {}).get("Height", {}).get("V"))) * _INCH_TO_PX
            if px > 0 or py > 0:
                min_x = min(min_x, px - sw / 2)
                min_y = min(min_y, py - sh / 2)
                max_x = max(max_x, px + sw / 2)
                max_y = max(max_y, py + sh / 2)
            # Also account for text below shapes (TxtPinY < 0)
            txt_pin_y = _safe_float(s.get("cells", {}).get("TxtPinY", {}).get("V"))
            if txt_pin_y < 0:
                # Text extends below shape
                text_below = abs(txt_pin_y) * _INCH_TO_PX + 20  # font estimate
                max_y = max(max_y, py + sh / 2 + text_below)
            # Account for text that may overflow shape bounds
            # (e.g. bullet lists, long descriptions next to shapes)
            txt_w = abs(_safe_float(s.get("cells", {}).get("TxtWidth", {}).get("V"))) * _INCH_TO_PX
            txt_h = abs(_safe_float(s.get("cells", {}).get("TxtHeight", {}).get("V"))) * _INCH_TO_PX
            if txt_w > sw:
                max_x = max(max_x, px + txt_w / 2)
                min_x = min(min_x, px - txt_w / 2)
            if txt_h > sh:
                max_y = max(max_y, py + txt_h / 2)
        if min_x < float('inf'):
            # Add padding — 4% or at least 20px for margins
            content_w = max_x - min_x
            content_h = max_y - min_y
            pad_x = max(50, content_w * 0.08)
            pad_y = max(50, content_h * 0.08)
            # Use content bounds but don't shrink below page if content fills it
            vb_x = min(0, min_x - pad_x)
            vb_y = min(0, min_y - pad_y)
            vb_w = max(content_w + 2 * pad_x, page_w_px) if content_w > page_w_px * 0.8 else max(content_w + 2 * pad_x, page_w_px * 0.5)
            vb_h = max(content_h + 2 * pad_y, page_h_px) if content_h > page_h_px * 0.8 else max(content_h + 2 * pad_y, page_h_px * 0.5)
            # Ensure viewBox covers from vb_x to max content + padding
            vb_w = max(vb_w, max_x + pad_x - vb_x)
            vb_h = max(vb_h, max_y + pad_y - vb_y)

    # Cap display pixel size
    display_w = vb_w
    display_h = vb_h
    if max(vb_w, vb_h) > max_svg_px:
        scale = max_svg_px / max(vb_w, vb_h)
        display_w = vb_w * scale
        display_h = vb_h * scale

    svg_lines = [
        f'<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{display_w:.0f}" height="{display_h:.0f}" '
        f'viewBox="{vb_x:.0f} {vb_y:.0f} {vb_w:.0f} {vb_h:.0f}">',
        f'<rect x="{vb_x:.0f}" y="{vb_y:.0f}" width="{vb_w:.0f}" height="{vb_h:.0f}" fill="white"/>',
    ]

    if masters is None:
        masters = {}
    if media is None:
        media = {}
    if page_rels is None:
        page_rels = {}
    if theme_colors is None:
        theme_colors = {}
    if layers is None:
        layers = {}

    used_markers: set[str] = set()
    gradients: dict[str, dict] = {}
    has_shadow: set[str] = set()

    # Two-pass rendering: geometry first, then text on top
    text_layer: list[str] = []

    # Render background page shapes first (behind foreground)
    if bg_shapes:
        svg_lines.append('<!-- Background page -->')
        for s in bg_shapes:
            svg_elements = _render_shape_svg(
                s, page_h, masters, media=media,
                page_rels=page_rels, used_markers=used_markers,
                output_dir=output_dir, theme_colors=theme_colors,
                layers=layers, gradients=gradients, has_shadow=has_shadow,
                text_layer=text_layer)
            svg_lines.extend(svg_elements)
        if bg_connects:
            bg_index = _build_shape_index(bg_shapes)
            svg_lines.extend(_render_connections_svg(
                bg_connects, bg_index, page_h, masters))

    # Sort shapes: containers first (background), then regular shapes
    # This ensures containers don't obscure the shapes inside them
    def _shape_z_order(s):
        """Return sort key: containers get low values (render first/behind)."""
        user = s.get("user", {})
        st = user.get("msvStructureType", {}).get("Value", "")
        name = (s.get("name_u", "") or s.get("name", "")).lower()
        if st == "Container" or any(kw in name for kw in ("dash square", "container", "swimlane")):
            return 0  # Containers render first (behind everything)
        return 1  # Regular shapes render on top

    sorted_shapes = sorted(shapes, key=_shape_z_order)

    # Render foreground shapes (geometry only, text collected separately)
    for s in sorted_shapes:
        svg_elements = _render_shape_svg(
            s, page_h, masters, media=media,
            page_rels=page_rels, used_markers=used_markers,
            output_dir=output_dir, theme_colors=theme_colors,
            layers=layers, gradients=gradients, has_shadow=has_shadow,
            text_layer=text_layer)
        svg_lines.extend(svg_elements)

    # Render connections
    if connects:
        shape_index = _build_shape_index(shapes)
        conn_lines = _render_connections_svg(connects, shape_index, page_h, masters)
        svg_lines.extend(conn_lines)

    # Text layer on top of everything — with collision avoidance
    if text_layer:
        svg_lines.append('<!-- Text layer -->')
        text_layer = _avoid_text_collisions(text_layer)
        svg_lines.extend(text_layer)

    svg_lines.append("</svg>")

    # Build a single <defs> block with all definitions
    defs_content = []
    if used_markers:
        # _arrow_marker_defs returns ["<defs>", ...markers..., "</defs>"]
        marker_lines = _arrow_marker_defs(used_markers)
        # Extract content between <defs> and </defs>
        for ml in marker_lines:
            if ml.strip() not in ("<defs>", "</defs>"):
                defs_content.append(ml)
    if gradients:
        defs_content.extend(_gradient_defs(gradients))
    if has_shadow:
        defs_content.append(_shadow_filter_def())

    if defs_content:
        defs_lines = ["<defs>"] + defs_content + ["</defs>"]
        for j, ml in enumerate(defs_lines):
            svg_lines.insert(3 + j, ml)

    return "\n".join(svg_lines)


# ---------------------------------------------------------------------------
# Legacy API compatibility
# ---------------------------------------------------------------------------

def _parse_master_texts(zf: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    """Parse text from master shapes. Returns {master_id: {shape_id: text}}.

    Kept for API compatibility. Internally we use _parse_master_shapes now.
    """
    masters = {}
    for name in zf.namelist():
        if name.startswith("visio/masters/master") and name.endswith(".xml") and "masters.xml" not in name:
            master_num = Path(name).stem.replace("master", "")
            try:
                root = ET.fromstring(zf.read(name))
            except (ET.ParseError, KeyError):
                continue
            shape_texts = {}
            for shape in root.iter(f"{_VTAG}Shape"):
                shape_id = shape.get("ID", "")
                text_elem = shape.find(f"{_VTAG}Text")
                if text_elem is not None:
                    text = "".join(text_elem.itertext()).strip()
                    if text:
                        shape_texts[shape_id] = text
            if shape_texts:
                masters[master_num] = shape_texts
    return masters


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

def find_vsd2xhtml() -> str | None:
    """Find vsd2xhtml from libvisio."""
    for name in ("vsd2xhtml", "vsd2raw"):
        path = shutil.which(name)
        if path:
            return path
    return None


def find_vss2xhtml() -> str | None:
    """Find vss2xhtml from libvisio."""
    path = shutil.which("vss2xhtml")
    if path:
        return path
    return None


def _convert_with_libvisio(input_path: str, output_dir: str, page: int | None = None) -> list[str]:
    """Convert using vsd2xhtml (libvisio)."""
    ext = Path(input_path).suffix.lower()

    if ext in STENCIL_EXTENSIONS:
        tool = find_vss2xhtml()
        if not tool:
            tool = find_vsd2xhtml()
    else:
        tool = find_vsd2xhtml()

    if not tool:
        return []

    basename = Path(input_path).stem

    cmd = [tool]
    if page is not None and "vsd2xhtml" in tool:
        cmd.extend(["--page", str(page)])
    cmd.append(input_path)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        return []

    xhtml_content = result.stdout
    if not xhtml_content.strip():
        return []

    svg_files = []
    try:
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

        root = ET.fromstring(xhtml_content)
        ns = {"svg": "http://www.w3.org/2000/svg", "xhtml": "http://www.w3.org/1999/xhtml"}
        svgs = root.findall(".//svg:svg", ns)
        if not svgs:
            svgs = root.findall(".//{http://www.w3.org/2000/svg}svg")

        for i, svg_elem in enumerate(svgs):
            svg_path = os.path.join(output_dir, f"{basename}_page{i + 1}.svg")
            svg_str = ET.tostring(svg_elem, encoding="unicode", xml_declaration=True)
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(svg_str)
            svg_files.append(svg_path)
    except ET.ParseError:
        svg_path = os.path.join(output_dir, f"{basename}.svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(xhtml_content)
        svg_files.append(svg_path)

    return svg_files


def _parse_vsdx_page_names(zf: zipfile.ZipFile) -> list[str]:
    """Parse page names from pages.xml inside a .vsdx/.vssx ZIP."""
    names = []
    try:
        pages_xml = zf.read("visio/pages/pages.xml")
        root = ET.fromstring(pages_xml)
        for page in root.findall(f"{_VTAG}Page"):
            name = page.get("Name", "")
            names.append(name)
    except (KeyError, ET.ParseError):
        pass
    return names


def _get_page_files(zf: zipfile.ZipFile) -> list[str]:
    """Get sorted list of page XML files from a ZIP."""
    page_files = sorted(
        n for n in zf.namelist()
        if n.startswith("visio/pages/page") and n.endswith(".xml")
        and not n.endswith("pages.xml")
    )
    if not page_files:
        page_files = sorted(
            n for n in zf.namelist()
            if "page" in n.lower() and n.endswith(".xml")
            and "pages.xml" not in n.lower()
            and "_rels" not in n
        )
    return page_files


def _parse_background_pages(zf: zipfile.ZipFile) -> dict[int, int]:
    """Parse pages.xml to find background page references.

    Returns {page_index: background_page_index} (0-based).
    """
    bg_map = {}
    try:
        pages_xml = zf.read("visio/pages/pages.xml")
        root = ET.fromstring(pages_xml)
        pages = root.findall(f"{_VTAG}Page")

        # Build page ID -> index map
        page_id_to_idx = {}
        for i, page in enumerate(pages):
            pid = page.get("ID", "")
            if pid:
                page_id_to_idx[pid] = i

        # Find BackPage references
        for i, page in enumerate(pages):
            page_sheet = page.find(f"{_VTAG}PageSheet")
            if page_sheet is None:
                continue
            for cell in page_sheet.findall(f"{_VTAG}Cell"):
                if cell.get("N") == "BackPage":
                    back_id = cell.get("V", "")
                    if back_id and back_id in page_id_to_idx:
                        bg_map[i] = page_id_to_idx[back_id]
    except (KeyError, ET.ParseError):
        pass
    return bg_map


def _vsdx_to_svg(input_path: str, output_dir: str) -> list[str]:
    """Parse .vsdx/.vstx/.vssx (ZIP+XML) and generate SVG directly."""
    if not zipfile.is_zipfile(input_path):
        return []

    os.makedirs(output_dir, exist_ok=True)
    basename = Path(input_path).stem
    svg_files = []

    with zipfile.ZipFile(input_path, "r") as zf:
        masters = _parse_master_shapes(zf)
        media = _extract_media(zf)
        theme_colors = _parse_theme(zf)
        doc_styles = _parse_stylesheets(zf)
        # Parse master rels for ForeignData image resolution
        master_rels = {}
        for name in zf.namelist():
            if name.startswith("visio/masters/_rels/master") and name.endswith(".xml.rels"):
                try:
                    rels_xml = zf.read(name)
                    root = ET.fromstring(rels_xml)
                    for rel in root.findall(f"{{{_RELS_NS}}}Relationship"):
                        rid = rel.get("Id", "")
                        target = rel.get("Target", "")
                        if rid and target:
                            master_rels[rid] = target
                except (KeyError, ET.ParseError):
                    pass
        page_files = _get_page_files(zf)
        all_dims = _parse_all_page_dimensions(zf)
        bg_map = _parse_background_pages(zf)

        # Pre-parse all pages for background composition
        # idx -> (shapes, connects, page_rels, layers)
        page_cache: dict[int, tuple] = {}

        for i, page_file in enumerate(page_files):
            try:
                page_xml = zf.read(page_file)
            except (KeyError, zipfile.BadZipFile):
                continue

            shapes = _parse_vsdx_shapes(page_xml, masters=masters)
            try:
                page_root = ET.fromstring(page_xml)
                connects = _parse_connects(page_root)
                page_layers = _parse_layers(page_root)
            except ET.ParseError:
                connects = []
                page_layers = {}

            page_rels = _parse_rels(zf, page_file)
            page_cache[i] = (shapes, connects, page_rels, page_layers)

        for i, page_file in enumerate(page_files):
            if i not in page_cache:
                continue

            shapes, connects, page_rels, page_layers = page_cache[i]
            if not shapes:
                continue

            if i < len(all_dims):
                page_w, page_h = all_dims[i]
            else:
                try:
                    page_xml = zf.read(page_file)
                    page_w, page_h = _parse_page_dimensions(page_xml)
                except (KeyError, zipfile.BadZipFile):
                    page_w, page_h = 8.5, 11.0

            # Background page composition
            bg_shapes = None
            bg_connects = None
            if i in bg_map:
                bg_idx = bg_map[i]
                if bg_idx in page_cache:
                    bg_shapes, bg_connects, _, _ = page_cache[bg_idx]

            # Merge master_rels into page_rels for image resolution
            all_rels = dict(master_rels)
            all_rels.update(page_rels)
            svg_content = _shapes_to_svg(
                shapes, page_w, page_h, masters, connects,
                media, all_rels, bg_shapes, bg_connects, output_dir,
                theme_colors, page_layers)
            svg_path = os.path.join(output_dir, f"{basename}_page{i + 1}.svg")
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(svg_content)
            svg_files.append(svg_path)

    return svg_files


def get_page_info(input_path: str) -> list[dict]:
    """Get page names and shape data from a Visio file.

    Returns list of dicts: [{"name": "Page-1", "shapes": [...], "index": 0}, ...]
    """
    ext = Path(input_path).suffix.lower()
    pages: list[dict] = []

    if ext in _XML_EXTENSIONS:
        if not zipfile.is_zipfile(input_path):
            return pages

        with zipfile.ZipFile(input_path, "r") as zf:
            masters = _parse_master_shapes(zf)
            page_names = _parse_vsdx_page_names(zf)
            page_files = _get_page_files(zf)
            all_dims = _parse_all_page_dimensions(zf)

            for i, page_file in enumerate(page_files):
                try:
                    page_xml = zf.read(page_file)
                except (KeyError, zipfile.BadZipFile):
                    continue

                shapes = _parse_vsdx_shapes(page_xml, masters=masters)
                name = page_names[i] if i < len(page_names) else f"Page {i + 1}"
                page_w, page_h = all_dims[i] if i < len(all_dims) else _parse_page_dimensions(page_xml)
                pages.append({"name": name, "shapes": shapes, "index": i,
                              "page_w": page_w, "page_h": page_h})

    return pages


def extract_all_text(input_path: str) -> str:
    """Extract all text from a Visio file."""
    ext = Path(input_path).suffix.lower()

    if ext in _XML_EXTENSIONS:
        pages = get_page_info(input_path)
        text_lines = []
        for page in pages:
            text_lines.append(f"--- {page['name']} ---")
            for shape in page["shapes"]:
                if shape.get("text"):
                    text_lines.append(shape["text"])
            text_lines.append("")
        return "\n".join(text_lines)

    if ext in (".vsd", ".vss", ".vst"):
        text = _extract_text_vsd(input_path)
        if text:
            return text
        # Fall back to libvisio
        tool = find_vsd2xhtml()
        if tool:
            try:
                result = subprocess.run(
                    [tool, input_path],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0:
                    try:
                        root = ET.fromstring(result.stdout)
                        texts = []
                        for elem in root.iter():
                            if elem.text and elem.text.strip():
                                texts.append(elem.text.strip())
                            if elem.tail and elem.tail.strip():
                                texts.append(elem.tail.strip())
                        return "\n".join(texts)
                    except ET.ParseError:
                        return result.stdout
            except Exception:
                pass

    return ""


def convert_vsd_to_svg(input_path: str, output_dir: str | None = None) -> list[str]:
    """Convert a Visio file to SVG pages.

    Returns a list of SVG file paths (one per page).
    Uses libvisio if available, otherwise built-in .vsdx parser.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="vsdview_")

    input_path = os.path.abspath(input_path)
    ext = Path(input_path).suffix.lower()

    if ext not in ALL_EXTENSIONS:
        raise RuntimeError(_("Unsupported file format: %s") % ext)

    # For .vsdx files, prefer built-in parser (handles images, arrows,
    # background pages). Fall back to libvisio only for .vsd/.vss.
    if ext in _XML_EXTENSIONS:
        svg_files = _vsdx_to_svg(input_path, output_dir)
        if svg_files:
            return svg_files

    # For .vsd binary files, try native parser first
    if ext in (".vsd", ".vss", ".vst"):
        svg_files = _vsd_to_svg(input_path, output_dir)
        if svg_files:
            return svg_files

    # Fall back to libvisio (C++)
    svg_files = _convert_with_libvisio(input_path, output_dir)
    if svg_files:
        return svg_files

    raise RuntimeError(
        _("Could not parse the Visio file. "
          "The file may be corrupt or unsupported.")
    )


def convert_vsd_page_to_svg(input_path: str, page_index: int, output_dir: str) -> str | None:
    """Convert a specific page of a Visio file to SVG. Returns SVG path or None."""
    ext = Path(input_path).suffix.lower()
    basename = Path(input_path).stem

    if ext in (".vsd", ".vss", ".vst"):
        svg_files = _convert_with_libvisio(input_path, output_dir, page=page_index + 1)
        if svg_files:
            return svg_files[0]
        return None

    if ext in _XML_EXTENSIONS and zipfile.is_zipfile(input_path):
        with zipfile.ZipFile(input_path, "r") as zf:
            masters = _parse_master_shapes(zf)
            media = _extract_media(zf)
            theme_colors = _parse_theme(zf)
            doc_styles = _parse_stylesheets(zf)
            page_files = _get_page_files(zf)
            all_dims = _parse_all_page_dimensions(zf)
            bg_map = _parse_background_pages(zf)
            if page_index >= len(page_files):
                return None

            page_file = page_files[page_index]
            page_xml = zf.read(page_file)
            if page_index < len(all_dims):
                page_w, page_h = all_dims[page_index]
            else:
                page_w, page_h = _parse_page_dimensions(page_xml)
            shapes = _parse_vsdx_shapes(page_xml, masters=masters)
            if not shapes:
                return None

            try:
                page_root = ET.fromstring(page_xml)
                connects = _parse_connects(page_root)
                page_layers = _parse_layers(page_root)
            except ET.ParseError:
                connects = []
                page_layers = {}

            page_rels = _parse_rels(zf, page_file)

            # Background page
            bg_shapes = None
            bg_connects = None
            if page_index in bg_map:
                bg_idx = bg_map[page_index]
                if bg_idx < len(page_files):
                    try:
                        bg_xml = zf.read(page_files[bg_idx])
                        bg_shapes = _parse_vsdx_shapes(bg_xml, masters=masters)
                        bg_root = ET.fromstring(bg_xml)
                        bg_connects = _parse_connects(bg_root)
                    except (KeyError, ET.ParseError):
                        pass

            # Merge master rels into page_rels for image resolution
            master_rels: dict[str, str] = {}
            for mname in zf.namelist():
                if "masters/" in mname and mname.endswith(".rels"):
                    try:
                        mrels_root = ET.fromstring(zf.read(mname))
                        for rel in mrels_root:
                            rid = rel.get("Id", "")
                            target = rel.get("Target", "")
                            if rid and target:
                                master_rels[rid] = target
                    except ET.ParseError:
                        pass
            all_rels = dict(master_rels)
            all_rels.update(page_rels)
            svg_content = _shapes_to_svg(
                shapes, page_w, page_h, masters, connects,
                media, all_rels, bg_shapes, bg_connects, output_dir,
                theme_colors, page_layers)
            svg_path = os.path.join(output_dir, f"{basename}_page{page_index + 1}.svg")
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(svg_content)
            return svg_path

    return None


def export_to_png(svg_path: str, output_path: str, width: int = 1920) -> str:
    """Export an SVG to PNG using rsvg-convert or cairosvg."""
    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        subprocess.run(
            [rsvg, "-w", str(width), "-o", output_path, svg_path],
            check=True,
            timeout=30,
        )
        return output_path

    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=output_path, output_width=width)
        return output_path
    except ImportError:
        pass

    raise RuntimeError(
        _("Neither rsvg-convert nor cairosvg found. Install one:\n"
          "  Ubuntu/Debian: sudo apt install librsvg2-bin\n"
          "  pip install cairosvg")
    )


def _add_watermark(svg_content: str) -> str:
    """Add a discreet 'VSDView' watermark in the bottom-right corner of SVG."""
    import re
    # Extract viewBox dimensions to position watermark
    vb_match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg_content)
    if not vb_match:
        return svg_content
    vb_w = float(vb_match.group(1))
    vb_h = float(vb_match.group(2))
    watermark = (
        f'<text x="{vb_w - 8:.1f}" y="{vb_h - 6:.1f}" '
        f'font-family="sans-serif" font-size="9" '
        f'fill="#b0b0b0" fill-opacity="0.5" '
        f'text-anchor="end">VSDView</text>'
    )
    return svg_content.replace("</svg>", f"{watermark}\n</svg>")


def export_to_pdf(svg_path: str, output_path: str) -> str:
    """Export an SVG to PDF using cairosvg."""
    try:
        import cairosvg
        with open(svg_path, "r", encoding="utf-8") as f:
            svg_content = f.read()
        svg_content = _add_watermark(svg_content)
        cairosvg.svg2pdf(bytestring=svg_content.encode("utf-8"),
                         write_to=output_path)
        return output_path
    except ImportError:
        raise RuntimeError(
            _("cairosvg is required for PDF export. Install it:\n"
              "  pip install cairosvg")
        )


# ---------------------------------------------------------------------------
# Native .vsd binary format support
# ---------------------------------------------------------------------------

def _vsd_to_svg(input_path: str, output_dir: str) -> list[str]:
    """Parse .vsd (binary) and generate SVG using the native parser.

    Uses _vsd_parser to read the OLE2 file and the existing _shapes_to_svg
    renderer to produce SVG output.
    """
    from libvisio_ng._vsd_parser import parse_vsd_to_dicts

    os.makedirs(output_dir, exist_ok=True)
    basename = Path(input_path).stem
    svg_files = []

    try:
        pages = parse_vsd_to_dicts(input_path)
    except Exception:
        return []

    for i, page_data in enumerate(pages):
        shapes = page_data["shapes"]
        if not shapes:
            continue

        page_w = page_data["page_width"]
        page_h = page_data["page_height"]

        svg_content = _shapes_to_svg(
            shapes, page_w, page_h,
            masters={}, connects=None,
            media={}, page_rels={},
            bg_shapes=None, bg_connects=None,
            output_dir=output_dir,
            theme_colors={}, layers={})
        svg_path = os.path.join(output_dir, f"{basename}_page{i + 1}.svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg_content)
        svg_files.append(svg_path)

    return svg_files


def _extract_text_vsd(input_path: str) -> str:
    """Extract text from a .vsd file using the native parser."""
    from libvisio_ng._vsd_parser import parse_vsd_file

    try:
        doc = parse_vsd_file(input_path)
    except Exception:
        return ""

    text_lines = []
    for i, page in enumerate(doc.pages):
        text_lines.append(f"--- {page.name or f'Page {i+1}'} ---")
        for shape in page.shapes:
            if shape.text:
                text_lines.append(shape.text)
        text_lines.append("")

    return "\n".join(text_lines)
