"""Contract validator.

Deterministic checks for a tabular dataset against a YAML data contract:
not-null / unique / accepted-values / numeric-range (starter), plus
type validation, freshness validation, and severity-aware actions
(block / quarantine / warn) added on top of the starter baseline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

# Severity ordering (low -> high) and the pipeline action each severity
# implies when a check fails. `critical` failures should stop the pipeline,
# `warning` failures should quarantine the affected rows/dataset for review,
# and `info` failures are surfaced but do not change pipeline behavior.
SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
ACTION_BY_SEVERITY = {"critical": "block", "warning": "quarantine", "info": "warn"}


def _issue(
    check: str,
    *,
    column: str | None,
    severity: str,
    passed: bool,
    details: str,
) -> dict[str, Any]:
    return {
        "check": check,
        "column": column,
        "severity": severity,
        "passed": bool(passed),
        "details": details,
        "action": "none" if passed else ACTION_BY_SEVERITY.get(severity, "warn"),
    }


def load_contract(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _type_invalid_count(series: pd.Series, declared_type: str) -> int:
    """Count non-null values that do not match the declared contract type.

    `pd.to_numeric(..., errors="coerce")` silently turns unparsable strings
    into NaN, which would otherwise hide type drift (e.g. amount arriving as
    "N/A" or order_id arriving as "ORD-1"). We compare against the non-null
    count explicitly instead of relying on downstream NaN propagation.
    """
    non_null = series.dropna()
    if non_null.empty:
        return 0

    declared_type = (declared_type or "").lower()

    if declared_type in {"integer", "int"}:
        numeric = pd.to_numeric(non_null, errors="coerce")
        bad_coerce = int(numeric.isna().sum())
        fractional = int(((numeric.dropna() % 1) != 0).sum())
        return bad_coerce + fractional

    if declared_type in {"number", "float", "double"}:
        numeric = pd.to_numeric(non_null, errors="coerce")
        return int(numeric.isna().sum())

    if declared_type in {"datetime", "timestamp", "date"}:
        parsed = pd.to_datetime(non_null, errors="coerce", utc=True)
        return int(parsed.isna().sum())

    if declared_type in {"boolean", "bool"}:
        valid = {True, False, "true", "false", "True", "False", 0, 1, "0", "1"}
        return int((~non_null.isin(valid)).sum())

    # "string"/unknown declared types: anything can be stringified, so there is
    # no meaningful type-drift signal to check deterministically here.
    return 0


def _check_freshness(
    df: pd.DataFrame,
    freshness_cfg: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if not freshness_cfg:
        return None

    column = freshness_cfg.get("column")
    max_delay = freshness_cfg.get("max_delay_minutes")
    severity = freshness_cfg.get("severity", "warning")
    if not column or column not in df.columns or max_delay is None:
        return None

    reference = pd.Timestamp(now or datetime.now(timezone.utc))
    if reference.tzinfo is None:
        reference = reference.tz_localize("UTC")

    parsed = pd.to_datetime(df[column], utc=True, errors="coerce")
    if parsed.notna().sum() == 0:
        return _issue(
            "freshness",
            column=column,
            severity=severity,
            passed=False,
            details="no_valid_timestamps",
        )

    latest = parsed.max()
    delay_minutes = (reference - latest).total_seconds() / 60.0
    passed = delay_minutes <= max_delay
    return _issue(
        "freshness",
        column=column,
        severity=severity,
        passed=passed,
        details=f"delay_minutes={delay_minutes:.2f}, max_delay_minutes={max_delay}",
    )


def validate_dataframe(
    df: pd.DataFrame,
    contract: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    columns = contract.get("columns", {})

    for column, rules in columns.items():
        severity = rules.get("severity", "warning")
        required = bool(rules.get("required", False))

        if column not in df.columns:
            if required:
                issues.append(
                    _issue(
                        "required_column",
                        column=column,
                        severity=severity,
                        passed=False,
                        details=f"Missing required column: {column}",
                    )
                )
            continue

        series = df[column]

        if required:
            null_count = int(series.isna().sum())
            issues.append(
                _issue(
                    "not_null",
                    column=column,
                    severity=severity,
                    passed=(null_count == 0),
                    details=f"null_count={null_count}",
                )
            )

        if rules.get("unique"):
            duplicate_count = int(series.duplicated(keep=False).sum())
            issues.append(
                _issue(
                    "unique",
                    column=column,
                    severity=severity,
                    passed=(duplicate_count == 0),
                    details=f"duplicate_rows={duplicate_count}",
                )
            )

        accepted = rules.get("accepted_values")
        if accepted is not None:
            invalid_mask = series.notna() & ~series.isin(accepted)
            invalid_count = int(invalid_mask.sum())
            issues.append(
                _issue(
                    "accepted_values",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}; accepted={accepted}",
                )
            )

        declared_type = rules.get("type")
        if declared_type:
            type_invalid_count = _type_invalid_count(series, declared_type)
            issues.append(
                _issue(
                    "type",
                    column=column,
                    severity=severity,
                    passed=(type_invalid_count == 0),
                    details=f"declared_type={declared_type}; invalid_count={type_invalid_count}",
                )
            )

        # Starter numeric range support.
        if "min" in rules or "max" in rules:
            numeric = pd.to_numeric(series, errors="coerce")
            invalid = pd.Series(False, index=series.index)
            if "min" in rules:
                invalid |= numeric < rules["min"]
            if "max" in rules:
                invalid |= numeric > rules["max"]
            invalid_count = int(invalid.fillna(False).sum())
            issues.append(
                _issue(
                    "range",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}",
                )
            )

    freshness_issue = _check_freshness(df, contract.get("freshness"), now=now)
    if freshness_issue is not None:
        issues.append(freshness_issue)

    return issues


def failed_issues(issues: list[dict[str, Any]], min_severity: str | None = None) -> list[dict[str, Any]]:
    failed = [i for i in issues if not i.get("passed", False)]
    if min_severity is None:
        return failed
    threshold = SEVERITY_ORDER[min_severity]
    return [i for i in failed if SEVERITY_ORDER.get(i.get("severity", "warning"), 1) >= threshold]


def pipeline_action(issues: list[dict[str, Any]]) -> str:
    """Roll failed issues up into a single pipeline decision.

    `block` wins over `quarantine`, which wins over `warn`. Returns `pass`
    when there are no failed issues at all.
    """
    failed = failed_issues(issues)
    if not failed:
        return "pass"
    worst_severity = max(failed, key=lambda i: SEVERITY_ORDER.get(i.get("severity", "warning"), 1))
    return ACTION_BY_SEVERITY.get(worst_severity.get("severity", "warning"), "warn")
