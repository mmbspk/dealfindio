"""
Weekly specials / flyer schedule for Australian supermarket chains.

Each chain publishes new specials on a known day-of-week cadence. This module
encodes those schedules and derives a per-chain freshness signal so the admin
panel can show how stale each chain's data is and surface chains that are
overdue for a scrape.

Schedule notes (AEST/AEDT, approximate — varies slightly by chain backend):
  - Coles:       Wednesdays ~00:00–08:00
  - Woolworths:  Wednesdays ~00:00–08:00
  - ALDI:        Wednesdays (Wed specials) + Saturdays (Sat specials) ~08:00
  - IGA:         Wednesdays ~00:00–08:00
  - Drakes:      Wednesdays ~00:00–08:00

These are time *windows*, not exact timestamps — the backend typically updates
overnight. We model this as "a new cycle starts on <weekday> each week".
Staleness = time since the most-recent expected refresh that has already passed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class ChainSchedule:
    chain: str
    label: str
    refresh_days: tuple[int, ...]  # ISO weekday: 1=Mon … 7=Sun
    # How many hours after midnight on the refresh day the new data typically
    # appears.  We add this as a grace buffer before marking stale.
    grace_hours: int = 10
    # A run is "fresh" if it happened within this many hours of the expected
    # refresh.  Anything older than cycle_hours is stale.
    cycle_hours: int = 7 * 24  # one week


# The single source of truth for chain schedules.
SCHEDULES: dict[str, ChainSchedule] = {
    "coles": ChainSchedule(
        chain="coles",
        label="Coles",
        refresh_days=(3,),  # Wednesday
        grace_hours=10,
    ),
    "woolworths": ChainSchedule(
        chain="woolworths",
        label="Woolworths",
        refresh_days=(3,),  # Wednesday
        grace_hours=10,
    ),
    "aldi": ChainSchedule(
        chain="aldi",
        label="ALDI",
        refresh_days=(3, 6),  # Wednesday + Saturday
        grace_hours=12,
    ),
    "iga": ChainSchedule(
        chain="iga",
        label="IGA",
        refresh_days=(3,),  # Wednesday
        grace_hours=10,
    ),
    "drakes": ChainSchedule(
        chain="drakes",
        label="Drakes",
        refresh_days=(3,),  # Wednesday
        grace_hours=10,
    ),
}


@dataclass
class ChainFreshness:
    chain: str
    label: str
    last_run_at: Optional[datetime]
    last_refresh_expected_at: Optional[datetime]
    hours_since_run: Optional[float]
    hours_overdue: Optional[float]  # positive = overdue, negative = still fresh
    status: str  # "fresh" | "stale" | "overdue" | "never"
    refresh_days_labels: list[str]


_WEEKDAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def _last_expected_refresh(schedule: ChainSchedule, now: datetime) -> Optional[datetime]:
    """Return the most-recent past datetime when this chain's specials should
    have gone live, respecting the grace_hours buffer.

    Walks back up to 14 days to find the most recent scheduled refresh day
    that has already passed (including the grace window).
    """
    for days_back in range(14):
        candidate = (now - timedelta(days=days_back)).replace(
            hour=schedule.grace_hours, minute=0, second=0, microsecond=0
        )
        # candidate.isoweekday(): 1=Mon … 7=Sun
        if candidate.isoweekday() in schedule.refresh_days and candidate <= now:
            return candidate
    return None


def compute_freshness(
    chain: str,
    last_run_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> ChainFreshness:
    """Compute freshness for a single chain given its last successful run time."""
    now = now or datetime.now(timezone.utc)
    schedule = SCHEDULES.get(chain)
    if schedule is None:
        # Unknown chain — treat as stale with no schedule info
        return ChainFreshness(
            chain=chain,
            label=chain.replace("_", " ").title(),
            last_run_at=last_run_at,
            last_refresh_expected_at=None,
            hours_since_run=(now - last_run_at).total_seconds() / 3600 if last_run_at else None,
            hours_overdue=None,
            status="never" if last_run_at is None else "stale",
            refresh_days_labels=[],
        )

    expected = _last_expected_refresh(schedule, now)
    refresh_days_labels = [_WEEKDAY_NAMES[d] for d in sorted(schedule.refresh_days)]

    if last_run_at is None:
        return ChainFreshness(
            chain=chain,
            label=schedule.label,
            last_run_at=None,
            last_refresh_expected_at=expected,
            hours_since_run=None,
            hours_overdue=None,
            status="never",
            refresh_days_labels=refresh_days_labels,
        )

    # Normalise to UTC — DB may store naive or tz-aware datetimes
    if last_run_at.tzinfo is None:
        last_run_at = last_run_at.replace(tzinfo=timezone.utc)
    else:
        from datetime import timezone as _tz

        last_run_at = last_run_at.astimezone(_tz.utc)

    hours_since = (now - last_run_at).total_seconds() / 3600

    # Overdue = time since the last expected refresh minus time since our last run.
    # Positive means we missed the refresh window; negative means we ran after it.
    hours_overdue: Optional[float] = None
    if expected is not None:
        hours_overdue = (expected - last_run_at).total_seconds() / 3600

    if hours_since > schedule.cycle_hours:
        status = "overdue"
    elif hours_overdue is not None and hours_overdue > 0:
        status = "stale"
    else:
        status = "fresh"

    return ChainFreshness(
        chain=chain,
        label=schedule.label,
        last_run_at=last_run_at,
        last_refresh_expected_at=expected,
        hours_since_run=round(hours_since, 1),
        hours_overdue=round(hours_overdue, 1) if hours_overdue is not None else None,
        status=status,
        refresh_days_labels=refresh_days_labels,
    )


def all_chain_freshness(
    last_runs: dict[str, Optional[datetime]],
    now: Optional[datetime] = None,
) -> list[ChainFreshness]:
    """Compute freshness for all known chains.

    ``last_runs`` maps chain name -> last successful run datetime (or None).
    Chains in SCHEDULES but absent from last_runs are treated as never-run.
    """
    now = now or datetime.now(timezone.utc)
    result = []
    # Known chains first (in SCHEDULES order), then any extras in last_runs
    seen: set[str] = set()
    for chain in SCHEDULES:
        seen.add(chain)
        result.append(compute_freshness(chain, last_runs.get(chain), now))
    for chain, ts in last_runs.items():
        if chain not in seen:
            result.append(compute_freshness(chain, ts, now))
    return result
