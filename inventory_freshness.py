"""Watchdog for the Pipe17 -> HubSpot inventory sync.

The inventory job stamps `inventory_last_synced_at` on every product it matches. If the
job stops running (scheduler paused, invoker permission missing, crash on every run),
nothing inside the job can report it. This module lets a job that IS running (the daily
reconcile sweep) read the newest stamp from HubSpot and say whether inventory is fresh.

Background: from 2026-10-06 to 2026-11-09 the scheduler's invoker SA lacked
roles/run.invoker on pipe17-hubspot-inventory, every scheduled call got a 403, and
HubSpot showed a frozen snapshot for weeks with no alert.

The pure core (`freshness_message`) is unit-tested offline in
selftest_inventory_freshness.py; `latest_synced_at` is the only network call.
"""
import datetime as dt

import requests

from config import (
    HUBSPOT_TOKEN, HUBSPOT_API_BASE, HS_PRODUCT_OBJECT,
    HS_INVENTORY_SYNCED_AT_PROP, INVENTORY_STALE_HOURS,
)


def parse_hs_datetime(value):
    """HubSpot returns datetime properties as ISO strings ('2026-11-09T18:55:53.804Z')
    or, for some reads, epoch milliseconds. Returns an aware UTC datetime or None."""
    if value in (None, ""):
        return None
    s = str(value).strip()
    if s.isdigit():
        return dt.datetime.fromtimestamp(int(s) / 1000, tz=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def freshness_message(latest, now, max_age_hours=INVENTORY_STALE_HOURS):
    """(is_stale, slack_line) for the newest inventory stamp.

    latest: aware datetime of the newest `inventory_last_synced_at`, or None if no
    product has ever been stamped."""
    if latest is None:
        return True, (":rotating_light: HubSpot inventory has NO sync stamp on any product. "
                      "The pipe17-hubspot-inventory job has not completed a run since the "
                      "stamp was added. Check its Cloud Scheduler entry and executions.")
    age_h = (now - latest).total_seconds() / 3600
    stamp = latest.strftime("%Y-%m-%d %H:%M UTC")
    if age_h > max_age_hours:
        return True, (f":rotating_light: HubSpot inventory is STALE: last synced {stamp} "
                      f"({age_h:.1f}h ago, limit {max_age_hours:g}h). Sales is quoting "
                      "from old numbers. Check the pipe17-hubspot-inventory scheduler "
                      "status and executions.")
    return False, f":white_check_mark: HubSpot inventory fresh (last synced {stamp})."


def latest_synced_at():
    """Newest `inventory_last_synced_at` across all products, or None."""
    body = {
        "filterGroups": [{"filters": [{"propertyName": HS_INVENTORY_SYNCED_AT_PROP,
                                       "operator": "HAS_PROPERTY"}]}],
        "sorts": [{"propertyName": HS_INVENTORY_SYNCED_AT_PROP, "direction": "DESCENDING"}],
        "properties": [HS_INVENTORY_SYNCED_AT_PROP],
        "limit": 1,
    }
    resp = requests.post(
        f"{HUBSPOT_API_BASE}/crm/v3/objects/{HS_PRODUCT_OBJECT}/search",
        headers={"Authorization": f"Bearer {HUBSPOT_TOKEN}",
                 "Content-Type": "application/json"},
        json=body, timeout=30)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    return parse_hs_datetime((results[0].get("properties") or {}).get(HS_INVENTORY_SYNCED_AT_PROP))


def check():
    """(is_stale, slack_line) against HubSpot right now."""
    return freshness_message(latest_synced_at(), dt.datetime.now(dt.timezone.utc))
