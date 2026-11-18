"""Pipe17 shipping requests -> Airtable Shipments, keyed on the shipping request ID.

Key: Pipe17 `shipmentId` ("Pipe17 Shipping Request ID" in Airtable) is the only upsert
key. `extShipmentId` ("Shipment Number") is written for display and never used to find a
row: Pipe17 reuses it when a backorder SR is canceled and re-routed (#BE63027606419 has
two SRs on each of "#BE63027606419" and "#BE63027606419.2").

Unit of work: one ORDER. Every run pulls the SRs updated in the window, and for each
order they belong to fetches ALL of that order's SRs (any channel, any tag) and:

  1. upserts one row per SR by shipmentId, writing only fields that changed (diffed
     against the stored row), with the forward-only Status guard, the provisional-SR
     routing gate, and Status "Backordered" for live SRs on a backorder location;
  2. runs the replacement sweep: each canceled SR is linked to the live SR(s) that now
     carry its line uniqueIds (one-to-many splits and cancel chains resolve to the
     newest live SRs), gets Status "Replaced" (or "Cancelled" when nothing replaced
     it), and is flagged Orphaned once a needed line has had no live SR for
     ORPHAN_GRACE_MINUTES. Ops fields are copied onto empty replacement fields.

Planning is pure (plan_sr_fields, plan_replacements, plan_sweep_fields), so it is tested
offline and a dry run prints exactly what a live run writes. Re-running on unchanged
data plans no writes.
"""
import datetime as dt
import logging
from collections import defaultdict

from config import (
    SHIPMENTS_TABLE, F_SHIP_REQUEST_ID, SHIP_REQUEST_ID_NAME, F_SHIPMENT_NUMBER,
    F_STATUS, F_ORDER_LINK, F_LINE_ITEMS, SHIP_STATUS_RANK, SR_CANCELED_STATUSES,
    SHIP_STATUS_BACKORDERED, SHIP_STATUS_REPLACED, SHIP_STATUS_CANCELLED,
    F_REPLACED_BY, F_REPLACES, F_ORPHANED, REPLACED_BY_NAME, ORPHANED_NAME,
    REPLACEMENT_COPY_FIELDS, BACKORDER_LOCATION_IDS, BACKORDER_INTEGRATION_IDS,
    BACKORDER_NAME_HINT, ORPHAN_GRACE_MINUTES, SWEEP_RECHECK_DAYS,
    RECOVERY_DONE_STATUSES, STATUS_FIELD_NAME, FIELD_LABELS,
)
import shipment_gate
from transform import shipment_to_airtable

log = logging.getLogger("pipe17-airtable")

ORDER_CANCELED_STATUSES = {"canceled", "canceledRestock"}


# --------------------------------------------------------------------------- SR helpers

def sid_of(sr):
    return (sr or {}).get("shipmentId")


def is_canceled(sr):
    return (sr or {}).get("status") in SR_CANCELED_STATUSES


def is_backorder(sr, loc_names=None):
    """True when the SR sits on a virtual backorder location: a listed location ID, a
    location whose name contains the backorder hint, or the no-fulfillment integration."""
    sr = sr or {}
    loc = sr.get("locationId")
    if loc in BACKORDER_LOCATION_IDS:
        return True
    if sr.get("fulfillmentIntegrationId") in BACKORDER_INTEGRATION_IDS:
        return True
    name = (loc_names or {}).get(loc) or ""
    return bool(BACKORDER_NAME_HINT) and BACKORDER_NAME_HINT in name.lower()


def line_ids(sr):
    return {li.get("uniqueId") for li in (sr or {}).get("lineItems") or [] if li.get("uniqueId")}


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def status_regresses(current, new):
    """True when writing `new` would move Status BACKWARD past `current`. A blank current
    is writable; a current status the sync doesn't manage (set by Ops) is preserved;
    otherwise the rank may hold or rise, never fall. Once a row reaches Processing
    (sent to fulfillment) nothing earlier can be written over it."""
    if not current:
        return False
    cr = SHIP_STATUS_RANK.get(current)
    if cr is None:
        return True
    nr = SHIP_STATUS_RANK.get(new)
    if nr is None:
        return False
    return nr < cr


