from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def _ks_statistic(current: np.ndarray, baseline: np.ndarray) -> float:
    """Two-sample Kolmogorov-Smirnov statistic without scipy.

    Max absolute gap between the two samples' empirical CDFs, evaluated at
    every observed value. This catches shape/shift changes that a mean-ratio
    check misses (e.g. a bimodal split with an unchanged mean).
    """
    cur_sorted = np.sort(current)
    base_sorted = np.sort(baseline)
    all_values = np.concatenate([cur_sorted, base_sorted])
    cdf_cur = np.searchsorted(cur_sorted, all_values, side="right") / cur_sorted.size
    cdf_base = np.searchsorted(base_sorted, all_values, side="right") / base_sorted.size
    return float(np.max(np.abs(cdf_cur - cdf_base)))


def detect_distribution_shift(
    current_values: Iterable[float],
    baseline_values: Iterable[float],
    *,
    ratio_threshold: float = 3.0,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Distribution-shift detector combining a mean-ratio check (starter)
    with a two-sample KS test (no scipy dependency required).

    Mean ratio alone misses shape changes with a similar mean (e.g. half the
    values collapsing to near-zero while the other half spikes). The KS
    statistic is compared against the standard asymptotic critical value for
    two-sample KS tests, `1.36 * sqrt((n+m)/(n*m))` at alpha=0.05.
    """
    cur = np.asarray(list(current_values), dtype=float)
    base = np.asarray(list(baseline_values), dtype=float)
    if cur.size == 0 or base.size == 0:
        return {"is_anomaly": False, "score": 0.0, "method": "mean_ratio+ks", "reason": "empty_input"}

    cur_mean = float(np.mean(cur))
    base_mean = float(np.mean(base))
    if base_mean == 0:
        ratio_score = float("inf") if cur_mean != 0 else 1.0
    else:
        ratio_score = max(abs(cur_mean / base_mean), abs(base_mean / cur_mean)) if cur_mean != 0 else float("inf")
    ratio_anomaly = ratio_score >= ratio_threshold

    ks_stat = _ks_statistic(cur, base)
    n, m = cur.size, base.size
    ks_crit = 1.36 * ((n + m) / (n * m)) ** 0.5 if n and m else 0.0
    ks_score = (ks_stat / ks_crit) if ks_crit > 0 else 0.0
    ks_anomaly = ks_stat > ks_crit

    is_anomaly = bool(ratio_anomaly or ks_anomaly)
    finite_ratio_score = ratio_score if np.isfinite(ratio_score) else max(ks_score, ratio_threshold)
    score = float(max(finite_ratio_score, ks_score))

    return {
        "is_anomaly": is_anomaly,
        "score": score,
        "method": "mean_ratio+ks",
        "reason": (
            f"baseline_mean={base_mean:.3f}, current_mean={cur_mean:.3f}, ratio_score={ratio_score:.3f}"
            f" (threshold={ratio_threshold}); ks_stat={ks_stat:.3f}, ks_crit={ks_crit:.3f} (alpha={alpha})"
        ),
    }
