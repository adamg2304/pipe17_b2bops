"""Parcel shipment tracking: Pipe17 fulfillments -> Airtable shipment Status + tracking.

Pure, testable core for the parcel-tracking job (main_parcel_tracking.py). The carrier and
tracking number(s) for a shipment live on its FULFILLMENTS, not the shipping request, and an
SR sits at Pipe17 status "fulfilled" (unmapped) once shipped — so Track A's SR sync leaves
parcel shipments stuck at "Processing" with no tracking. This module reads fulfillments and:

  * keeps only PARCEL shipments (carrier in PARCEL_CARRIERS) that Pipe17 fulfilled directly —
    LTL/freight shipments originate from Airtable work and carry no Pipe17 parcel fulfillment,
    so they are never touched;
  * advances the Airtable shipment Status to In-Transit, FORWARD-ONLY (reusing the same guard
    Track A uses, so a status Ops already moved ahead is never dragged back);
  * writes the de-duplicated parcel tracking number(s).

It never creates an Airtable row (update-only).
"""
from config import (
    PARCEL_CARRIERS, PARCEL_SKIP_FULFILLMENT_STATUSES,
    F_STATUS, F_PARCEL_TRACKING, SHIP_STATUS_IN_TRANSIT,
    SHIPMENTS_TABLE, SHIP_REQUEST_ID_NAME, F_SHIPMENT_NUMBER, SHIP_STATUS_RANK,
)


def _status_regresses(current, new):
    """Forward-only Status guard — behaviourally identical to main._status_regresses (the
    Track A sync's guard). Kept local so this job imports only config + the two clients, not
    the full Track A module tree (docs/gcs/hubspot). selftest_parcel_tracking asserts parity
    with main's version, so the two can't drift. True means writing `new` would move the
    row BACKWARD past the status Airtable already shows (a blank or unmanaged current is
    never a regression / is preserved)."""
    if not current:
        return False
    cr = SHIP_STATUS_RANK.get(current)
    if cr is None:
        return True            # unmanaged current value — preserve it
    nr = SHIP_STATUS_RANK.get(new)
    if nr is None:
        return False           # unmanaged new value — allow the write
    return nr < cr


def is_parcel_carrier(carrier):
    """True when the fulfillment's shippingCarrier is a parcel carrier we auto-advance.
    Case-insensitive substring match, so "UPS Ground" / "USPS Priority" match on the base
    name. Freight carriers (not in PARCEL_CARRIERS) return False and are left to Airtable."""
    c = (carrier or "").strip().lower()
    return bool(c) and any(frag in c for frag in PARCEL_CARRIERS)


def group_parcel_shipments(fulfillments, require_tag=None):
    """Collapse fulfillments to one entry per Pipe17 shipmentId, keeping only parcel
    shipments that have shipped (a parcel carrier + at least one tracking number, not a
    cancelled fulfillment). Returns {shipmentId: {"extOrderId", "carrier", "tracking":[...]}}
    with tracking numbers de-duplicated in first-seen order across the shipment's fulfillments.

    require_tag (e.g. "Airtable"), when set, drops any fulfillment not carrying that tag — so
    only Airtable-synced shipments are considered."""
    groups = {}
    for f in fulfillments or []:
        if require_tag and require_tag not in (f.get("tags") or []):
            continue
        if (f.get("status") or "").strip() in PARCEL_SKIP_FULFILLMENT_STATUSES:
            continue
        if not is_parcel_carrier(f.get("shippingCarrier")):
            continue
        tns = [str(t).strip() for t in (f.get("trackingNumber") or []) if str(t).strip()]
        if not tns:
            continue
        sid = f.get("shipmentId")
        if not sid:
            continue
        g = groups.setdefault(
            sid, {"extOrderId": f.get("extOrderId"), "carrier": f.get("shippingCarrier"),
                  "tracking": []})
        if not g.get("extOrderId"):
            g["extOrderId"] = f.get("extOrderId")
        for t in tns:
            if t not in g["tracking"]:
                g["tracking"].append(t)
    return groups


def plan_shipment_update(current_status, current_tracking, tracking_numbers):
    """Airtable fields to write for one parcel shipment (empty dict = nothing changed).

    Status -> In-Transit, but only when that is forward (never clobbers a blank, a human
    status, or a more-advanced one like Arrived). Parcel Tracking Number -> the joined
    numbers, written only when it differs from what's stored (idempotent)."""
    fields = {}
    tracking_str = ", ".join(tracking_numbers or [])
    if (current_status != SHIP_STATUS_IN_TRANSIT
            and not _status_regresses(current_status, SHIP_STATUS_IN_TRANSIT)):
        fields[F_STATUS] = SHIP_STATUS_IN_TRANSIT
    if tracking_str and (current_tracking or "").strip() != tracking_str:
        fields[F_PARCEL_TRACKING] = tracking_str
    return fields


def reconcile(fulfillments, p17, at, *, dry_run, require_tag=None, log=None):
    """Drive fulfillments -> Airtable updates. Returns (counts, updates).

    A fulfillment carries the Pipe17 shipmentId, which is the Shipments row key ("Pipe17
    Shipping Request ID"), so each parcel shipment's row is found directly by it; the
    reused Shipment Number is never used to match. `p17` is unused (kept for the
    caller's signature); `at` needs find_record + update_record. updates is a list of
    (shipment number, record_id, fields, group) for logging/Slack."""
    groups = group_parcel_shipments(fulfillments, require_tag=require_tag)
    counts = {"fulfillments": len(fulfillments or []), "parcel_shipments": len(groups),
              "updated": 0, "would_update": 0, "unchanged": 0,
              "no_sr": 0, "not_in_airtable": 0}
    if not groups:
        return counts, []

    updates = []
    for sid, g in groups.items():
        rec = at.find_record(SHIPMENTS_TABLE, SHIP_REQUEST_ID_NAME, sid)
        if not rec:
            counts["not_in_airtable"] += 1          # update-only: never created here
            continue
        f = rec.get("fields") or {}
        fields = plan_shipment_update(f.get(F_STATUS), f.get(F_PARCEL_TRACKING), g["tracking"])
        if not fields:
            counts["unchanged"] += 1
            continue
        updates.append((f.get(F_SHIPMENT_NUMBER) or sid, rec["id"], fields, g))
        if dry_run:
            counts["would_update"] += 1
        else:
            at.update_record(SHIPMENTS_TABLE, rec["id"], fields)
            counts["updated"] += 1
    return counts, updates
