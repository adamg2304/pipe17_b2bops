"""Pipe17 -> Airtable sync (GCP Cloud Run Job).

Runs orders first (so parent Orders exist before shipments resolve their links), then
shipments. Both gated by the Airtable tag, incremental on the lookback window, and
idempotent. Honors DRY_RUN (map + log, no writes). A fail-safe refuses any live run
with the tag gate disabled, so an ungated write can't happen by accident.
"""
import datetime as dt
import logging
import sys

from config import (
    SYNC_LOOKBACK_MINUTES, SYNC_ORDERS, DRY_RUN, PIPE17_TAG_FILTER,
    ORDERS_TABLE, SHIPMENTS_TABLE,
    ORDER_NUMBER_FIELD, SHIPMENT_NUMBER_FIELD, SHIPMENT_ORDER_LINK_FIELD, FIELD_LABELS,
    ORDER_SYNC_STATUSES, ORDER_MERGE_FIELD, O_ORDER_NUMBER,
    GENERATE_ORDER_SLIP, O_ORDER_ATTACHMENTS,
)
import pipe17_client as p17
import airtable_client as at
from transform import shipment_to_airtable, order_number_of
from order_transform import order_to_airtable
import docs_render
import airtable_attach

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipe17-airtable")


def sync_orders(since, tag):
    orders, raw_by_num, skipped = [], {}, 0
    for o in p17.iter_orders(since, tag=tag or None, statuses=ORDER_SYNC_STATUSES):
        if tag and tag not in (o.get("tags") or []):
            skipped += 1
            continue
        cust = p17.get_customer(o.get("customerId"))
        ext_order_id = o.get("extOrderId")
        # Status is create-only: is_new is False if the Orders row already exists,
        # so we never re-stamp Status on an existing (Ops-managed) order.
        is_new = at.find_record_id(ORDERS_TABLE, ORDER_NUMBER_FIELD, ext_order_id) is None
        fields = order_to_airtable(o, cust, is_new=is_new)
        if fields.get(O_ORDER_NUMBER):
            orders.append(fields)
            raw_by_num[ext_order_id] = o
    log.info("Orders: mapped %d (%d skipped: missing '%s' tag)", len(orders), skipped, tag)
    if orders and DRY_RUN:
        log.info("DRY_RUN: not writing %d orders. Sample:", len(orders))
        for f in orders[:5]:
            log.info("  %s", f)
    elif orders:
        created, updated, records = at.upsert(ORDERS_TABLE, orders, [ORDER_MERGE_FIELD])
        log.info("Orders upserted: %d created, %d updated", created, updated)
        if GENERATE_ORDER_SLIP:
            _generate_order_slips(records, raw_by_num)


def _generate_order_slips(records, raw_by_num):
    """Render + attach an order slip to each order that doesn't already have one."""
    made = 0
    for rec in records:
        f = rec.get("fields", {})
        num = f.get(O_ORDER_NUMBER)
        order = raw_by_num.get(num)
        if not order or f.get(O_ORDER_ATTACHMENTS):   # unknown, or slip already present
            continue
        try:
            pdf = docs_render.render_pdf(docs_render.order_slip_html(order))
            fname = "OrderSlip_%s.pdf" % (str(num or "order").lstrip("#"))
            airtable_attach.attach_pdf(rec["id"], O_ORDER_ATTACHMENTS, fname, pdf)
            made += 1
        except Exception:
            log.exception("Order slip failed for %s", num)
    if made:
        log.info("Order slips generated + attached: %d", made)


def sync_shipments(since, tag):
    order_link_cache = {}

    def resolve_order_link(ext_order_id):
        if not ext_order_id:
            return None
        if ext_order_id not in order_link_cache:
            order_link_cache[ext_order_id] = at.find_record_id(
                ORDERS_TABLE, ORDER_NUMBER_FIELD, ext_order_id)
        return order_link_cache[ext_order_id]

    shipments, skipped = [], 0
    for sr in p17.iter_shipping_requests(since, tag=tag or None):
        if tag and tag not in (sr.get("tags") or []):
            skipped += 1
            continue
        fields = shipment_to_airtable(sr)
        if not fields.get(SHIPMENT_NUMBER_FIELD):
            continue
        rec_id = resolve_order_link(order_number_of(sr))
        if rec_id:
            fields[SHIPMENT_ORDER_LINK_FIELD] = [rec_id]
        shipments.append(fields)

    log.info("Mapped %d tagged shipping requests (%d skipped: missing '%s' tag)",
             len(shipments), skipped, tag)
    linked = sum(1 for f in shipments if SHIPMENT_ORDER_LINK_FIELD in f)
    if shipments and linked < len(shipments):
        log.warning("%d/%d shipments had no matching Order row (Order Link left empty)",
                    len(shipments) - linked, len(shipments))

    if shipments and DRY_RUN:
        log.info("DRY_RUN: not writing. Sample of mapped records:")
        for f in shipments[:10]:
            readable = {FIELD_LABELS.get(k, k): v for k, v in f.items()
                        if k not in (SHIPMENT_NUMBER_FIELD, SHIPMENT_ORDER_LINK_FIELD)}
            log.info("  %s -> Order Link %s | %s", f.get(SHIPMENT_NUMBER_FIELD),
                     f.get(SHIPMENT_ORDER_LINK_FIELD, "(none)"), readable)
    elif shipments:
        created, updated, _ = at.upsert(SHIPMENTS_TABLE, shipments, [SHIPMENT_NUMBER_FIELD])
        log.info("Shipments upserted: %d created, %d updated", created, updated)


def main():
    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(minutes=SYNC_LOOKBACK_MINUTES)).isoformat()
    tag = PIPE17_TAG_FILTER.strip()

    # Fail-safe: never write with the tag gate disabled.
    if not DRY_RUN and not tag:
        log.error("Refusing live run: tag gate disabled (PIPE17_TAG_FILTER empty). Aborting.")
        sys.exit(1)

    log.info("Sync window: updated since %s | tag gate: %s | DRY_RUN=%s",
             since, tag or "(disabled)", DRY_RUN)

    if SYNC_ORDERS:
        sync_orders(since, tag)      # orders first: parents exist before shipments link
    sync_shipments(since, tag)

    log.info("Sync complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Sync failed")
        sys.exit(1)
