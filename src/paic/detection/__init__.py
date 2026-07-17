"""Deterministic, distribution-aware anomaly detection for analytical metrics."""

from paic.detection.config import DetectionConfig, load_detection_config
from paic.detection.engine import build_detection

__all__ = ["DetectionConfig", "build_detection", "load_detection_config"]
