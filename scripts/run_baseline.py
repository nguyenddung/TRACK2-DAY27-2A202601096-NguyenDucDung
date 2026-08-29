#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability.anomaly import detect_anomaly
from observability.lineage import get_downstream_assets
from observability.rag_metrics import detect_text_length_shift
from observability.slo import calculate_slo, evaluate_multiwindow_burn
from src.contract_validator import failed_issues, load_contract, pipeline_action, validate_dataframe
from src.io_utils import load_jsonl, load_yaml


def kb_freshness(docs: list[dict[str, Any]], kb_contract: dict[str, Any], *, now: datetime | None = None):
    """KB freshness/SLO was intentionally left unfinished in the starter --
    the `stale_kb` fault only means something once staleness is measured
    against `contracts/kb_contract.yaml` and rolled into an SLO."""
    fresh_cfg = kb_contract.get("freshness", {})
    max_delay = fresh_cfg.get("max_delay_minutes", 60)
    now = now or datetime.now(timezone.utc)
    delays = []
    for doc in docs:
        published = pd.to_datetime(doc.get("published_at"), utc=True, errors="coerce")
        if pd.isna(published):
            continue
        delays.append((pd.Timestamp(now) - published).total_seconds() / 60.0)
    bad_events = sum(1 for d in delays if d > max_delay)
    total_events = len(delays)
    return {
        "max_delay_minutes": max_delay,
        "doc_delays_minutes": [round(d, 2) for d in delays],
        "stale_doc_count": bad_events,
        "total_docs": total_events,
    }


def main() -> None:
    config = load_yaml(ROOT / "lab_config.yaml")

    orders = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    history = pd.read_csv(ROOT / "data" / "history" / "metrics_history.csv")
    contract = load_contract(ROOT / "contracts" / "orders_contract.yaml")
    issues = validate_dataframe(orders, contract)
    failed = failed_issues(issues)
    critical_failed = failed_issues(issues, min_severity="critical")
    action = pipeline_action(issues)

    # Segment history by weekday before calling the detector: `auto` prefers
    # `context["same_segment_history"]` over the raw (season-mixed) history.
    current_dow = datetime.now().weekday()
    segment = history.loc[history["day_of_week"] == current_dow, "row_count"].tail(8).tolist()
    row_result = detect_anomaly(
        len(orders),
        history["row_count"].tail(14).tolist(),
        method="auto",
        context={
            "metric_name": "row_count",
            "day_of_week": current_dow,
            "same_segment_history": segment if len(segment) >= 3 else None,
        },
    )

    updated = pd.to_datetime(orders["updated_at"], utc=True, errors="coerce")
    freshness_minutes = (
        pd.Timestamp(datetime.now(timezone.utc)) - updated.max()
    ).total_seconds() / 60.0
    revenue_freshness_cfg = config["slo"]["revenue_freshness"]
    revenue_freshness_slo = calculate_slo(
        revenue_freshness_cfg["target"],
        bad_events=int(freshness_minutes > revenue_freshness_cfg["threshold_minutes"]),
        total_events=1,
    )

    docs = load_jsonl(ROOT / "data" / "incoming" / "kb_documents.jsonl")
    kb_contract = load_contract(ROOT / "contracts" / "kb_contract.yaml")
    kb_fresh = kb_freshness(docs, kb_contract)
    kb_freshness_cfg = config["slo"]["rag_index_freshness"]
    kb_freshness_slo = calculate_slo(
        kb_freshness_cfg["target"],
        bad_events=kb_fresh["stale_doc_count"],
        total_events=max(kb_fresh["total_docs"], 1),
    )

    text_result = detect_text_length_shift(
        [d["content"] for d in docs], history["mean_text_length"].tail(14).tolist()
    )

    # Demo SLO: one check event for this run's critical contract checks.
    bad = 1 if critical_failed else 0
    contract_slo_cfg = config["slo"]["critical_contract_pass"]
    contract_slo = calculate_slo(contract_slo_cfg["target"], bad_events=bad, total_events=1)

    # Illustrative multi-window burn: this run's burn rate stands in for the
    # short window; the long window reuses the same signal since a single
    # `make baseline` run has no rolling window history to draw from.
    burn_policy = evaluate_multiwindow_burn(
        short_window_burn=contract_slo["burn_rate"],
        long_window_burn=contract_slo["burn_rate"],
    )

    with open(ROOT / "data" / "baseline" / "lineage_graph.json", "r", encoding="utf-8") as f:
        lineage = json.load(f)["dataset_lineage"]
    blast_radius = get_downstream_assets(lineage, "stg_orders")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "orders_rows": int(len(orders)),
        "failed_contract_checks": len(failed),
        "critical_contract_failures": len(critical_failed),
        "pipeline_action": action,
        "row_count_anomaly": row_result,
        "freshness_minutes": freshness_minutes,
        "revenue_freshness_slo": revenue_freshness_slo,
        "kb_freshness": kb_fresh,
        "kb_freshness_slo": kb_freshness_slo,
        "kb_text_length_signal": text_result,
        "contract_slo": contract_slo,
        "burn_policy": burn_policy,
        "sample_blast_radius_from_stg_orders": blast_radius,
    }
    out = ROOT / "reports" / "latest_metrics.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("=== DATA RELIABILITY BASELINE ===")
    print(f"orders rows              : {len(orders)}")
    print(f"contract failed checks   : {len(failed)}")
    print(f"critical contract fails  : {len(critical_failed)}")
    print(f"pipeline action          : {action}")
    print(f"row-count anomaly        : {row_result['is_anomaly']} ({row_result['method']}, score={row_result['score']:.2f})")
    print(f"freshness minutes        : {freshness_minutes:.1f} (SLO breached={revenue_freshness_slo['breached']})")
    print(f"KB stale docs            : {kb_fresh['stale_doc_count']}/{kb_fresh['total_docs']} (SLO breached={kb_freshness_slo['breached']})")
    print(f"KB length anomaly        : {text_result['is_anomaly']}")
    print(f"contract burn rate       : {contract_slo['burn_rate']:.2f} (page={burn_policy['page']}, severity={burn_policy['severity']})")
    print(f"sample blast radius      : {', '.join(blast_radius)}")
    print(f"report                    : {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
