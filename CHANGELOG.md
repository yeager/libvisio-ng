# 0.7.2 — 2026-11-19

- Follow the Visio page catalog and relationships for page order, preserve empty pages, and expose page layers with per-page visibility overrides.

# 0.7.1 — 2026-09-17

- Reject negative single-page indices instead of selecting from the end of the document.
- Create nested output directories for single-page conversion.
- Display actual page dimensions in CLI info output.
- Add four regression tests.

Validation: full Python test suite (151 tests).

# Changelog

## 0.6.0 (2025-02-28)

### New features
- **Rich text (per-run styling):** parse `<cp>`/`<pp>` run references and
  `Character`/`Paragraph` sections in .vsdx; render as `<tspan>` elements with
  per-run font, size, color, bold/italic/underline styling in SVG output.
- **Radial gradients:** detect radial gradient fill patterns (FillPattern 29-32,
  37-39) and emit `<radialGradient>` with focal point support.
- **Image embedding:** parse `<ForeignData>` elements, resolve media references
  from the .vsdx archive, and embed as base64 data URIs in SVG `<image>` elements
  (PNG, JPEG).
- **Shadow filters:** drop-shadow support via SVG `<filter>` elements.
- **EllipticalArcTo:** proper SVG arc rendering for elliptical geometry rows.
- **NURBS curves:** parse and render NURBSTo geometry rows as cubic Bézier
  approximations.
- **Theme color resolution:** resolve Visio theme/variant colors from document
  themes for accurate fill, line, and text colors.
- **Auto-contrast text:** automatically switch text color for readability against
  dark fills.
- **Bullet lists:** paragraph bullet rendering with IndFirst/IndLeft support.

### Improvements
- Expanded test suite from 6 to 41 tests covering rich text, gradients, image
  embedding, VSD binary parsing, text parsing, theme colors, and version
  consistency.
- Added test fixtures for rich text, gradient, and image embedding scenarios.
- VSD binary parser: improved text run parsing, paragraph/character format
  extraction, connection point handling, layer membership, and sub-shape support.

### Fixes
- Hardcoded version references replaced with dynamic version checks.
- Geometry coordinate scaling for master-inherited shapes.

## 0.5.0 (2025-02-27)

- Dash array line patterns with full Visio pattern mapping.
- Fill patterns (hatching) via SVG `<pattern>` defs.
- Improved SVG `<defs>` generation for markers and patterns.

## 0.2.0 (2025-02-26)

- Major VSD binary parser improvements.
- Native .vsd (OLE2) format support alongside .vsdx.

## 0.1.0 (2025-02-25)

- Initial release: .vsdx parser extracted from vsdview.
- SVG conversion with geometry, text, fills, and line styles.
- CLI tool `visio2svg`.
