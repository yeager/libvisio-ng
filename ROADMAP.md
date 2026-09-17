# Proposed next improvements

Prioritized follow-up proposals; these are not implemented in this release.

1. Make native binary single-page conversion match whole-document conversion; it currently depends on the external fallback tool.
2. Return binary page metadata through get_page_info, including dimensions and shape data used by VSDView.
3. Add recursive text extraction tests for nested groups and shared cross-language rendering fixtures.
4. Introduce explicit conversion diagnostics for unsupported geometry, fonts and fallback paths.
