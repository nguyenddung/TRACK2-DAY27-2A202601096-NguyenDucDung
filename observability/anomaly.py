"""Anomaly detection.

Z-score is kept as a simple, always-available baseline. `mad_detector` is a
robust (outlier-resistant) alternative. `auto` combines both with
seasonality context: when the caller supplies a same-segment history (e.g.
"last N Saturdays" for a Saturday reading), auto compares against that
segment instead of the raw, season-mixed history, and prefers the robust MAD
statistic once there is enough data.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def zscore_detector(current: float, history: Iterable[float], threshold: float = 3.0) -> dict[str, Any]:
    values = np.asarray(list(history), dtype=float)
    if values.size < 3:
        return {"is_anomaly": False, "score": 0.0, "method": "zscore", "reason": "insufficient_history"}
    mean = float(np.mean(values))
    std = float(np.std(values))
    if std == 0:
        score = float("inf") if float(current) != mean else 0.0
    else:
        score = abs(float(current) - mean) / std
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "zscore",
        "reason": f"mean={mean:.3f}, std={std:.3f}, threshold={threshold}",
    }


def mad_detector(current: float, history: Iterable[float], threshold: float = 3.5) -> dict[str, Any]:
    """Median Absolute Deviation detector (robust to outliers in `history`).

    When the history is constant (MAD == 0), a raw modified z-score would
    divide by zero and always report "no anomaly" no matter how far `current`
    is from that constant value. We fall back to std-dev in that case, and to
    an exact-equality check if even std-dev is zero (fully constant history).
    """
    values = np.asarray(list(history), dtype=float)
    if values.size < 5:
        return {"is_anomaly": False, "score": 0.0, "method": "mad", "reason": "insufficient_history"}
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))

    if mad == 0:
        std = float(np.std(values))
        if std == 0:
            is_anomaly = float(current) != median
            score = float("inf") if is_anomaly else 0.0
            reason = f"median={median:.3f}, mad=0, std=0 (constant history)"
        else:
            score = abs(float(current) - median) / std
            is_anomaly = score > threshold
            reason = f"median={median:.3f}, mad=0, fallback_std={std:.3f}, threshold={threshold}"
        return {"is_anomaly": bool(is_anomaly), "score": float(score), "method": "mad", "reason": reason}

    modified_z = 0.6745 * abs(float(current) - median) / mad
    return {
        "is_anomaly": bool(modified_z > threshold),
        "score": float(modified_z),
        "method": "mad",
        "reason": f"median={median:.3f}, mad={mad:.3f}, threshold={threshold}",
    }


def _auto_detector(
    current: float,
    history: Iterable[float],
    *,
    threshold: float,
    context: dict[str, Any],
) -> dict[str, Any]:
    history_list = list(history)
    segment = context.get("same_segment_history")
    known_event = context.get("known_event")

    if segment:
        baseline_values: list[float] = list(segment)
        source = "same_segment_history"
    else:
        baseline_values = history_list
        source = "history"

    if len(baseline_values) >= 5:
        result = mad_detector(current, baseline_values, threshold=3.5)
        result["method"] = f"auto:mad:{source}"
    else:
        result = zscore_detector(current, baseline_values, threshold=threshold)
        result["method"] = f"auto:zscore:{source}"

    day_of_week = context.get("day_of_week")
    extra = [f"baseline_n={len(baseline_values)}", f"source={source}"]
    if day_of_week is not None:
        extra.append(f"day_of_week={day_of_week}")
    result["reason"] = f"{result['reason']}; " + ", ".join(extra)

    if known_event and result["is_anomaly"]:
        result["is_anomaly"] = False
        result["reason"] += f"; suppressed_by_known_event={known_event}"

    return result


def detect_anomaly(
    current: float,
    history: Iterable[float],
    *,
    method: str = "auto",
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stable lab API.

    - `zscore`: basic z-score against raw `history`.
    - `mad`: robust median/MAD detector against raw `history`.
    - `auto`: seasonality- and context-aware. Prefers
      `context["same_segment_history"]` (e.g. same-weekday values) over the
      raw `history` when supplied, uses the robust MAD statistic once there
      are at least 5 baseline points, and suppresses the alert (while still
      reporting the score) when `context["known_event"]` explains the shift.
    """
    if method == "mad":
        return mad_detector(current, history)
    if method == "zscore":
        return zscore_detector(current, history, threshold=threshold)
    if method == "auto":
        return _auto_detector(current, history, threshold=threshold, context=context or {})
    raise ValueError(f"Unsupported method: {method}")
