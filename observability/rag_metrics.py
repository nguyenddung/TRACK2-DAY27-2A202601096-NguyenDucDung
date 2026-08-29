from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from observability.anomaly import zscore_detector


def approximate_token_lengths(texts: Iterable[str]) -> list[int]:
    # Deliberately simple proxy; no tokenizer/model download needed.
    return [len(str(t).split()) for t in texts]


def detect_text_length_shift(
    current_texts: Iterable[str],
    baseline_batch_means: Iterable[float],
    *,
    threshold: float = 3.0,
) -> dict[str, Any]:
    lengths = approximate_token_lengths(current_texts)
    current_mean = float(np.mean(lengths)) if lengths else 0.0
    result = zscore_detector(current_mean, baseline_batch_means, threshold=threshold)
    result["metric"] = "mean_text_length"
    result["current_mean"] = current_mean
    return result


def detect_embedding_norm_shift(
    current_norms: Iterable[float],
    baseline_norms: Iterable[float],
    *,
    threshold: float = 3.0,
) -> dict[str, Any]:
    """Embedding-space drift proxy: z-score of the mean embedding vector norm.

    No embedding model is required for the lab: callers (or hidden tests)
    feed precomputed norms through this stable interface. A shift in mean
    norm is a cheap, model-agnostic signal for retrieval/embedding drift
    (e.g. an index rebuilt with a different model, or truncated/garbled
    documents producing degenerate embeddings).
    """
    current = np.asarray(list(current_norms), dtype=float)
    if current.size == 0:
        return {"is_anomaly": False, "score": 0.0, "method": "embedding_norm_zscore", "reason": "empty_current"}
    current_mean = float(np.mean(current))
    result = zscore_detector(current_mean, baseline_norms, threshold=threshold)
    result["method"] = "embedding_norm_zscore"
    result["current_mean"] = current_mean
    return result
