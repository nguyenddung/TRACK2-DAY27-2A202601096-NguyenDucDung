from __future__ import annotations

from typing import Any


def calculate_slo(target: float, bad_events: int, total_events: int) -> dict[str, Any]:
    if not 0 < target < 1:
        raise ValueError("target must be between 0 and 1 (exclusive)")
    if bad_events < 0 or total_events < 0 or bad_events > total_events:
        raise ValueError("invalid event counts")
    allowed_bad_rate = 1.0 - target
    if total_events == 0:
        return {
            "target": target,
            "actual_bad_rate": 0.0,
            "allowed_bad_rate": allowed_bad_rate,
            "burn_rate": 0.0,
            "remaining_error_budget_fraction": 1.0,
            "breached": False,
        }
    actual_bad_rate = bad_events / total_events
    burn_rate = actual_bad_rate / allowed_bad_rate
    consumed_fraction = min(1.0, actual_bad_rate / allowed_bad_rate)
    return {
        "target": target,
        "actual_bad_rate": actual_bad_rate,
        "allowed_bad_rate": allowed_bad_rate,
        "burn_rate": burn_rate,
        "remaining_error_budget_fraction": max(0.0, 1.0 - consumed_fraction),
        "breached": bool(actual_bad_rate > allowed_bad_rate),
    }


#  Google SRE Workbook multi-window burn-rate thresholds. Requiring BOTH the
#  short and the long window to exceed a tier's threshold is what tells a
#  transient spike (short window hot, long window still cool because the bad
#  interval gets diluted) apart from sustained burn (both windows hot).
PAGE_BURN_THRESHOLD = 14.4   # ~2% of a 30-day budget in 1 hour if sustained
TICKET_BURN_THRESHOLD = 6.0  # ~5% of a 30-day budget in 6 hours if sustained


def evaluate_multiwindow_burn(
    *,
    short_window_burn: float,
    long_window_burn: float,
    policy: str = "two_window",
) -> dict[str, Any]:
    """Two-window burn-rate policy (page / ticket / info).

    - page (critical): short AND long window burn both exceed the fast-burn
      threshold -> sustained fast burn, wake someone up.
    - ticket (warning): short AND long window burn both exceed the
      slow-burn threshold (but not the page threshold) -> sustained slow
      burn, file a ticket, no page.
    - info: everything else, including a short-window spike that the long
      window does not confirm (a transient blip that should not page).
    """
    if short_window_burn >= PAGE_BURN_THRESHOLD and long_window_burn >= PAGE_BURN_THRESHOLD:
        return {
            "page": True,
            "severity": "critical",
            "reason": (
                f"short_window_burn={short_window_burn:.2f} and long_window_burn={long_window_burn:.2f} "
                f"both >= page threshold {PAGE_BURN_THRESHOLD} -> sustained fast burn"
            ),
            "short_window_burn": short_window_burn,
            "long_window_burn": long_window_burn,
            "policy": policy,
        }

    if short_window_burn >= TICKET_BURN_THRESHOLD and long_window_burn >= TICKET_BURN_THRESHOLD:
        return {
            "page": False,
            "severity": "warning",
            "reason": (
                f"short_window_burn={short_window_burn:.2f} and long_window_burn={long_window_burn:.2f} "
                f"both >= ticket threshold {TICKET_BURN_THRESHOLD} -> sustained slow burn, file a ticket"
            ),
            "short_window_burn": short_window_burn,
            "long_window_burn": long_window_burn,
            "policy": policy,
        }

    if short_window_burn >= PAGE_BURN_THRESHOLD and long_window_burn < TICKET_BURN_THRESHOLD:
        reason = (
            f"short_window_burn={short_window_burn:.2f} is high but long_window_burn={long_window_burn:.2f} "
            f"stays below {TICKET_BURN_THRESHOLD} -> transient spike, alert suppressed"
        )
    else:
        reason = (
            f"short_window_burn={short_window_burn:.2f}, long_window_burn={long_window_burn:.2f} "
            f"below alerting thresholds"
        )
    return {
        "page": False,
        "severity": "info",
        "reason": reason,
        "short_window_burn": short_window_burn,
        "long_window_burn": long_window_burn,
        "policy": policy,
    }
