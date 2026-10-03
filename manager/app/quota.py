"""Data quotas: usage per period, the period's start, and what the housekeeping loop should do.

Kept pure (no store, no clock of its own) so the decision is unit-testable. A quota and a realtime
bandwidth cap on the same forward are independent: the cap limits speed (applied in the kernel
traffic-control layer), the quota limits total data (enforced here by disabling the forward).
"""
from datetime import datetime, timedelta
from typing import Literal, Optional


def period_start(now: datetime, period: Literal["day", "week", "month"]) -> datetime:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return midnight
    if period == "week":   # weeks start on Monday
        return midnight - timedelta(days=now.weekday())
    return midnight.replace(day=1)


def quota_action(usage: int, limit: int, enabled: bool, disabled_by_quota: bool) -> Optional[Literal["disable", "enable"]]:
    """disable: over the limit and still on. enable: back under the limit (the period rolled over, or the
    quota was raised) and this forward was the one the quota switched off. Anything else: nothing to do."""
    over = usage >= limit
    if over and enabled:
        return "disable"
    if not over and not enabled and disabled_by_quota:
        return "enable"
    return None
