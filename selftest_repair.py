"""Offline tests for the repair engine (repair.diagnose_and_heal). No network, no creds.

Run: HUBSPOT_TOKEN=x PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_repair.py
"""
import repair
from config import (O_ORDER_NUMBER, SHIPMENT_NUMBER_FIELD, PIPE17_TAG_FILTER,
                    REPAIR_STATUS_HEALED, REPAIR_STATUS_REVIEW)

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1; print(f"  PASS  {name}")
    else:
        failed += 1; print(f"  FAIL  {name}")


# ---- stubs --------------------------------------------------------------
upserts = []          # (table, fields)
repair._loc_map = lambda: {}
repair.p17.get_customer = lambda cid: {"name": "Acme"}
repair.p17.get_order_shipments = lambda ext: _ORDER_SRS.get(ext, [])
repair.inventory_state.apply_to_fields = lambda *a, **k: None
repair.order_to_airtable = lambda o, c, is_new=True: {O_ORDER_NUMBER: o.get("extOrderId")}
repair.shipment_to_airtable = lambda s, location_map=None: {SHIPMENT_NUMBER_FIELD: s.get("extShipmentId")}


def _upsert(table, records, merge):
    upserts.append((table, records))
    # pretend created unless the id looks "existing" (handled via find_record_id stub)
    return (1, 0, [])


repair.at.upsert = _upsert

_ORDERS = {}          # ext -> order dict
_ORDER_SRS = {}       # ext -> [shipments]
_EXISTING = set()     # ext ids that already have an Airtable row

repair.p17.get_order_by_ext_id = lambda ext: _ORDERS.get(ext)
repair.at.find_record_id = lambda table, field, value: ("rec1" if value in _EXISTING else None)

TAG = PIPE17_TAG_FILTER


def reset():
    upserts.clear(); _ORDERS.clear(); _ORDER_SRS.clear(); _EXISTING.clear()


# ---- order cases --------------------------------------------------------
reset()
r = repair.diagnose_and_heal("#BE999")  # not in Pipe17
check("order not found -> review, not healed", r["status"] == REPAIR_STATUS_REVIEW and not r["healed"])

reset()
_ORDERS["#BE1"] = {"extOrderId": "#BE1", "status": "readyForFulfillment", "tags": ["Other"]}
r = repair.diagnose_and_heal("#BE1")  # eligible status but missing tag
check("order missing tag -> not healed, tag in diagnosis",
      not r["healed"] and TAG in r["diagnosis"])

reset()
_ORDERS["#BE2"] = {"extOrderId": "#BE2", "status": "reviewRequired", "tags": [TAG]}
r = repair.diagnose_and_heal("#BE2")  # tagged but non-synced status
check("order non-synced status -> not healed, status in diagnosis",
      not r["healed"] and "reviewRequired" in r["diagnosis"])

reset()
_ORDERS["#BE3"] = {"extOrderId": "#BE3", "status": "readyForFulfillment", "tags": [TAG]}
r = repair.diagnose_and_heal("#BE3")  # eligible, missing from Airtable
check("eligible+missing -> healed (created), upsert called",
      r["healed"] and r["status"] == REPAIR_STATUS_HEALED and len(upserts) == 1)

reset()
_ORDERS["#BE4"] = {"extOrderId": "#BE4", "status": "sentToFulfillment", "tags": [TAG]}
_EXISTING.add("#BE4")
r = repair.diagnose_and_heal("#BE4")  # eligible, already present (damaged -> refresh)
check("eligible+existing -> healed (refresh)", r["healed"] and len(upserts) == 1)

reset()
_ORDERS["#BE5"] = {"extOrderId": "#BE5", "status": "reviewRequired", "tags": ["Other"]}
r = repair.diagnose_and_heal("#BE5", force=True)  # force past non-eligibility
check("force heals a non-eligible order", r["healed"] and len(upserts) == 1)

reset()
_ORDERS["#BE6"] = {"extOrderId": "#BE6", "status": "readyForFulfillment", "tags": [TAG]}
r = repair.diagnose_and_heal("#BE6", dry_run=True)
check("dry-run writes nothing", not r["healed"] and len(upserts) == 0)

# ---- SR cases -----------------------------------------------------------
reset()
_ORDER_SRS["#BE7"] = [{"extShipmentId": "#BE7.1", "tags": [TAG]},
                      {"extShipmentId": "#BE7.2", "tags": [TAG]}]
r = repair.diagnose_and_heal("#BE7.2")  # one specific SR, auto-detected as SR by suffix
check("SR by suffix -> heals just that SR", r["kind"] == "sr" and r["healed"] and len(upserts) == 1
      and upserts[0][1][0][SHIPMENT_NUMBER_FIELD] == "#BE7.2")

reset()
_ORDER_SRS["#BE8"] = [{"extShipmentId": "#BE8.1", "tags": [TAG]},
                      {"extShipmentId": "#BE8.2", "tags": [TAG]}]
r = repair.diagnose_and_heal("#BE8", kind="sr")  # whole-order SR heal
check("SR kind, no suffix -> heals all SRs", r["healed"] and len(upserts) == 2)

reset()
_ORDER_SRS["#BE9"] = [{"extShipmentId": "#BE9.1", "tags": ["Other"]}]
r = repair.diagnose_and_heal("#BE9.1")  # untagged SR
check("untagged SR -> not healed without force", not r["healed"] and len(upserts) == 0)

reset()
_ORDER_SRS["#BE9"] = [{"extShipmentId": "#BE9.1", "tags": ["Other"]}]
r = repair.diagnose_and_heal("#BE9.1", force=True)
check("untagged SR + force -> healed", r["healed"] and len(upserts) == 1)

# ---- id normalization ---------------------------------------------------
check("normalize_id adds '#'", repair.normalize_id("BE123") == "#BE123")
check("normalize_id keeps '#'", repair.normalize_id("  #BE123 ") == "#BE123")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
