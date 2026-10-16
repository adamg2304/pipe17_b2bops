"""Offline tests for Inventory State + OOS exception note. No network, no creds.

Run: HUBSPOT_TOKEN=x PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3.12 selftest_inventory_state.py
"""
import inventory_state as iv
from inventory_state import Result, UNSET
from config import (
    INV_FULLY_IN_STOCK, INV_PARTIALLY_OOS, INV_AWAITING,
    O_INVENTORY_STATE, O_EXCEPTION_FLAG, O_STATUS,
)

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def order(status="readyForFulfillment", routing="pendingInventory", exceptions=("eRouteOutOfStock",),
          lines=None, ext="#X"):
    return {"extOrderId": ext, "status": status, "routingStatus": routing,
            "exceptions": list(exceptions), "lineItems": lines or []}


def line(uid, sku, qty, requires=True):
    return {"uniqueId": uid, "sku": sku, "quantity": qty, "requiresShipping": requires}


def sr(lines):
    return {"lineItems": lines}


# --- Fixture 1: #BE15487 single short SKU, 2 SRs routing 18+3 = 21 of 72 ---
o1 = order(ext="#BE15487", lines=[line("L1", "13-60-67-69", 72)])
s1 = [sr([line("L1", "13-60-67-69", 18)]), sr([line("L1", "13-60-67-69", 3)])]
r1 = iv.compute(o1, s1)
check("BE15487 -> Partially Out of Stock",
      r1 == Result(True, INV_PARTIALLY_OOS, "OOS: 13-60-67-69 x51 short (21/72 routed)"))

# --- Fixture 2: archived + excluded + sticky exception -> clear (active guard) ---
o2 = order(ext="#CEN65580554766", status="archived", routing="excluded",
           lines=[line("L1", "ABC", 10)])
r2 = iv.compute(o2, [sr([line("L1", "ABC", 4)])])
check("archived+excluded -> clear both (active guard beats sticky exception)",
      r2 == Result(True, state=None, note=None))

# --- Fixture 3: multi-SKU partial; omit the fully-routed line ---
o3 = order(ext="#CENmulti", lines=[line("A", "13-11-00-00", 20),
                                   line("B", "11-01-00-40", 20),
                                   line("C", "11-01-00-43", 20)])
s3 = [sr([line("A", "13-11-00-00", 20), line("B", "11-01-00-40", 7)])]
r3 = iv.compute(o3, s3)
check("multi-SKU -> Partially Out of Stock", r3.state == INV_PARTIALLY_OOS)
check("multi-SKU note lists only short lines, omits the fully-routed one",
      r3.note == "OOS: 11-01-00-40 x13 short (7/20 routed); 11-01-00-43 x20 short (0/20 routed)")

# --- Fixture 4: active OOS, zero SRs -> Awaiting Inventory ---
o4 = order(ext="#await", lines=[line("X", "ABC", 10)])
r4 = iv.compute(o4, [])
check("zero SRs + OOS -> Awaiting Inventory",
      r4 == Result(True, INV_AWAITING, "OOS: ABC x10 short (0/10 routed)"))

# --- Fully In Stock: not OOS, routed == ordered -> clear note ---
o5 = order(ext="#full", status="sentToFulfillment", routing="acknowledged", exceptions=(),
           lines=[line("L1", "SKU5", 5)])
r5 = iv.compute(o5, [sr([line("L1", "SKU5", 5)])])
check("fully routed, not OOS -> Fully In Stock (note cleared)",
      r5 == Result(True, INV_FULLY_IN_STOCK, None))

# --- UNSET: not OOS but still routing (routed < ordered) -> write nothing ---
o6 = order(ext="#routing", status="readyForFulfillment", routing="acknowledged", exceptions=(),
           lines=[line("L1", "SKU6", 5)])
r6 = iv.compute(o6, [sr([line("L1", "SKU6", 3)])])
check("not OOS, still routing -> UNSET (leave fields untouched)", r6 == UNSET)

# --- Service lines excluded from ordered (White Glove etc.) ---
o7 = order(ext="#svc", status="sentToFulfillment", routing="acknowledged", exceptions=(),
           lines=[line("L1", "SKU7", 4), line("L2", "White Glove Delivery", 1, requires=False)])
r7 = iv.compute(o7, [sr([line("L1", "SKU7", 4)])])
check("service line excluded -> Fully In Stock despite unrouted service line",
      r7 == Result(True, INV_FULLY_IN_STOCK, None))

# --- apply_to_fields writes only the two inventory fields, never Status ---
f = {}
iv.apply_to_fields(f, o1, s1, O_INVENTORY_STATE, O_EXCEPTION_FLAG)
check("apply_to_fields sets Inventory State", f.get(O_INVENTORY_STATE) == INV_PARTIALLY_OOS)
check("apply_to_fields sets Exception Flag", "OOS:" in (f.get(O_EXCEPTION_FLAG) or ""))
check("apply_to_fields never writes Status", O_STATUS not in f)
f_unset = {}
iv.apply_to_fields(f_unset, o6, [sr([line("L1", "SKU6", 3)])], O_INVENTORY_STATE, O_EXCEPTION_FLAG)
check("apply_to_fields writes nothing on UNSET", f_unset == {})
f_bad = {}
iv.apply_to_fields(f_bad, order(ext="#bad", lines=[]), [], O_INVENTORY_STATE, O_EXCEPTION_FLAG)
# empty order (ordered 0) + OOS + routed 0 -> Awaiting with empty note; state is a valid option
check("empty-line OOS order still a valid option (no junk option)",
      f_bad.get(O_INVENTORY_STATE) in (INV_AWAITING, None))

# --- Recovery engine: a flagged order whose SRs now cover ordered -> Fully In Stock ---
import main

class FakeAT:
    def __init__(self, recs):
        self.recs = recs
        self.updates = []
    def list_records(self, table, formula, field_ids=None, page_size=100):
        self.last_formula = formula
        return self.recs
    def update_record(self, table, record_id, fields):
        self.updates.append((record_id, fields))

class FakeP17:
    def __init__(self, order, srs):
        self._order, self._srs = order, srs
    def get_order_by_ext_id(self, num):
        return self._order
    def get_order_shipments(self, num):
        return self._srs

recovered = order(ext="#BE15487", status="sentToFulfillment", routing="acknowledged",
                  exceptions=(), lines=[line("L1", "13-60-67-69", 72)])
full_srs = [sr([line("L1", "13-60-67-69", 72)])]
fake_at = FakeAT([{"id": "rec1", "fields": {main.O_ORDER_NUMBER: "#BE15487"}}])
main.at = fake_at
main.p17 = FakeP17(recovered, full_srs)
main.DRY_RUN = False
wrote, left, failed_n = main.recovery_pass(False)
check("recovery writes the healed order", wrote == 1 and len(fake_at.updates) == 1)
rid, upd = fake_at.updates[0]
check("recovery clears to Fully In Stock, note cleared",
      upd.get(O_INVENTORY_STATE) == INV_FULLY_IN_STOCK and upd.get(O_EXCEPTION_FLAG) is None)
check("recovery formula scopes to flagged + not-done",
      "Awaiting Inventory" in fake_at.last_formula and "Completed" in fake_at.last_formula)

# idempotency: run again, same write (value unchanged)
fake_at.updates.clear()
main.recovery_pass(False)
rid2, upd2 = fake_at.updates[0]
check("recovery is idempotent (same result on re-run)", upd2 == upd)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