# --------------------------------------------------------------------------- diffing

def _norm(v):
    """Comparable form of an Airtable cell value: blank/False/[] -> None, select objects
    -> name, link/attachment lists -> sorted tuple of ids/names."""
    if v is None or v is False or v == "" or v == []:
        return None
    if isinstance(v, dict):
        return v.get("name") or v.get("id")
    if isinstance(v, (list, tuple)):
        return tuple(sorted(str(_norm(x)) for x in v))
    if isinstance(v, str):
        return v.strip()
    return v


def diff_fields(want, current):
    """The subset of `want` that differs from the stored `current` fields."""
    current = current or {}
    return {k: v for k, v in want.items() if _norm(v) != _norm(current.get(k))}


# --------------------------------------------------------------------------- phase 1

def plan_sr_fields(sr, prior_fields, loc_names=None, order_row_id=None, canceled_status=None):
    """Fields to write for one SR's row. `prior_fields` is the stored row (None = no row
    yet, so every mapped field is written). Returns {} when the row is already current.

    Status for a canceled SR belongs to the sweep. On an existing row phase 1 leaves it
    alone; on a NEW row it is written at creation as `canceled_status` (the sweep's
    Replaced/Cancelled), so record-created automations (the LateShipment webhook) never
    see a canceled shipment with a blank status."""
    want = shipment_to_airtable(sr, location_map=loc_names or {})
    if order_row_id:
        want[F_ORDER_LINK] = [order_row_id]
    if is_canceled(sr):
        want.pop(F_STATUS, None)
        if prior_fields is None and canceled_status:
            want[F_STATUS] = canceled_status
    elif is_backorder(sr, loc_names) and not shipment_gate.is_committed(sr):
        want[F_STATUS] = SHIP_STATUS_BACKORDERED
    exists = prior_fields is not None
    shipment_gate.gate_routing_fields(want, sr, exists)
    if (F_STATUS in want and exists
            and status_regresses(_norm(prior_fields.get(F_STATUS)), want[F_STATUS])):
        del want[F_STATUS]
    if not exists:
        return want
    changed = diff_fields(want, prior_fields)
    if changed:
        changed[F_SHIP_REQUEST_ID] = sid_of(sr)
    return changed


# --------------------------------------------------------------------------- sweep

def _lines_still_needed(order):
    """Line uniqueIds the order still expects to ship, or None when unknown (no order
    data: every missing line counts). A canceled order needs nothing. Bundle children
    on SRs ("line-1_2") count as needed when their parent order line ("line-1") is."""
    if not order:
        return None
    if order.get("status") in ORDER_CANCELED_STATUSES:
        return set()
    lines = order.get("lineItems")
    if not lines:
        return None
    return {li.get("uniqueId") for li in lines
            if li.get("uniqueId") and (li.get("quantity") or 0) > 0}


def _needed(uid, needed):
    if needed is None:
        return True
    return uid in needed or uid.split("_")[0] in needed


