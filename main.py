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
    GENERATE_PACKING_LIST, SHIP_PACKING_LIST_ATTACH,
    WRITEBACK_DEAL_ON_APPROVAL, HUBSPOT_TOKEN, HS_ORDERED_STAGE_ID,
    HS_ORDER_DETAILS_PROP, HS_DEAL_ID_CUSTOM_FIELD, PIPE17_ORDER_PREFIX_MAP,
)
import pipe17_client as p17
import airtable_client as at
import hubspot_client as hs
from transform import shipment_to_airtable, order_number_of
from order_transform import order_to_airtable
import docs_render
import airtable_attach

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipe17-airtable")


def sync_orders(since, tag):
    orders, raw_by_num, skipped = [], {}, 0
    new_orders = []
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
            if is_new:   # first time we see this order Ops-approved -> write back to HubSpot
                new_orders.append(o)
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
        if WRITEBACK_DEAL_ON_APPROVAL:
            _writeback_deals(new_orders)


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


def _hubspot_deal_id(order):
    """The HubSpot deal id behind a Pipe17 order: the hubspot_deal_id custom field
    Track B stamps at creation, falling back to stripping the currency prefix off
    extOrderId (production order numbers are just <prefix><dealId>)."""
    for cf in order.get("customFields") or []:
        if cf.get("name") == HS_DEAL_ID_CUSTOM_FIELD and cf.get("value"):
            return str(cf["value"])
    ext = order.get("extOrderId") or ""
    for prefix in PIPE17_ORDER_PREFIX_MAP.values():
        if prefix and ext.startswith(prefix):
            rest = ext[len(prefix):]
            return rest or None
    return None


def _writeback_deals(new_orders):
    """On Ops approval (order now readyForFulfillment, first sync), move the HubSpot
    deal to the Ordered-with-Warehouse stage and record the Pipe17 order number.
    Per-deal errors are caught so a write-back failure never blocks the sync."""
    if not new_orders:
        return
    if not HUBSPOT_TOKEN:
        log.warning("Deal write-back skipped: HUBSPOT_TOKEN not set on this job.")
        return
    done = 0
    for o in new_orders:
        deal_id = _hubspot_deal_id(o)
        if not deal_id:
            log.warning("Deal write-back skipped for %s: no hubspot_deal_id.", o.get("extOrderId"))
            continue
        try:
            hs.update_deal(deal_id, {"dealstage": HS_ORDERED_STAGE_ID,
                                     HS_ORDER_DETAILS_PROP: o.get("extOrderId")})
            done += 1
        except Exception:
            log.exception("Deal write-back failed for deal %s (order %s)",
                          deal_id, o.get("extOrderId"))
    if done:
        log.info("HubSpot deals moved to Ordered-with-Warehouse: %d", done)


def _generate_packing_lists(records, raw_by_num):
    """Render + attach a packing list to each shipment that doesn't already have one."""
    made = 0
    for rec in records:
        f = rec.get("fields", {})
        num = f.get(SHIPMENT_NUMBER_FIELD)
        shipment = raw_by_num.get(num)
        if not shipment or f.get(SHIP_PACKING_LIST_ATTACH):   # unknown, or list already present
            continue
        try:
            pdf = docs_render.render_pdf(docs_render.packing_list_html(shipment))
            fname = "PackingList_%s.pdf" % (str(num or "shipment").lstrip("#"))
            airtable_attach.attach_pdf(rec["id"], SHIP_PACKING_LIST_ATTACH, fname, pdf)
            made += 1
        except Exception:
            log.exception("Packing list failed for %s", num)
    if made:
        log.info("Packing lists generated + attached: %d", made)


def sync_shipments(since, tag):
    order_link_cache = {}
    raw_by_num = {}

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
        raw_by_num[fields[SHIPMENT_NUMBER_FIELD]] = sr

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
        created, updated, records = at.upsert(SHIPMENTS_TABLE, shipments, [SHIPMENT_NUMBER_FIELD])
        log.info("Shipments upserted: %d created, %d updated", created, updated)
        if GENERATE_PACKING_LIST:
            _generate_packing_lists(records, raw_by_num)


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
