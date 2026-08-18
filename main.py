"""Pipe17 -> Airtable sync (GCP Cloud Run Job).

Each run: pull Pipe17 shipping requests updated within the lookback window, map to
the Airtable Shipments shape, resolve each one's parent Order (by Order Number) to
set the Order Link, and idempotently upsert (merge on Shipment Number). Optionally
upserts parent Orders too (SYNC_ORDERS). Runs to completion; schedule via Cloud
Scheduler at a cadence <= SYNC_LOOKBACK_MINUTES.
"""
import datetime as dt
import logging
import sys

from config import (
    SYNC_LOOKBACK_MINUTES, SYNC_ORDERS, DRY_RUN,
    ORDERS_TABLE, SHIPMENTS_TABLE,
    ORDER_NUMBER_FIELD, SHIPMENT_NUMBER_FIELD, SHIPMENT_ORDER_LINK_FIELD,
)
import pipe17_client as p17
import airtable_client as at
from transform import shipment_to_airtable, order_number_of

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipe17-airtable")


def main():
    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(minutes=SYNC_LOOKBACK_MINUTES)).isoformat()
    log.info("Sync window: updated since %s", since)

    order_link_cache = {}   # extOrderId -> Airtable Orders recordId (or None)

    def resolve_order_link(ext_order_id):
        if not ext_order_id:
            return None
        if ext_order_id not in order_link_cache:
            order_link_cache[ext_order_id] = at.find_record_id(
                ORDERS_TABLE, ORDER_NUMBER_FIELD, ext_order_id)
        return order_link_cache[ext_order_id]

    shipments = []
    for sr in p17.iter_shipping_requests(since):
        fields = shipment_to_airtable(sr)
        if not fields.get(SHIPMENT_NUMBER_FIELD):
            continue  # never upsert without a merge key
        rec_id = resolve_order_link(order_number_of(sr))
        if rec_id:
            fields[SHIPMENT_ORDER_LINK_FIELD] = [rec_id]
        shipments.append(fields)

    log.info("Mapped %d shipping requests", len(shipments))
    linked = sum(1 for f in shipments if SHIPMENT_ORDER_LINK_FIELD in f)
    if shipments and linked < len(shipments):
        log.warning("%d/%d shipments had no matching Order row (Order Link left empty)",
                    len(shipments) - linked, len(shipments))

    if shipments and DRY_RUN:
        import json
        log.warning("DRY_RUN=true — NOT writing to Airtable. Mapped payload below:")
        for f in shipments:
            log.info("  %s", json.dumps(f, ensure_ascii=False))
        log.info("DRY_RUN: %d shipments would be upserted (%d linked to an Order).",
                 len(shipments), linked)
    elif shipments:
        created, updated = at.upsert(SHIPMENTS_TABLE, shipments, [SHIPMENT_NUMBER_FIELD])
        log.info("Shipments upserted: %d created, %d updated", created, updated)

    if SYNC_ORDERS:
        log.info("SYNC_ORDERS enabled — parent-order upsert not yet mapped; skipping.")
        # TODO: map Pipe17 order -> Orders fields and upsert merge on Order Number.

    log.info("Sync complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Sync failed")
        sys.exit(1)