def plan_replacements(order_srs, now, order=None, grace_minutes=ORPHAN_GRACE_MINUTES):
    """{canceled sid: resolution} for every canceled SR on the order.

    A canceled SR's line uniqueIds are looked up on the order's LIVE SRs created at or
    after it. Every live SR carrying one of its lines is a replacement, so a split across
    new SRs gives several, and a chain (A canceled -> B canceled -> C live) resolves A and
    B both to C, the newest live SR. Lines with no live SR are orphan lines; the SR is
    `orphaned` once the grace since its cancel has passed and the order still needs a
    missing line, otherwise `pending`.

    resolution = {"replaced_by": [sids, newest first], "orphan_lines": [uids],
                  "orphaned": bool, "pending": bool}
    """
    srs = [s for s in order_srs or [] if sid_of(s)]
    live = [s for s in srs if not is_canceled(s)]
    created = {sid_of(s): s.get("createdAt") or "" for s in srs}
    needed = _lines_still_needed(order)
    grace = dt.timedelta(minutes=grace_minutes)
    out = {}
    for c in sorted((s for s in srs if is_canceled(s)), key=lambda s: s.get("createdAt") or ""):
        c_created = c.get("createdAt") or ""
        replaced_by, orphan_lines = set(), []
        for uid in sorted(line_ids(c)):
            hits = [s for s in live
                    if uid in line_ids(s) and (s.get("createdAt") or "") >= c_created]
            if hits:
                replaced_by.update(sid_of(s) for s in hits)
            elif _needed(uid, needed):
                orphan_lines.append(uid)
        canceled_at = _parse_ts(c.get("canceledAt") or c.get("updatedAt"))
        grace_over = canceled_at is None or (now - canceled_at) >= grace
        out[sid_of(c)] = {
            "replaced_by": sorted(replaced_by, key=lambda x: (created.get(x, ""), x), reverse=True),
            "orphan_lines": orphan_lines,
            "orphaned": bool(orphan_lines) and grace_over,
            "pending": bool(orphan_lines) and not grace_over,
        }
    return out


def plan_sweep_fields(order_srs, rows_by_sid, plan, done_statuses=RECOVERY_DONE_STATUSES):
    """{sid: fields} the sweep writes. `rows_by_sid` is {sid: {"id", "fields"}} for every
    row the order has (after phase 1). Only changed values are returned, so a re-run on
    a swept order plans nothing.

    Canceled rows: Status Replaced (or Cancelled), Replaced By, Orphaned. Status is
    written over any status Ops set except a done status (Completed): Pipe17 has
    canceled the SR, so the row must say so. Live rows: Replaces = the canceled rows that
    point at them, and the copy fields from those canceled rows where the live value is
    empty (newest canceled row first)."""
    by_sid = {sid_of(s): s for s in order_srs or [] if sid_of(s)}
    done = {d for d in done_statuses or ()}
    updates = defaultdict(dict)
    replaces = defaultdict(set)
    sources = defaultdict(list)      # live sid -> canceled rows feeding it, newest first

    for csid in sorted(plan, key=lambda s: (by_sid.get(s, {}).get("createdAt") or ""), reverse=True):
        res = plan[csid]
        row = rows_by_sid.get(csid)
        if not row:
            continue
        cur = row.get("fields") or {}
        want = {}
        if _norm(cur.get(F_STATUS)) not in done:
            want[F_STATUS] = SHIP_STATUS_REPLACED if res["replaced_by"] else SHIP_STATUS_CANCELLED
        rep_rows = [rows_by_sid[s] for s in res["replaced_by"] if s in rows_by_sid]
        if F_REPLACED_BY:
            want[F_REPLACED_BY] = sorted(r["id"] for r in rep_rows)
        if F_ORPHANED:
            want[F_ORPHANED] = bool(res["orphaned"])
        updates[csid].update(diff_fields(want, cur))
        for r_sid in res["replaced_by"]:
            if r_sid in rows_by_sid:
                replaces[r_sid].add(row["id"])
                sources[r_sid].append(cur)

    for sid, row in rows_by_sid.items():
        if sid in plan:
            continue
        cur = row.get("fields") or {}
        if F_REPLACES:
            updates[sid].update(diff_fields({F_REPLACES: sorted(replaces.get(sid, ()))}, cur))
        for fid in REPLACEMENT_COPY_FIELDS:
            if _norm(cur.get(fid)) is not None:
                continue
            for src in sources.get(sid, ()):
                if _norm(src.get(fid)) is not None:
                    updates[sid][fid] = src.get(fid)
                    break
    return {sid: f for sid, f in updates.items() if f}


