"""Reconcile sweep (GCP Cloud Run Job) — the backstop for the frequent sync.

The frequent Pipe17 -> Airtable sync only looks at a narrow recent window. If a run is
late, fails, or coincides with a burst of status changes, orders that became eligible in
that gap are never retried and silently never reach Airtable.

This sweep closes that gap. It re-checks every Airtable-tagged order in a sync status
over a wide window (RECONCILE_LOOKBACK_DAYS, default 14) and upserts any that are missing.
The upsert is idempotent, so orders already in Airtable are untouched. It reuses the
production sync_orders / sync_shipments, so a healed order also gets its order slip and
HubSpot write-back exactly like a normal sync.

Every run reports to Slack: an alert naming the orders it healed when there was drift, or
a one-line all-clear heartbeat when there wasn't. A dead or failing sweep therefore shows
up as a missing heartbeat or a failure alert, instead of orders quietly going missing.

Run:
  python main_reconcile.py                    # uses RECONCILE_LOOKBACK_DAYS
  RECONCILE_LOOKBACK_DAYS=30 python main_reconcile.py
"""
import datetime as dt
import logging
import sys

from config import RECONCILE_LOOKBACK_DAYS, PIPE17_TAG_FILTER, DRY_RUN
import main as sync
import notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipe17-airtable")


def _format_healed(healed, days, dry_run):
    verb = "would heal" if dry_run else "healed"
    shown = ", ".join(healed[:25])
    if len(healed) > 25:
        shown += f" (+{len(healed) - 25} more)"
    return (f":wrench: Reconcile {verb} {len(healed)} order(s) that were missing from "
            f"Airtable over the last {days}d: {shown}")


def main():
    tag = PIPE17_TAG_FILTER.strip()
    # Same fail-safe as the frequent sync: never write with the tag gate off.
    if not DRY_RUN and not tag:
        log.error("Refusing live reconcile: tag gate disabled (PIPE17_TAG_FILTER empty).")
        sys.exit(1)

    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(days=RECONCILE_LOOKBACK_DAYS)).isoformat()
    log.info("Reconcile sweep: eligible orders updated since %s | tag=%s | DRY_RUN=%s",
             since, tag or "(disabled)", DRY_RUN)

    healed = sync.sync_orders(since, tag)   # orders are the backstop's main job
    sync.sync_shipments(since, tag)         # heal shipment drift too (logged, not alerted)

    if healed:
        msg = _format_healed(healed, RECONCILE_LOOKBACK_DAYS, DRY_RUN)
        log.warning(msg)
    else:
        msg = (f":white_check_mark: Reconcile OK — no drift, every eligible order present "
               f"in Airtable over the last {RECONCILE_LOOKBACK_DAYS}d.")
        log.info(msg)
    notify.slack(msg)
    log.info("Reconcile complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Reconcile failed")
        notify.slack(f":rotating_light: Pipe17 -> Airtable RECONCILE FAILED: {e}")
        sys.exit(1)
