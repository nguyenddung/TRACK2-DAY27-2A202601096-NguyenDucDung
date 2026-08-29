#!/usr/bin/env python3
"""Great Expectations Core 1.x validation flow for the orders dataset.

Builds a reusable Expectation Suite, wires it to a Validation Definition and
a Checkpoint (with an UpdateDataDocsAction), runs it against the current
`data/incoming/orders.csv`, and turns the per-expectation results into a
severity-aware pipeline decision (block / quarantine / warn) consistent with
`src/contract_validator.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import great_expectations as gx
except ImportError as exc:  # friendlier classroom failure
    raise SystemExit("great_expectations is not installed. Run: pip install -r requirements.txt") from exc

from src.contract_validator import ACTION_BY_SEVERITY, SEVERITY_ORDER


def build_suite(context: "gx.data_context.AbstractDataContext") -> "gx.ExpectationSuite":
    suite = gx.ExpectationSuite(name="orders_suite")
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToNotBeNull(column="order_id", meta={"severity": "critical"})
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeUnique(column="order_id", meta={"severity": "critical"})
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeBetween(column="amount", min_value=0, meta={"severity": "critical"})
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="currency", value_set=["USD", "VND"], meta={"severity": "critical"}
        )
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="status",
            value_set=["pending", "completed", "refunded", "cancelled"],
            meta={"severity": "warning"},
        )
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToNotBeNull(column="customer_id", meta={"severity": "critical"})
    )
    return context.suites.add(suite)


def run_checkpoint(df: pd.DataFrame) -> "gx.checkpoint.checkpoint.CheckpointResult":
    context = gx.get_context()
    suite = build_suite(context)

    data_source = context.data_sources.add_pandas("orders_pandas")
    asset = data_source.add_dataframe_asset(name="orders_dataframe")
    batch_definition = asset.add_batch_definition_whole_dataframe("whole_orders")

    validation_definition = context.validation_definitions.add(
        gx.ValidationDefinition(name="orders_validation", data=batch_definition, suite=suite)
    )

    checkpoint = context.checkpoints.add(
        gx.Checkpoint(
            name="orders_checkpoint",
            validation_definitions=[validation_definition],
            actions=[gx.checkpoint.UpdateDataDocsAction(name="update_data_docs")],
        )
    )
    return checkpoint.run(batch_parameters={"dataframe": df})


def summarize(result: "gx.checkpoint.checkpoint.CheckpointResult") -> dict[str, Any]:
    """Flatten checkpoint results into per-expectation rows with severity and
    an implied action, then roll up the worst severity into one pipeline
    decision -- the same block/quarantine/warn vocabulary used by the
    deterministic contract validator.
    """
    rows: list[dict[str, Any]] = []
    for validation_result in result.run_results.values():
        for expectation_result in validation_result.results:
            severity = expectation_result.expectation_config.meta.get("severity", "warning")
            passed = bool(expectation_result.success)
            rows.append(
                {
                    "expectation": expectation_result.expectation_config.type,
                    "column": expectation_result.expectation_config.kwargs.get("column"),
                    "severity": severity,
                    "passed": passed,
                    "unexpected_count": expectation_result.result.get("unexpected_count"),
                    "action": "none" if passed else ACTION_BY_SEVERITY.get(severity, "warn"),
                }
            )

    failed = [r for r in rows if not r["passed"]]
    if failed:
        worst = max(failed, key=lambda r: SEVERITY_ORDER.get(r["severity"], 1))
        pipeline_action = ACTION_BY_SEVERITY.get(worst["severity"], "warn")
    else:
        pipeline_action = "pass"

    return {"success": bool(result.success), "expectations": rows, "pipeline_action": pipeline_action}


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    result = run_checkpoint(df)
    summary = summarize(result)

    for row in summary["expectations"]:
        status = "PASS" if row["passed"] else "FAIL"
        print(
            f"{row['expectation']:<40} column={row['column']!s:<12} severity={row['severity']:<8} "
            f"{status:<4} action={row['action']}"
        )

    print(f"\nGX checkpoint result: {'PASS' if summary['success'] else 'FAIL'}")
    print(f"Pipeline action: {summary['pipeline_action']}")

    out = ROOT / "reports" / "gx_result.json"
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"Report written to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
