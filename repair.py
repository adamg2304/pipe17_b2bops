"""Diagnose why a Pipe17 order / shipping request is missing or wrong in Airtable, and
heal it through the normal sync mapping.

This is the engine behind the ops "Sync Repair Requests" form. It is framework-free:
repair_service.py wraps it for the webhook, and it is unit-tested offline by monkey-
patching the module-level clients (p17/at/...), the same style as the other selftests.

A repair is EXPLICIT and human-initiated, so for a "damaged" row it re-writes the
current Pipe17 mapping as the source of truth. It never force-creates a row that the
scheduled sync intentionally skips (wrong tag / non-synced status) unless `force` is set
— instead it reports exactly why the record was skipped, which is the triage signal.
"""
import logging
import re

import pipe17_client as p17
import airtable_client as at
import inventory_state
from transform import shipment_to_airtable
from order_transform import order_to_airtable
from config import (
    PIPE17_TAG_FILTER, ORDER_SYNC_STATUSES,
    ORDERS_TABLE, SHIPMENTS_TABLE, ORDER_NUMBER_FIELD, ORDER_MERGE_FIELD,
    O_ORDER_NUMBER, O_INVENTORY_STATE, O_EXCEPTION_FLAG,
    SHIPMENT_NUMBER_FIELD, SHIPMENT_ORDER_LINK_FIELD,
    REPAIR_STATUS_HEALED, REPAIR_STATUS_NOT_HEALED, REPAIR_STATUS_REVIEW,
    REPAIR_TYPE_ORDER, REPAIR_TYPE_SR,
)

log = logging.getLogger("pipe17-airtable")

_SR_SUFFIX = re.compile(r"\.\d+$")


def normalize_id(raw):
    """Trim and give the id its leading '#': ops may paste 'BE123' or '#BE123'."""
    s = (raw or "").strip()
    if s and not s.startswith("#"):
        s = "#" + s
    return s


def _is_sr_number(num):
    return bool(_SR_SUFFIX.search(num or ""))


def _order_ext_of(num):
    """The order ext id behind an SR number: '#BE123.2' -> '#BE123' (a bare order id is
    returned unchanged)."""
    return _SR_SUFFIX.sub("", num or "")


def _loc_map():
    """Live {locationId: warehouse name} so a healed shipment's Origin WH is the real
    name. Falls back to the static map on any read failure (never blocks the heal)."""
    try:
        from config import PIPE17_INVENTORY_READ_KEY, LOCATION_MAP
        import pipe17_inventory
        return pipe17_inventory.location_name_map(PIPE17_INVENTORY_READ_KEY) or LOCATION_MAP
    except Exception:
        from config import LOCATION_MAP
        return LOCATION_MAP


def _result(**kw):
    r = {"ok": True, "kind": None, "found": False, "eligible": False, "healed": False,
         "status": None, "diagnosis": "", "outcome": ""}
    r.update(kw)
    return r


def _repair_order(num, force, dry_run):
    order = p17.get_order_by_ext_id(num)
    if not order:
        return _result(kind="order", found=False, status=REPAIR_STATUS_REVIEW,
                       diagnosis=(f"No Pipe17 order found for {num}. Check the number, and that "
                                  "it's the US/CA channel this sync reads."),
                       outcome="Nothing to heal — order not found in Pipe17.")
    tags = order.get("tags") or []
    status = order.get("status")
    tag_ok = PIPE17_TAG_FILTER in tags
    status_ok = status in ORDER_SYNC_STATUSES
    existing = at.find_record_id(ORDERS_TABLE, ORDER_NUMBER_FIELD, num)
    eligible = tag_ok and status_ok

    reasons = []
    if not tag_ok:
        reasons.append(f"it isn't tagged '{PIPE17_TAG_FILTER}' in Pipe17, so the scheduled sync "
                       "skips it (fix the tag / the routing automation)")
    if not status_ok:
        reasons.append(f"its status is '{status}', and the order sync only pulls "
                       f"{', '.join(ORDER_SYNC_STATUSES)} (reviewRequired = still in Ops review; "
                       "draft = not released; fulfilled/canceled = past the window)")
    if eligible and existing:
        diagnosis = "Order is eligible and already in Airtable — re-synced from Pipe17 to refresh its data."
    elif eligible and not existing:
        diagnosis = ("Order is eligible but was missing from Airtable (a missed/failed run or it fell "
                     "outside the lookback window) — created it from Pipe17.")
    else:
        diagnosis = "Order was skipped because " + "; and ".join(reasons) + "."

    if not (eligible or force):
        return _result(kind="order", found=True, eligible=False, healed=False,
                       status=REPAIR_STATUS_REVIEW, diagnosis=diagnosis,
                       outcome=("Not healed: the order isn't in a state the sync pulls. Fix the cause "
                                "above, or resubmit with Force to push it in anyway."))

    if dry_run:
        return _result(kind="order", found=True, eligible=eligible, healed=False,
                       status=REPAIR_STATUS_HEALED, diagnosis=diagnosis,
                       outcome="[dry-run] would upsert the Orders row from Pipe17.")

    cust = p17.get_customer(order.get("customerId"))
    fields = order_to_airtable(order, cust, is_new=existing is None)
    try:
        srs = p17.get_order_shipments(num)
        inventory_state.apply_to_fields(fields, order, srs, O_INVENTORY_STATE, O_EXCEPTION_FLAG)
    except Exception:
        log.exception("Inventory State compute failed during repair of %s", num)
    if not fields.get(O_ORDER_NUMBER):
        return _result(kind="order", found=True, eligible=eligible, healed=False,
                       status=REPAIR_STATUS_REVIEW, diagnosis=diagnosis,
                       outcome="Could not map the order (no order number produced).")
    created, updated, _ = at.upsert(ORDERS_TABLE, [fields], [ORDER_MERGE_FIELD])
    verb = "Created" if created else "Updated"
    forced = "" if eligible else " (forced — order is not normally synced)"
    return _result(kind="order", found=True, eligible=eligible, healed=True,
                   status=REPAIR_STATUS_HEALED, diagnosis=diagnosis,
                   outcome=f"{verb} the Orders row from Pipe17{forced}.")


