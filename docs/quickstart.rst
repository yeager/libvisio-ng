Quick Start
===========

Basic conversion
----------------

Convert a Visio file to SVG:

.. code-block:: python

   from libvisio_ng import convert

   # Convert all pages to SVG files
   svg_files = convert("diagram.vsdx", output_dir="output/")

   # Works with both .vsdx and .vsd formats
   svg_files = convert("legacy.vsd", output_dir="output/")

Page information
----------------

Retrieve metadata about pages in a Visio file:

.. code-block:: python

   from libvisio_ng import get_page_info

   for page in get_page_info("diagram.vsdx"):
       print(f"{page['name']}: {page['width']}x{page['height']}")

Text extraction
---------------

Extract all text content from a diagram:

.. code-block:: python

   from libvisio_ng import extract_text

   text = extract_text("diagram.vsdx")
   print(text)

Working with .vsd files directly
---------------------------------

The binary VSD parser provides low-level access:

.. code-block:: python

   from libvisio_ng import parse_vsd_file

   doc = parse_vsd_file("legacy.vsd")
   for page in doc.pages:
       print(f"Page: {page.name} ({len(page.shapes)} shapes)")
       for shape in page.shapes:
           if shape.text:
               print(f"  Shape {shape.shape_id}: {shape.text}")

PNG and PDF export
------------------

Export SVG files to PNG or PDF (requires ``cairosvg``):

.. code-block:: python

   from libvisio_ng import convert, export_to_png, export_to_pdf

   svg_files = convert("diagram.vsdx", output_dir="output/")
   for svg in svg_files:
       export_to_png(svg, svg.replace(".svg", ".png"))
       export_to_pdf(svg, svg.replace(".svg", ".pdf"))
