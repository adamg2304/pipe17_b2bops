"""Offline tests: provisional SRs can't overwrite warehouse/line items, and a canceled SR
re-using a live SR's number can't take over its row. No network, no creds.

Run: DRY_RUN=false PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_shipment_gate.py
"""
import os
os.environ["DRY_RUN"] = "false"
os.environ["GENERATE_PACKING_LIST"] = "true"

import main
import shipment_gate as g
import pipe17_inventory
from config import (F_ORIGIN_WH, F_LINE_ITEMS, F_SHIPMENT_CREATION_DATE, F_STATUS,
                    SHIPMENT_NUMBER_FIELD, SHIP_PACKING_LIST_ATTACH)

passed = failed = 0


def check(name, cond):
    global passed, failed
    passed, failed = (passed + 1, failed) if cond else (passed, failed + 1)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


MTO, CBO = "fcf8381c1cffc669", "be5624a3c307a1ec"
LOCS = {MTO: "Mantoria - Mississauga (MTO)", CBO: "Canada Backorders B2B"}


def sr(sid, status, loc, qty, created, updated, num="#CEN66138513506"):
    return {"shipmentId": sid, "extShipmentId": num, "extOrderId": "#CEN66138513506",
            "status": status, "locationId": loc, "createdAt": created, "updatedAt": updated,
            "tags": ["Airtable"], "shippingAddress": {"firstName": "J", "city": "Montreal"},
            "lineItems": [{"sku": "11-11-00-59", "quantity": qty, "name": "Daily Chair"}]}


LIVE = sr("2f8e", "sentToFulfillment", MTO, 29, "2026-10-21T15:01:32Z", "2026-10-29T14:09:35Z")
DUP = sr("0134", "canceledRestock", CBO, 5, "2026-11-04T15:30:32Z", "2026-11-05T16:11:55Z")
REVIEW = sr("0134", "reviewRequired", CBO, 5, "2026-11-04T15:30:32Z", "2026-11-04T15:31:00Z")

# ---- pure gate ----------------------------------------------------------------
check("live beats canceled regardless of recency", g.prefer(DUP, LIVE) is LIVE)
check("committed beats provisional", g.prefer(REVIEW, LIVE) is LIVE)
check("pick_owner finds the live SR", g.pick_owner([LIVE, DUP], "#CEN66138513506") is LIVE)
f = {F_ORIGIN_WH: "x", F_LINE_ITEMS: "y", F_SHIPMENT_CREATION_DATE: "z", F_STATUS: "s"}
check("provisional SR on existing row: routing fields dropped",
      sorted(g.gate_routing_fields(dict(f), REVIEW, True)) ==
      sorted([F_ORIGIN_WH, F_LINE_ITEMS, F_SHIPMENT_CREATION_DATE]))
f2 = dict(f); g.gate_routing_fields(f2, REVIEW, False)
check("provisional SR creating a row keeps backorder WH", f2.get(F_ORIGIN_WH) == "x")
f3 = dict(f); g.gate_routing_fields(f3, LIVE, True)
check("committed SR on existing row keeps WH (re-route flows through)", f3.get(F_ORIGIN_WH) == "x")


# ---- sync_shipments end to end (fakes) ----------------------------------------
def run(window, siblings, existing):
    writes, updates = [], []
    main.p17.iter_shipping_requests = lambda since, tag=None: iter(window)
    main.p17.get_order_shipments = lambda ext: siblings
    pipe17_inventory.location_name_map = lambda key: LOCS
    main.at.find_record_id = lambda *a: "recORDER"
    main.at.find_record = lambda t, n, num: ({"id": "recSHIP", "fields": existing}
                                             if existing is not None else None)

    def upsert(table, recs, merge):
        writes.extend(recs)
        return 0, len(recs), [{"id": "recSHIP", "fields": {**(existing or {}), **r}} for r in recs]
    main.at.upsert = upsert
    main.at.update_record = lambda t, rid, flds: updates.append(flds)
    main.docs_render.render_pdf = lambda html: b"%PDF"
    main.docs_render.packing_list_html = lambda s: ""
    main.airtable_attach.attach_pdf = lambda *a: updates.append({"attached": a[2]})
    main.sync_shipments("2026-11-05T00:00:00Z", "Airtable")
    return writes, updates


ROW = {F_ORIGIN_WH: "Mantoria - Mississauga (MTO)", F_LINE_ITEMS: "29 x 11-11-00-59 - Daily Chair",
       F_STATUS: "Ready to Book", SHIP_PACKING_LIST_ATTACH: [{"id": "att1"}]}

# The #CEN66138513506 incident: only the canceled duplicate is in the window.
w, u = run([DUP], [LIVE, DUP], ROW)
check("canceled dup superseded: row written from live SR", w and w[0].get(F_LINE_ITEMS, "").startswith("29 x"))
check("canceled dup superseded: WH stays Mantoria", w and w[0].get(F_ORIGIN_WH) == LOCS[MTO])
check("canceled dup superseded: packing list untouched", not u)

# Both SRs in one window: the live one owns the row.
w, u = run([LIVE, DUP], [LIVE, DUP], ROW)
check("same-number SRs in one window -> one write", len(w) == 1)
check("same-number SRs in one window -> live SR wins", w[0].get(F_ORIGIN_WH) == LOCS[MTO])

# Provisional SR on an existing row (no live sibling): WH/line items held, PL cleared.
w, u = run([REVIEW], [REVIEW], ROW)
check("provisional SR doesn't overwrite Origin WH", F_ORIGIN_WH not in w[0])
check("provisional SR doesn't overwrite Line Items", F_LINE_ITEMS not in w[0])
check("provisional SR clears stale Shipment Details", {SHIP_PACKING_LIST_ATTACH: []} in u)

# Brand-new provisional SR: row created with the backorder WH, no Shipment Details.
w, u = run([REVIEW], [REVIEW], None)
check("new provisional SR shows backorder WH", w[0].get(F_ORIGIN_WH) == LOCS[CBO])
check("new provisional SR gets no Shipment Details", not any("attached" in x for x in u))

# Re-routed after send: SR now sent from Mantoria, row still shows the backorder WH.
sent = dict(REVIEW, status="sentToFulfillment", locationId=MTO)
row_bo = {F_ORIGIN_WH: LOCS[CBO], F_LINE_ITEMS: "5 x 11-11-00-59 - Daily Chair", F_STATUS: "In Review"}
w, u = run([sent], [sent], row_bo)
check("sent SR updates Origin WH to the real warehouse", w[0].get(F_ORIGIN_WH) == LOCS[MTO])
check("sent SR gets Shipment Details", any("attached" in x for x in u))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