# --------------------------------------------------------------------------- I/O

def _readable(fields):
    return {FIELD_LABELS.get(k, k): v for k, v in fields.items()}


def sync_order(ext_order_id, *, p17, at, loc_names=None, order_row_id=None, dry_run=True,
               now=None, srs=None, notify=None, on_rows=None):
    """Sync every SR on one order and sweep its replacements. Returns a report dict:
    {"order", "rows": {sid: {"id","fields"}}, "actions": [...], "plan": {...},
     "orphans_new": [...], "dupes": {...}}.

    `on_rows(rows_by_sid, srs_by_sid, prior_line_items)` runs after a live write (the
    caller generates packing lists there). In dry_run nothing is written; new rows get a
    placeholder id "new:<sid>" so the sweep plan can still be shown."""
    now = now or dt.datetime.now(dt.timezone.utc)
    srs = [s for s in (srs if srs is not None else p17.get_order_shipments(ext_order_id)) or []
           if sid_of(s)]
    srs_by_sid = {sid_of(s): s for s in srs}
    report = {"order": ext_order_id, "rows": {}, "actions": [], "plan": {},
              "orphans_new": [], "dupes": {}}
    if not srs:
        return report

    prior, dupes = at.find_records_by_values(
        SHIPMENTS_TABLE, SHIP_REQUEST_ID_NAME, F_SHIP_REQUEST_ID, srs_by_sid)
    if dupes:
        report["dupes"] = dupes
        log.warning("Order %s: duplicate Airtable rows for shipping request(s) %s; using the "
                    "oldest row for each", ext_order_id, dupes)
    rows = {sid: {"id": r["id"], "fields": dict(r.get("fields") or {})} for sid, r in prior.items()}
    prior_line_items = {sid: (r.get("fields") or {}).get(F_LINE_ITEMS)
                        for sid, r in prior.items()}

    # Replacement plan first: it only needs the SRs (+ order data when a canceled SR has
    # a line with no live SR), and a new canceled row is created with its final status.
    plan = plan_replacements(srs, now)
    if any(r["orphan_lines"] for r in plan.values()):
        try:
            order = p17.get_order_by_ext_id(ext_order_id)
        except Exception:
            log.exception("Order lookup failed for %s; treating missing lines as needed",
                          ext_order_id)
            order = None
        plan = plan_replacements(srs, now, order=order)
    report["plan"] = plan

    # Phase 1: one row per SR, changed fields only.
    writes = []
    for sid, sr in srs_by_sid.items():
        cstatus = None
        if sid in plan:
            cstatus = SHIP_STATUS_REPLACED if plan[sid]["replaced_by"] else SHIP_STATUS_CANCELLED
        fields = plan_sr_fields(sr, rows[sid]["fields"] if sid in rows else None,
                                loc_names, order_row_id, canceled_status=cstatus)
        action = "create" if sid not in rows else ("update" if fields else "none")
        report["actions"].append({"phase": "sr", "sid": sid, "number": sr.get("extShipmentId"),
                                  "status": sr.get("status"), "action": action,
                                  "fields": _readable(fields)})
        if fields:
            writes.append(fields)
    if writes and not dry_run:
        _, _, records = at.upsert(SHIPMENTS_TABLE, writes, [F_SHIP_REQUEST_ID])
        for rec in records:
            sid = (rec.get("fields") or {}).get(F_SHIP_REQUEST_ID)
            if sid:
                rows[sid] = {"id": rec["id"], "fields": dict(rec.get("fields") or {})}
    elif writes:
        for fields in writes:
            sid = fields[F_SHIP_REQUEST_ID]
            row = rows.setdefault(sid, {"id": f"new:{sid}", "fields": {}})
            row["fields"].update(fields)

    # Phase 2: replacement sweep (links, Orphaned, status changes on existing rows, copied
    # ops fields). Needs record ids, so it runs after phase 1 has created any new rows.
    sweep = plan_sweep_fields(srs, rows, plan)
    for sid, fields in sweep.items():
        report["actions"].append({"phase": "sweep", "sid": sid,
                                  "number": srs_by_sid[sid].get("extShipmentId"),
                                  "status": srs_by_sid[sid].get("status"), "action": "update",
                                  "fields": _readable(fields)})
    for csid, res in plan.items():
        if res["orphaned"]:
            was = _norm((rows.get(csid) or {}).get("fields", {}).get(F_ORPHANED)) if F_ORPHANED else None
            # Warn (and alert) once, when the flag is first set; an orphan that stays
            # orphaned is logged at info on later runs so the log isn't flooded.
            (log.info if was else log.warning)(
                "Order %s: canceled SR %s (%s) is ORPHANED: line(s) %s have no live "
                "shipping request", ext_order_id, csid,
                srs_by_sid[csid].get("extShipmentId"), ", ".join(res["orphan_lines"]))
            if F_ORPHANED and not was:
                report["orphans_new"].append(csid)
    if sweep and not dry_run:
        real = [(rows[sid]["id"], f) for sid, f in sweep.items()
                if sid in rows and not rows[sid]["id"].startswith("new:")]
        if real:
            for rec in at.update_records(SHIPMENTS_TABLE, real):
                sid = (rec.get("fields") or {}).get(F_SHIP_REQUEST_ID)
                if sid in rows:
                    rows[sid]["fields"].update(rec.get("fields") or {})
    elif sweep:
        for sid, f in sweep.items():
            rows[sid]["fields"].update(f)

    if report["orphans_new"] and notify and not dry_run:
        lines = []
        for csid in report["orphans_new"]:
            sr = srs_by_sid[csid]
            lines.append(f"{sr.get('extShipmentId')} (`{csid}`): {', '.join(plan[csid]['orphan_lines'])}")
        notify(f":warning: {ext_order_id}: canceled shipping request(s) with no live replacement "
               f"after {ORPHAN_GRACE_MINUTES} min:\n" + "\n".join(lines))

    report["rows"] = rows
    if on_rows and not dry_run:
        on_rows(rows, srs_by_sid, prior_line_items)
    return report


