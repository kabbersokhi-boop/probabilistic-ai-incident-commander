"""Deterministic commerce analytics and semantic metric layer."""

from paic.analytics.config import AnalyticsConfig, load_analytics_config
from paic.analytics.engine import build_analytics
from paic.analytics.registry import METRIC_DEFINITIONS, METRIC_REGISTRY

__all__ = [
    "METRIC_DEFINITIONS",
    "METRIC_REGISTRY",
    "AnalyticsConfig",
    "build_analytics",
    "load_analytics_config",
]
