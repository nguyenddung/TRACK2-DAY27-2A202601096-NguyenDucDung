from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "latest_metrics.json"
HISTORY = ROOT / "data" / "history" / "metrics_history.csv"
ORDERS_CONTRACT = ROOT / "contracts" / "orders_contract.yaml"
KB_CONTRACT = ROOT / "contracts" / "kb_contract.yaml"

st.set_page_config(page_title="Data Reliability Lab", layout="wide")
st.title("Data Reliability Game Day")
st.caption("Detect -> Triage -> Root Cause -> Blast Radius -> Mitigate -> Verify Recovery")

if not REPORT.exists():
    st.warning("Run `make baseline` first to generate reports/latest_metrics.json")
    st.stop()

report = json.loads(REPORT.read_text(encoding="utf-8"))


def incident_status(report: dict) -> tuple[str, str]:
    """Roll every signal in the report up into one status word."""
    if report.get("pipeline_action") == "block":
        return "CRITICAL", "red"
    slos = [
        report.get("revenue_freshness_slo", {}).get("breached", False),
        report.get("kb_freshness_slo", {}).get("breached", False),
    ]
    anomalies = [
        report.get("row_count_anomaly", {}).get("is_anomaly", False),
        report.get("kb_text_length_signal", {}).get("is_anomaly", False),
    ]
    if report.get("burn_policy", {}).get("page"):
        return "CRITICAL", "red"
    if any(slos) or any(anomalies) or report.get("pipeline_action") == "quarantine":
        return "DEGRADED", "orange"
    return "HEALTHY", "green"


status, color = incident_status(report)
st.markdown(f"### Incident status: :{color}[{status}]")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Orders rows", report["orders_rows"])
c2.metric("Freshness (min)", f"{report['freshness_minutes']:.1f}")
c3.metric("Contract failures", report["failed_contract_checks"])
c4.metric("Critical failures", report["critical_contract_failures"])
c5.metric("Pipeline action", report.get("pipeline_action", "n/a"))

st.subheader("SLO / error budget")
slo_cols = st.columns(3)
slo_items = [
    ("Revenue freshness", report.get("revenue_freshness_slo")),
    ("KB freshness", report.get("kb_freshness_slo")),
    ("Critical contract pass", report.get("contract_slo")),
]
for col, (label, slo) in zip(slo_cols, slo_items):
    with col:
        st.markdown(f"**{label}**")
        if not slo:
            st.write("n/a")
            continue
        st.write(f"target: {slo['target']:.3%}")
        st.write(f"actual bad rate: {slo['actual_bad_rate']:.3%}")
        st.write(f"burn rate: {slo['burn_rate']:.2f}x")
        st.progress(max(0.0, min(1.0, slo["remaining_error_budget_fraction"])))
        st.write(f"remaining budget: {slo['remaining_error_budget_fraction']:.1%}")
        if slo["breached"]:
            st.error("SLO breached")

st.subheader("Burn-rate policy")
burn = report.get("burn_policy")
if burn:
    st.write(f"page: **{burn['page']}** | severity: **{burn['severity']}**")
    st.caption(burn["reason"])

st.subheader("Current signals")
st.json({
    "row_count_anomaly": report["row_count_anomaly"],
    "kb_text_length_signal": report["kb_text_length_signal"],
    "kb_freshness": report.get("kb_freshness"),
})

history = pd.read_csv(HISTORY)
st.subheader("Historical row count")
st.line_chart(history.set_index("date")[["row_count"]])

st.subheader("Blast radius")
st.write("stg_orders -> " + " -> ".join(report["sample_blast_radius_from_stg_orders"]))

st.subheader("Ownership & runbook")
orders_contract = yaml.safe_load(ORDERS_CONTRACT.read_text(encoding="utf-8"))
kb_contract = yaml.safe_load(KB_CONTRACT.read_text(encoding="utf-8"))
own_col1, own_col2 = st.columns(2)
own_col1.write(f"**orders** owner: `{orders_contract.get('owner')}`")
own_col2.write(f"**kb_documents** owner: `{kb_contract.get('owner')}`")
st.write("Runbook: [docs/LAB_GUIDE.md](../docs/LAB_GUIDE.md) (Phase 6: Mystery incident) · "
         "Incident template: [reports/incident_report.md](../reports/incident_report.md)")
