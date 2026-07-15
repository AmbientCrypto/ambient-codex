"""Internal package seam for the phased Ambient Codex CLI refactor.

Phase 0 deliberately exposes metadata only. Workflow behavior remains in the
extensionless ``bin/ambient`` compatibility facade until later green phases.
"""

PACKAGE_NAME = "ambient_codex"
LAYOUT_VERSION = 1

__all__ = ("LAYOUT_VERSION", "PACKAGE_NAME")