def recheck_formula(days=SWEEP_RECHECK_DAYS):
    """Airtable formula for canceled rows the sweep hasn't settled: Status Cancelled, a
    shipping request ID, ingested in the last `days`, and (when those fields exist) not
    Orphaned and no Replaced By."""
    parts = ['{%s}="%s"' % (STATUS_FIELD_NAME, SHIP_STATUS_CANCELLED),
             'NOT({%s}="")' % SHIP_REQUEST_ID_NAME,
             "IS_AFTER(CREATED_TIME(), DATEADD(NOW(), -%d, 'days'))" % days]
    if F_ORPHANED:
        parts.append("NOT({%s})" % ORPHANED_NAME)
    if F_REPLACED_BY:
        parts.append('{%s}=""' % REPLACED_BY_NAME)
    return "AND(%s)" % ",".join(parts)


def recheck_orders(p17, at, days=SWEEP_RECHECK_DAYS):
    """extOrderIds that still have an unsettled canceled row, found from Airtable (not the
    updatedSince window) and resolved through Pipe17 by shipping request ID."""
    orders = set()
    for rec in at.list_records(SHIPMENTS_TABLE, recheck_formula(days),
                               field_ids=[F_SHIP_REQUEST_ID, F_SHIPMENT_NUMBER]):
        sid = (rec.get("fields") or {}).get(F_SHIP_REQUEST_ID)
        try:
            sr = p17.get_shipping_request(sid)
        except Exception:
            log.exception("Recheck: Pipe17 lookup failed for shipping request %s", sid)
            continue
        if sr and sr.get("extOrderId"):
            orders.add(sr["extOrderId"])
    return orders
