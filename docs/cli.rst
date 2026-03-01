CLI Reference
=============

``visio2svg`` is the command-line interface for libvisio-ng.

Convert
-------

Convert Visio files to SVG:

.. code-block:: bash

   # Convert all pages
   visio2svg convert diagram.vsdx -o output/

   # Convert a specific page (0-indexed)
   visio2svg convert diagram.vsdx -p 0 -o output/

   # Convert .vsd binary format
   visio2svg convert legacy.vsd -o output/

Info
----

Display page information:

.. code-block:: bash

   visio2svg info diagram.vsdx

Output::

   Page 0: Page-1 (8.5x11.0)
   Page 1: Page-2 (8.5x11.0)

Text
----

Extract all text content:

.. code-block:: bash

   visio2svg text diagram.vsdx

Version
-------

.. code-block:: bash

   visio2svg --version
