# AI Agent Decision Log

Lab was completed with Claude Code as the coding agent, working through the repo end-to-end. Key decisions below.

## Decision 1 — Freshness check vs. the existing public test fixture
- Hypothesis: `src/contract_validator.py` needed a real freshness check (`contract["freshness"]`) comparing the latest timestamp to wall-clock "now", per the TODO in the file and the STUDENT_API note that freshness is a hidden-eval case.
- Prompt / request to agent: implement type validation, freshness validation, and severity/action for the contract validator.
- Agent proposal: add `_check_freshness()` using `datetime.now(timezone.utc)` as the reference point. Doing so exposed that `tests_public/test_contracts.py`'s `healthy_df()` fixture used a **hardcoded** calendar date (`2026-08-28T...`), which recedes into the past every day the suite is run and would start failing freshness for reasons unrelated to the fixture's intent.
- Test/evidence: ran `pytest tests_public -q` before and after — freshness check alone flipped the previously-passing `test_healthy_contract_passes_starter_checks` to fail once "now" moved past the hardcoded date.
- Accept/reject/revise: revised — updated the fixture to build `created_at`/`updated_at` relative to `datetime.now()` (matching the pattern already used by `scripts/reset_lab.py` for the same reason), instead of weakening the freshness check itself.
- Why: the fixture, not the feature, was date-fragile. Every other timestamp-bearing file in the repo (`reset_lab.py`, `generate_data.py`) already re-anchors to "now" for exactly this reason, so aligning the test fixture is consistent with the codebase's own convention rather than a one-off hack.

## Decision 2 — MAD zero-edge-case and seasonality in `auto` mode
- Hypothesis: the starter `auto` method (naive z-score, context ignored) would false-negative on a real volume drop if the drop happened to land on a low-variance weekday segment, and `mad_detector`'s zero-MAD branch (`reason: "mad_is_zero_todo"`) was a known unhandled edge case.
- Prompt / request to agent: make `auto` context-aware (same-weekday baseline) and fix the MAD zero-edge-case instead of leaving it a silent always-`False`.
- Agent proposal: prefer `context["same_segment_history"]` over raw `history` when present, switch to MAD once >=5 baseline points exist (robust to the one bad point being tested), and fall back mad→std→exact-equality when MAD/std degenerate.
- Test/evidence: ran `python scripts/inject_fault.py volume_drop && python scripts/run_baseline.py` — `row_count_anomaly.is_anomaly=True`, score 5.53, using `auto:mad:same_segment_history`, while `dbt build` and `validate_orders()` both stayed green on the same truncated file (150/600 rows). Confirms the anomaly layer is the only one that catches this fault class.
- Accept/reject/revise: accepted.
- Why: this is the exact "seasonality" gap Phase 3 of the lab guide calls out, and the fix is verifiable against a fault the repo already ships a scenario for.

## Decision 3 — Multi-window burn-rate policy design
- Hypothesis: a single-threshold burn-rate check can't tell a brief spike apart from sustained fast burn, which is why the starter `evaluate_multiwindow_burn()` never pages.
- Prompt / request to agent: implement a real multi-window burn-rate policy; must not page on a short transient spike but must page on sustained fast burn.
- Agent proposal: two-tier policy following the Google SRE Workbook pattern — require **both** the short and long window burn rate to clear a tier's threshold (14.4x for page, 6x for ticket) before acting. A short-window-only spike (long window still low) is explicitly reported as "transient spike, suppressed" rather than paging.
- Test/evidence: manually checked both required cases — `short=20, long=1.5` → `page=False` ("transient spike"); `short=20, long=16` → `page=True`, `severity=critical`.
- Accept/reject/revise: accepted.
- Why: requiring long-window confirmation is what actually distinguishes "blip" from "sustained burn" — a single-window threshold cannot make that distinction by construction.

## Decision 4 — Fix the SCD join fan-out instead of only testing for it
- Hypothesis: `fct_daily_revenue.sql`'s join to `active_customers` (filtered on `is_active = true`, no dedup) inflates revenue if a customer ever has two active dimension rows, per the comment already in the starter SQL.
- Prompt / request to agent: write the smallest dbt unit test to expose the failure mode (per `docs/AI_AGENT_GUIDE.md`'s sample prompt), without touching the model first.
- Agent proposal: added `duplicate_active_customer_row_does_not_inflate_revenue` in `unit_tests.yml` (1 completed order for a customer with 2 synthetic active rows; expect 1 row / unchanged revenue). Ran it against the unmodified model first — it failed exactly as predicted (`daily_revenue` 50.0 → 100.0, `completed_order_rows` 1 → 2), proving the bug is real and the test catches it.
- Test/evidence: `dbt test --select test_type:unit` failing before the model change, passing after adding `qualify row_number() over (partition by customer_id order by valid_from desc) = 1` to the `active_customers` CTE. Also added `assert_revenue_reconciles_with_source.sql` (singular test) so any future re-introduction of the fan-out fails `dbt build` against the real seed data too, not only the synthetic unit-test fixture.
- Accept/reject/revise: extended beyond the original ask — fixed the model once the test proved the bug, rather than leaving a known-red test in the suite.
- Why: a red test that's expected to stay red isn't useful signal for later regressions; fixing the root cause and keeping the test as a regression guard is more valuable than "exposure only."

## Decision 5 — Distribution-shift detector: KS test instead of scipy
- Hypothesis: the starter mean-ratio-only detector in `observability/distribution.py` would miss a shape change (e.g. a bimodal split) that leaves the mean roughly unchanged.
- Prompt / request to agent: strengthen `detect_distribution_shift` per the Phase-advanced note ("KS test, PSI, quantile drift...") without adding scipy to `requirements.txt` (not in the starter's declared dependency set).
- Agent proposal: implemented a two-sample KS statistic manually with numpy (`searchsorted` against the merged, sorted value set) and compared it to the standard asymptotic critical value `1.36 * sqrt((n+m)/(n*m))`, OR'd with the existing mean-ratio check.
- Test/evidence: `pytest tests_public/test_distribution.py -q` still passes (extreme mean shift case); manually verified the KS branch trips independently for a same-mean, different-shape synthetic pair.
- Accept/reject/revise: accepted.
- Why: kept the dependency footprint unchanged (no scipy) while adding a second, independent signal rather than replacing the starter check outright.
