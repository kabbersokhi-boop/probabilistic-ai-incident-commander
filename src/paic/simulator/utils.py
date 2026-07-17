"""Low-level deterministic helpers used by simulator generators."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TypeVar

import numpy as np
import numpy.typing as npt

from paic.simulator.config import WeightedValue

T = TypeVar("T")


def probability_vector(weights: Sequence[float]) -> npt.NDArray[np.float64]:
    """Normalize strictly positive weights into a probability vector."""

    values: npt.NDArray[np.float64] = np.asarray(weights, dtype=np.float64)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("weights must be a non-empty one-dimensional sequence")
    if np.any(values <= 0) or not np.all(np.isfinite(values)):
        raise ValueError("weights must be finite and strictly positive")
    return values / values.sum()


def values_and_probabilities(
    options: Sequence[WeightedValue],
) -> tuple[npt.NDArray[np.str_], npt.NDArray[np.float64]]:
    values = np.asarray([item.value for item in options], dtype=np.str_)
    probabilities = probability_vector([item.weight for item in options])
    return values, probabilities


def choose_weighted(
    rng: np.random.Generator,
    values: Sequence[T] | npt.NDArray[np.str_],
    weights: Sequence[float] | npt.NDArray[np.float64],
    size: int,
) -> npt.NDArray[np.object_]:
    probabilities = probability_vector(weights)
    return np.asarray(rng.choice(values, size=size, p=probabilities), dtype=object)


def stable_ids(prefix: str, count: int, width: int = 8) -> list[str]:
    """Return lexicographically sortable deterministic identifiers."""

    return [f"{prefix}-{index:0{width}d}" for index in range(1, count + 1)]


def random_datetimes(
    rng: np.random.Generator,
    start: datetime,
    end: datetime,
    count: int,
) -> list[datetime]:
    """Sample UTC datetimes uniformly in a half-open interval."""

    if end <= start:
        raise ValueError("end must be after start")
    total_microseconds = int((end - start).total_seconds() * 1_000_000)
    offsets = rng.integers(0, total_microseconds, size=count, endpoint=False)
    return [start + timedelta(microseconds=int(offset)) for offset in offsets]


def clipped_probabilities(values: npt.ArrayLike) -> npt.NDArray[np.float64]:
    return np.clip(np.asarray(values, dtype=np.float64), 0.001, 0.999)


def rounded_money(values: npt.ArrayLike) -> npt.NDArray[np.float64]:
    return np.round(np.asarray(values, dtype=np.float64), 2)
