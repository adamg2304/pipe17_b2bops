"""Offline tests for the provisional-SR routing gate (shipment_gate). No network, no creds.
Keying, dedupe and the replacement sweep are covered in selftest_shipment_sync.py.

Run: PIPE17_API_KEY=x AIRTABLE_API_KEY=x HUBSPOT_TOKEN=x python3 selftest_shipment_gate.py
"""
import shipment_gate as g
from config import F_ORIGIN_WH, F_LINE_ITEMS, F_SHIPMENT_CREATION_DATE, F_STATUS

passed = failed = 0


def check(name, cond):
    global passed, failed
    passed, failed = (passed + 1, failed) if cond else (passed, failed + 1)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


SENT = {"status": "sentToFulfillment"}
REVIEW = {"status": "reviewRequired"}
CANCELED = {"status": "canceledRestock"}
FIELDS = {F_ORIGIN_WH: "x", F_LINE_ITEMS: "y", F_SHIPMENT_CREATION_DATE: "z", F_STATUS: "s"}

check("sentToFulfillment is committed", g.is_committed(SENT))
check("fulfilled is committed", g.is_committed({"status": "fulfilled"}))
check("reviewRequired is not committed", not g.is_committed(REVIEW))
check("canceledRestock is canceled", g.is_canceled(CANCELED))
check("canceled is canceled", g.is_canceled({"status": "canceled"}))
check("sentToFulfillment is not canceled", not g.is_canceled(SENT))

f = dict(FIELDS)
check("provisional SR on existing row: routing fields dropped",
      sorted(g.gate_routing_fields(f, REVIEW, True))
      == sorted([F_ORIGIN_WH, F_LINE_ITEMS, F_SHIPMENT_CREATION_DATE]))
check("...Status left for the status rules", f == {F_STATUS: "s"})

f = dict(FIELDS)
g.gate_routing_fields(f, REVIEW, False)
check("provisional SR creating a row keeps its (backorder) warehouse", f.get(F_ORIGIN_WH) == "x")

f = dict(FIELDS)
g.gate_routing_fields(f, SENT, True)
check("committed SR on existing row keeps WH (re-route flows through)", f == FIELDS)

check("no prefer/pick_owner: nothing picks a row by extShipmentId",
      not hasattr(g, "prefer") and not hasattr(g, "pick_owner"))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