def _repair_sr(num, force, dry_run):
    ext_order = _order_ext_of(num)
    shipments = p17.get_order_shipments(ext_order)
    if not shipments:
        return _result(kind="sr", found=False, status=REPAIR_STATUS_REVIEW,
                       diagnosis=(f"No Pipe17 shipping requests found for order {ext_order}. The order "
                                  "may not have routed yet, or the number is wrong."),
                       outcome="Nothing to heal — no shipping requests in Pipe17.")
    if _is_sr_number(num):
        targets = [s for s in shipments if s.get("extShipmentId") == num]
        if not targets:
            have = ", ".join(sorted(s.get("extShipmentId") or "?" for s in shipments))
            return _result(kind="sr", found=False, status=REPAIR_STATUS_REVIEW,
                           diagnosis=f"Order {ext_order} has no shipping request {num}. It has: {have}.",
                           outcome="Nothing to heal — that specific SR doesn't exist.")
    else:
        targets = shipments

    untagged = [s.get("extShipmentId") for s in targets if PIPE17_TAG_FILTER not in (s.get("tags") or [])]
    order_row = at.find_record_id(ORDERS_TABLE, ORDER_NUMBER_FIELD, ext_order)

    reasons = []
    if untagged:
        reasons.append(f"{len(untagged)} of {len(targets)} SR(s) aren't tagged '{PIPE17_TAG_FILTER}', "
                       "so the sync skips them")
    if not order_row:
        reasons.append(f"the order {ext_order} has no Airtable row yet, so Order Link can't be set "
                       "(repair the order first)")

    if untagged and not force:
        return _result(kind="sr", found=True, eligible=False, healed=False,
                       status=REPAIR_STATUS_REVIEW,
                       diagnosis="Shipping request(s) skipped because " + "; and ".join(reasons) + ".",
                       outcome=("Not healed: untagged SR(s) aren't synced. Add the "
                                f"'{PIPE17_TAG_FILTER}' tag in Pipe17, or resubmit with Force."))

    if dry_run:
        return _result(kind="sr", found=True, eligible=not untagged, healed=False,
                       status=REPAIR_STATUS_HEALED,
                       diagnosis=f"Would upsert {len(targets)} shipping request(s) for {ext_order}.",
                       outcome="[dry-run] no write.")

    loc_map = _loc_map()
    n = 0
    for s in targets:
        f = shipment_to_airtable(s, location_map=loc_map)
        if not f.get(SHIPMENT_NUMBER_FIELD):
            continue
        if order_row:
            f[SHIPMENT_ORDER_LINK_FIELD] = [order_row]
        at.upsert(SHIPMENTS_TABLE, [f], [SHIPMENT_NUMBER_FIELD])
        n += 1
    note = ""
    if not order_row:
        note = " (Order Link left empty — repair the order so shipments can link to it)"
    diagnosis = (f"Re-synced {n} shipping request(s) for {ext_order} from Pipe17."
                 + (" " + "; and ".join(reasons) + "." if reasons else ""))
    return _result(kind="sr", found=True, eligible=not untagged, healed=n > 0,
                   status=REPAIR_STATUS_HEALED if n else REPAIR_STATUS_REVIEW,
                   diagnosis=diagnosis,
                   outcome=f"Upserted {n} shipping request row(s) from Pipe17{note}.")


def diagnose_and_heal(pipe17_id, kind="auto", force=False, dry_run=False):
    """Diagnose + heal one order or shipping request. `kind` is 'order', 'sr', or 'auto'
    (a '.N' suffix => SR; otherwise treated as an order). Returns a result dict with a
    human `diagnosis`, an `outcome`, and a `status` for the request row. Never raises on a
    data problem — it reports it; only a genuine client/exception bubbles up to the caller."""
    num = normalize_id(pipe17_id)
    if not num:
        return _result(ok=False, status=REPAIR_STATUS_NOT_HEALED,
                       diagnosis="No Pipe17 ID provided.", outcome="Nothing to do.")
    k = (kind or "auto").strip().lower()
    if k in ("order", REPAIR_TYPE_ORDER.lower()):
        return _repair_order(num, force, dry_run)
    if k in ("sr", "shipping request", REPAIR_TYPE_SR.lower()):
        return _repair_sr(num, force, dry_run)
    # auto
    if _is_sr_number(num):
        return _repair_sr(num, force, dry_run)
    return _repair_order(num, force, dry_run)
