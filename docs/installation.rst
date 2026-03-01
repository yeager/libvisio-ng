Installation
============

Requirements
------------

- Python 3.10 or later
- ``olefile`` (installed automatically)

Install from PyPI
-----------------

.. code-block:: bash

   pip install libvisio-ng

Optional dependencies
---------------------

For PNG export:

.. code-block:: bash

   pip install libvisio-ng[png]

For PDF export:

.. code-block:: bash

   pip install libvisio-ng[pdf]

Install from source
-------------------

.. code-block:: bash

   git clone https://github.com/yeager/libvisio-ng.git
   cd libvisio-ng
   pip install -e ".[dev]"
