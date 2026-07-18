"""Probabilistic AI Incident Commander."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version

# Avoid allocating a worker per host CPU in shared CI and container environments.
# User-provided settings always take precedence.
os.environ.setdefault("POLARS_MAX_THREADS", "4")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

try:
    __version__ = version("probabilistic-ai-incident-commander")
except PackageNotFoundError:  # pragma: no cover - editable source tree
    __version__ = "0.9.0"

__all__ = ["__version__"]
