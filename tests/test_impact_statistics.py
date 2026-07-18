from __future__ import annotations

import numpy as np
import numpy.typing as npt

from paic.impact.statistics import (
    concordance_index,
    fit_cox,
    fit_logistic,
    kaplan_meier,
    nearest_neighbor_matches,
    stabilized_iptw,
)


def test_kaplan_meier_known_curve() -> None:
    rows = kaplan_meier(
        np.asarray([1.0, 2.0, 2.0, 3.0]),
        np.asarray([True, True, False, True]),
        0.95,
    )
    assert rows[0]["at_risk"] == 4
    assert rows[0]["survival_probability"] == 0.75
    assert rows[-1]["survival_probability"] == 0.0


def test_propensity_fit_weights_and_matching() -> None:
    x = np.asarray([[-2.0], [-1.0], [-0.2], [0.2], [1.0], [2.0]])
    y = np.asarray([0.0, 0.0, 0.0, 1.0, 1.0, 1.0])
    fit = fit_logistic(x, y)
    assert fit.probabilities[0] < fit.probabilities[-1]
    exposed: npt.NDArray[np.bool_] = y.astype(np.bool_)
    weights = stabilized_iptw(fit.probabilities, exposed, 0.02)
    assert np.all(np.isfinite(weights))
    assert nearest_neighbor_matches(fit.probabilities, exposed, 1.0)


def test_cox_fit_and_concordance() -> None:
    features = np.asarray([[0.0], [0.5], [1.0], [1.5], [2.0]])
    durations = np.asarray([5.0, 4.0, 3.0, 2.0, 1.0])
    events: npt.NDArray[np.bool_] = np.ones(5, dtype=np.bool_)
    fit = fit_cox(features, durations, events)
    assert fit.converged
    assert fit.coefficients[0] > 0
    assert concordance_index(durations, events, features[:, 0]) == 1.0
