# Incident Report

## Severity
P1 — CEO-visible revenue metric materially wrong; downstream Support Agent blast radius confirmed but not yet impacted at time of detection.

## Summary
`data/incoming/orders.csv` was partially ingested (`scripts/inject_fault.py volume_drop`, kept 150 of 600 raw order rows — a 75% row-count drop). Every deterministic guardrail (contract validation, GX suite, dbt tests) still reported **green**, because a partial-but-well-formed file violates no column-level rule: every remaining row is still not-null, unique, in the accepted value sets, and in range. Only the anomaly detector, which compares today's row count against the same-weekday history instead of a fixed threshold, caught it. This is the exact "pipeline SUCCESS but data is wrong" scenario described in the lab README.

## Detection
- **Signal**: `detect_metric(len(orders), history, method="auto", context={"same_segment_history": ...})` → `is_anomaly=True`, method `auto:mad:same_segment_history`, score 5.53 (threshold 3.5). Reason: today's count (150) sits far outside the median/MAD band of the last 8 same-weekday readings.
- **Not caught by**: `validate_orders()` (0 failed checks — file structurally healthy), `dbt build` (all 20 data tests + 2 unit tests pass — no column constraint is violated by a smaller-but-clean file), GX checkpoint (all 6 expectations pass).
- **First observed**: at `make baseline` time, via `reports/latest_metrics.json.row_count_anomaly`.

## Root Cause
Partial/truncated ingestion upstream of `data/incoming/orders.csv` (simulated here by `inject_fault.py volume_drop`, which keeps only the first 25% of rows). In a real system this maps to a source-extract job stopping early, a paginated API call losing pages, or a scheduler re-running before the full upstream export finished.

## Evidence
1. **Row count**: `orders_rows` dropped from a healthy 600 to 150 (-75%), while `failed_contract_checks = 0` and `critical_contract_failures = 0` — proves the file is well-formed but incomplete, not corrupted.
2. **Anomaly detector**: `row_count_anomaly.is_anomaly = True`, `score = 5.53`, method `auto:mad:same_segment_history` — the same-weekday MAD baseline (robust to the single point that would otherwise skew a mean/std check) flags the drop that a naive fixed-threshold rule would miss on a day with different expected volume.
3. **dbt build is green**: `PASS=20 WARN=0 ERROR=0` including the new `assert_revenue_reconciles_with_source` and the `duplicate_active_customer_row_does_not_inflate_revenue` unit test — confirms the transformation logic itself is correct; the problem is upstream data completeness, not the SQL.
4. **Revenue impact, measured directly from `fct_daily_revenue`**:
   - Healthy: `2026-08-29 | completed_order_rows=290 | daily_revenue=$18,961.04`
   - Post-fault: `2026-08-29 | completed_order_rows=66 | daily_revenue=$4,308.42`
   - **-77.3% revenue for the day** — this is the "CEO sees revenue drop" symptom from the lab scenario, reproduced with real numbers from the warehouse.

## Blast Radius
Computed via `downstream_assets(lineage_graph, "stg_orders")` (BFS over `data/baseline/lineage_graph.json`):

```text
stg_orders -> fct_daily_revenue -> ceo_revenue_dashboard
```

`stg_customers` and the `kb_documents -> kb_active_docs -> rag_index -> support_agent` chain are unaffected by this specific fault (KB freshness and text-length signals both stayed healthy during this incident). Only the revenue dashboard is in the blast radius.

## Mitigation
1. Quarantine the current `data/incoming/orders.csv` — `pipeline_action` from `src.contract_validator.pipeline_action()` returns `"pass"` here (no contract rule fires), so mitigation must be triggered by the anomaly signal, not the contract layer alone. This is the practical argument for wiring anomaly results into the same block/quarantine/warn decision the contract validator already produces.
2. Re-run the upstream extract/ingestion job for the affected window instead of re-running downstream `dbt build` — the dbt layer is not at fault and rebuilding it against the truncated file only reproduces the wrong number faster.
3. Re-ingest the full file and re-run `make baseline`.

## Recovery
```bash
make reset      # restores the full 600-row healthy snapshot
make baseline    # regenerates reports/latest_metrics.json
```

## Verification
- [x] Contract healthy — `failed_contract_checks = 0`, `critical_contract_failures = 0`
- [x] dbt tests healthy — `PASS=20 WARN=0 ERROR=0`, `daily_revenue` back to $18,961.04
- [x] Anomaly returned to expected range — `row_count_anomaly.is_anomaly = False` after `make reset`
- [x] SLO healthy / budget understood — `revenue_freshness_slo.breached = False`, `contract_slo.burn_rate = 0.0`
- [x] Downstream output verified — `fct_daily_revenue` and `ceo_revenue_dashboard` blast-radius nodes both reflect the restored 600-row baseline

## Prevention / Action Items
| Action | Owner | Deadline | Why |
|---|---|---|---|
| Wire anomaly `is_anomaly` results into `pipeline_action` (today it only reads contract issues) so a volume drop can trigger `quarantine`, not just a dashboard flag | Data/AI Reliability Team | next sprint | Contract checks alone gave a false "pass" for a 75% row-count drop; the pipeline decision should reflect every detector, not only deterministic column rules |
| Add a row-count *floor* contract rule (e.g. `min_rows` relative to a rolling baseline) alongside the statistical anomaly detector | commerce-data (orders owner) | next sprint | Belt-and-suspenders: a simple floor check is cheap, auditable, and catches the case even if the statistical baseline is ever misconfigured |
| Add ingestion-side row-count completeness check (compare rows written vs. rows expected from the source system) before the file lands in `data/incoming/` | upstream ingestion owner | 2 sprints | Shifts detection left, before the bad file ever reaches the pipeline that feeds the CEO dashboard |
