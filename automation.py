"""Shim module to allow `import automation.*` when running from this directory.

This repo’s root directory is itself named `automation/`. When executing commands
from inside it, Python can’t import `automation` as a package because it expects
an *enclosing* directory on `sys.path`.

By defining `__path__`, we make this module behave like a package whose
submodules live alongside this file (e.g. `backend/`, `maricopa_scraper/`).
"""

from __future__ import annotations

import os

# Treat this module as a package rooted at the current directory.
__path__ = [os.path.dirname(__file__)]  # type: ignore
