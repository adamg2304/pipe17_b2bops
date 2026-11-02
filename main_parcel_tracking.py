"""Parcel shipment tracking sync (GCP Cloud Run Job).

Pipe17 fulfillments -> Airtable shipments. For shipments that shipped via PARCEL (carrier
in PARCEL_CARRIERS) and were picked up by Pipe17 directly, advance the Airtable shipment
Status to In-Transit (forward-only) and write the parcel tracking number(s). LTL/freight
shipments originate from Airtable work and carry no Pipe17 parcel fulfillment, so they are
never touched. Update-only: this job never creates an Airtable shipment row. Honors DRY_RUN.

This restores the Skubana behavior Ops lost: a parcel shipment flips to In-Transit with its
tracking as soon as the carrier label is created, instead of sitting at "Processing".

  python main_parcel_tracking.py                         # recent window (PARCEL_TRACKING_LOOKBACK_MINUTES)
  python main_parcel_tracking.py --dry-run               # force dry-run (never writes)
  python main_parcel_tracking.py --ext-order-id '#BE...' # just one order (live test)
  PARCEL_TRACKING_LOOKBACK_MINUTES=240 python main_parcel_tracking.py
"""
import datetime as dt
import logging
import sys

from config import (
    PARCEL_TRACKING_LOOKBACK_MINUTES, DRY_RUN, PIPE17_AIRTABLE_TAG, FIELD_LABELS,
    F_PARCEL_TRACKING,
)

LABELS = dict(FIELD_LABELS, **{F_PARCEL_TRACKING: "Parcel Tracking Number"})
import pipe17_client as p17
import airtable_client as at
import parcel_tracking
import notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("parcel-tracking")


def _arg(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def _readable(fields):
    return {LABELS.get(k, k): v for k, v in fields.items()}


def main():
    dry_run = DRY_RUN or ("--dry-run" in sys.argv)
    ext_order_id = _arg("--ext-order-id")
    tag = PIPE17_AIRTABLE_TAG or None

    if ext_order_id:
        fulfillments = p17.get_order_fulfillments(ext_order_id)
        log.info("Parcel tracking: single order %s | %d fulfillment(s) | DRY_RUN=%s",
                 ext_order_id, len(fulfillments), dry_run)
    else:
        since = (dt.datetime.now(dt.timezone.utc)
                 - dt.timedelta(minutes=PARCEL_TRACKING_LOOKBACK_MINUTES)).isoformat()
        fulfillments = list(p17.iter_fulfillments(since, tag=tag))
        log.info("Parcel tracking: fulfillments updated since %s | %d read | tag=%s | DRY_RUN=%s",
                 since, len(fulfillments), tag or "(all)", dry_run)

    counts, updates = parcel_tracking.reconcile(
        fulfillments, p17, at, dry_run=dry_run, require_tag=tag, log=log)

    for ext_ship, _rec_id, fields, g in updates:
        verb = "would update" if dry_run else "updated"
        log.info("  %s %s (%s) -> %s", verb, ext_ship, g.get("carrier"), _readable(fields))
    log.info("Parcel tracking complete: %s", counts)

    n = counts["updated"] + counts["would_update"]
    if n:
        verb = "would advance" if dry_run else "advanced"
        notify.slack(
            f":package: Parcel tracking {verb} {n} shipment(s) to In-Transit with tracking "
            f"(scanned {counts['fulfillments']} fulfillments, {counts['parcel_shipments']} parcel).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Parcel tracking sync failed")
        notify.slack(f":rotating_light: Parcel tracking sync FAILED: {e}")
        sys.exit(1)
