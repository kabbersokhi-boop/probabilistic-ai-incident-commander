"""Deterministic helpers for anomaly detection."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from paic.analytics.config import TimeGrain


def stable_hash_id(prefix: str, payload: dict[str, Any], *, length: int = 20) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:length]}"


def period_delta(grain: TimeGrain) -> timedelta:
    return timedelta(hours=1) if grain == "hour" else timedelta(days=1)
