"""Probabilistic AI Incident Commander."""

from __future__ import annotations

import os

# Avoid allocating a worker per host CPU in shared CI and container environments.
# User-provided settings always take precedence.
os.environ.setdefault("POLARS_MAX_THREADS", "4")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

# Keep source-tree execution authoritative when an older editable distribution
# happens to remain installed in the developer environment.
__version__ = "0.10.0"

__all__ = ["__version__"]
