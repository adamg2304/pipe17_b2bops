"""Pipe17 -> Airtable sync (GCP Cloud Run Job).

Runs orders first (so parent Orders exist before shipments resolve their links), then
shipments. Both gated by the Airtable tag, incremental on the lookback window, and
idempotent. Honors DRY_RUN (map + log, no writes). A fail-safe refuses any live run
with the tag gate disabled, so an ungated write can't happen by accident.
"""
import datetime as dt
import logging
import os
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
    PACKING_LIST_STATUSES, SHIPMENT_NUMBER_NAME, F_LINE_ITEMS,
    O_INVENTORY_STATE, O_EXCEPTION_FLAG,
    INVENTORY_STATE_NAME, STATUS_FIELD_NAME, PIPE17_ORDER_ID_NAME,
    RECOVERY_DONE_STATUSES, INV_AWAITING, INV_PARTIALLY_OOS,
)
import pipe17_client as p17
import airtable_client as at
import hubspot_client as hs
from transform import shipment_to_airtable, order_number_of
from order_transform import order_to_airtable
import inventory_state
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
            # Inventory State + Exception Flag, computed from the order + its SRs. Never
            # writes Status. Per-order failure is logged and never blocks the run.
            try:
                srs = p17.get_order_shipments(ext_order_id)
                inventory_state.apply_to_fields(fields, o, srs, O_INVENTORY_STATE, O_EXCEPTION_FLAG)
            except Exception:
                log.exception("Inventory State compute failed for order %s", ext_order_id)
            orders.append(fields)
            raw_by_num[ext_order_id] = o
            if is_new:   # first time we see this order Ops-approved -> write back to HubSpot
                new_orders.append(o)
    log.info("Orders: mapped %d (%d skipped: missing '%s' tag)", len(orders), skipped, tag)
    # Order numbers that had no Airtable row yet — i.e. the drift the reconcile sweep
    # heals. Populated whether or not we actually write (DRY_RUN reports would-create).
    created_numbers = [o.get("extOrderId") for o in new_orders]
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
    return created_numbers


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


def _generate_packing_lists(records, raw_by_num, prior_line_items):
    """Attach a packing list to each shipment that is SENT to fulfillment (BLK-1/3):
    a shipment must be handed to the warehouse before its pick list is real, so a
    shipment still in ready-for-fulfillment gets a row + status but no list yet.

    (Re)generation rule (BLK-4): generate when there is no list yet, or when the
    shipment's Line Items changed under the same Shipment Number (a re-split/re-route
    after the first sweep) — in that case the stale list is cleared then replaced,
    so Airtable never drifts from what Pipe17 will actually ship. Unchanged shipments
    that already have a list are skipped (idempotent). Errors are caught per-record
    and never block the sync.
    """
    made = 0
    for rec in records:
        f = rec.get("fields", {})
        num = f.get(SHIPMENT_NUMBER_FIELD)
        shipment = raw_by_num.get(num)
        if not shipment:
            continue
        if shipment.get("status") not in PACKING_LIST_STATUSES:
            continue   # not warehouse-bound yet -> no pick list
        has_pl = bool(f.get(SHIP_PACKING_LIST_ATTACH))
        changed = num in prior_line_items and prior_line_items[num] != f.get(F_LINE_ITEMS)
        if has_pl and not changed:
            continue   # already generated, contents unchanged
        try:
            if has_pl and changed:   # clear the stale list before replacing it
                at.update_record(SHIPMENTS_TABLE, rec["id"], {SHIP_PACKING_LIST_ATTACH: []})
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

    # Resolve warehouse names live from Pipe17 /locations (needs the org-wide read key),
    # so Origin WH is the real name, not a hand-maintained table. Fall back to the static
    # LOCATION_MAP if the lookup fails, so a locations hiccup never blocks the sync.
    from config import LOCATION_MAP, PIPE17_INVENTORY_READ_KEY
    loc_map = LOCATION_MAP
    try:
        import pipe17_inventory
        live = pipe17_inventory.location_name_map(PIPE17_INVENTORY_READ_KEY)
        if live:
            loc_map = live
            log.info("Resolved %d Pipe17 warehouse names for Origin WH", len(live))
    except Exception:
        log.exception("Could not read Pipe17 /locations; using static LOCATION_MAP for Origin WH")

    shipments, skipped = [], 0
    unmatched_orders = set()
    for sr in p17.iter_shipping_requests(since, tag=tag or None):
        if tag and tag not in (sr.get("tags") or []):
            skipped += 1
            continue
        fields = shipment_to_airtable(sr, location_map=loc_map)
        if not fields.get(SHIPMENT_NUMBER_FIELD):
            continue
        ext_order_id = order_number_of(sr)
        rec_id = resolve_order_link(ext_order_id)
        if rec_id:
            fields[SHIPMENT_ORDER_LINK_FIELD] = [rec_id]
        else:
            unmatched_orders.add(ext_order_id)
        shipments.append(fields)
        raw_by_num[fields[SHIPMENT_NUMBER_FIELD]] = sr

    log.info("Mapped %d tagged shipping requests (%d skipped: missing '%s' tag)",
             len(shipments), skipped, tag)
    linked = sum(1 for f in shipments if SHIPMENT_ORDER_LINK_FIELD in f)
    if shipments and linked < len(shipments):
        # Name the offenders: a tagged shipment whose order has no Orders row. Usually an
        # order that is no longer in a synced status (ORDER_SYNC_STATUSES), so the order
        # sync won't create it — the shipment links to nothing. Logging the ids makes the
        # gap actionable instead of a bare count.
        log.warning("%d/%d shipments had no matching Order row (Order Link left empty): %s",
                    len(shipments) - linked, len(shipments),
                    ", ".join(sorted(o for o in unmatched_orders if o)))

    if shipments and DRY_RUN:
        log.info("DRY_RUN: not writing. Sample of mapped records:")
        for f in shipments[:10]:
            readable = {FIELD_LABELS.get(k, k): v for k, v in f.items()
                        if k not in (SHIPMENT_NUMBER_FIELD, SHIPMENT_ORDER_LINK_FIELD)}
            log.info("  %s -> Order Link %s | %s", f.get(SHIPMENT_NUMBER_FIELD),
                     f.get(SHIPMENT_ORDER_LINK_FIELD, "(none)"), readable)
    elif shipments:
        # BLK-4: capture each row's stored Line Items BEFORE the upsert overwrites them,
        # so the packing-list step can tell when a shipment re-split/re-routed and its
        # contents changed under the same Shipment Number.
        prior_line_items = {}
        if GENERATE_PACKING_LIST:
            for num in raw_by_num:
                existing = at.find_record(SHIPMENTS_TABLE, SHIPMENT_NUMBER_NAME, num)
                if existing:
                    prior_line_items[num] = (existing.get("fields") or {}).get(F_LINE_ITEMS)
        created, updated, records = at.upsert(SHIPMENTS_TABLE, shipments, [SHIPMENT_NUMBER_FIELD])
        log.info("Shipments upserted: %d created, %d updated", created, updated)
        if GENERATE_PACKING_LIST:
            _generate_packing_lists(records, raw_by_num, prior_line_items)


