"""Backward-compatible import shim for the v20 service migration.

New code should import a focused module from :mod:`agl.services`.  The complete
legacy surface remains available here for scripts created before v20.
"""

from agl.operations import *  # noqa: F401,F403