# --- Inventory State recovery + backfill (read FROM Airtable, recompute from Pipe17) ---

def _not_done_clauses():
    return ['NOT({%s}="%s")' % (STATUS_FIELD_NAME, s) for s in RECOVERY_DONE_STATUSES]


def _reconcile_inventory_state(formula, label, dry_run):
    """Read Orders matching `formula` FROM Airtable, re-fetch each order + its SRs from
    Pipe17, recompute Inventory State + Exception Flag, and write the result. Idempotent;
    per-record errors are logged and never block. The UNSET case (still routing) writes
    nothing. Never writes Status."""
    records = at.list_records(ORDERS_TABLE, formula, field_ids=[O_ORDER_NUMBER])
    log.info("%s: %d candidate order rows", label, len(records))
    wrote = left = failed = 0
    for rec in records:
        num = (rec.get("fields") or {}).get(O_ORDER_NUMBER)
        if not num:
            continue
        try:
            order = p17.get_order_by_ext_id(num)
            if not order:
                left += 1
                continue
            srs = p17.get_order_shipments(num)
            fields = {}
            inventory_state.apply_to_fields(fields, order, srs, O_INVENTORY_STATE, O_EXCEPTION_FLAG)
            if not fields:   # UNSET -> leave the row's fields as they are
                left += 1
                continue
            if dry_run:
                log.info("[DRY_RUN] %s %s -> %s", label, num, fields)
            else:
                at.update_record(ORDERS_TABLE, rec["id"], fields)
            wrote += 1
        except Exception:
            failed += 1
            log.exception("%s: failed for order %s", label, num)
    log.info("%s done: wrote=%d left-as-is=%d failed=%d", label, wrote, left, failed)
    return wrote, left, failed


def recovery_pass(dry_run):
    """Each run: re-check already-flagged, not-done orders straight from Airtable (not the
    updatedSince window), so stock that returned gets cleared to Fully In Stock."""
    flagged = "OR(%s)" % ",".join('{%s}="%s"' % (INVENTORY_STATE_NAME, v)
                                  for v in (INV_AWAITING, INV_PARTIALLY_OOS))
    formula = "AND(%s)" % ",".join([flagged] + _not_done_clauses())
    return _reconcile_inventory_state(formula, "Inventory recovery", dry_run)


def backfill_inventory_state(dry_run):
    """One-time, idempotent: set initial Inventory State / Exception Flag on existing
    non-done orders that carry a Pipe17 Order ID (the sync-managed set)."""
    formula = "AND(%s)" % ",".join(['NOT({%s}="")' % PIPE17_ORDER_ID_NAME] + _not_done_clauses())
    return _reconcile_inventory_state(formula, "Inventory backfill", dry_run)


def _lookback_minutes():
    """Normal runs use SYNC_LOOKBACK_MINUTES. A backfill can widen the window with
    `--lookback-min N` (or the LOOKBACK_MIN env), e.g. to re-pull orders that advanced
    past the sync window before a run caught them."""
    for i, a in enumerate(sys.argv):
        if a == "--lookback-min" and i + 1 < len(sys.argv):
            return int(sys.argv[i + 1])
    env = os.environ.get("LOOKBACK_MIN")
    return int(env) if env else SYNC_LOOKBACK_MINUTES


def main():
    # One-time inventory-state backfill mode (idempotent); does nothing else.
    if "--backfill-inventory-state" in sys.argv:
        log.info("Mode: inventory-state backfill | DRY_RUN=%s", DRY_RUN)
        backfill_inventory_state(DRY_RUN)
        return

    minutes = _lookback_minutes()
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)).isoformat()
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

    # Recovery pass: re-check already-flagged, not-done orders straight from Airtable
    # (independent of the updatedSince window), so returned stock clears to Fully In Stock.
    try:
        recovery_pass(DRY_RUN)
    except Exception:
        log.exception("Inventory recovery pass failed (continuing)")

    log.info("Sync complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Sync failed")
        import notify
        notify.slack(f":rotating_light: Pipe17 -> Airtable sync FAILED: {e}")
        sys.exit(1)
