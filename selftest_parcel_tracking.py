"""Offline tests for the parcel shipment tracking sync. No network, no creds.

Run: HUBSPOT_TOKEN=x PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_parcel_tracking.py
"""
import parcel_tracking as pt
from config import (
    F_STATUS, F_PARCEL_TRACKING, SHIP_STATUS_IN_TRANSIT, SHIPMENT_NUMBER_NAME,
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


# ---- is_parcel_carrier ----------------------------------------------------
check("UPS is parcel", pt.is_parcel_carrier("UPS") is True)
check("UPS Ground is parcel (substring)", pt.is_parcel_carrier("UPS Ground") is True)
check("FedEx is parcel", pt.is_parcel_carrier("FedEx") is True)
check("USPS Priority is parcel", pt.is_parcel_carrier("USPS Priority") is True)
check("Purolator is parcel", pt.is_parcel_carrier("Purolator") is True)
check("blank carrier not parcel", pt.is_parcel_carrier("") is False)
check("None carrier not parcel", pt.is_parcel_carrier(None) is False)
check("Estes (LTL freight) not parcel", pt.is_parcel_carrier("Estes Express") is False)
check("RXO (LTL freight) not parcel", pt.is_parcel_carrier("RXO") is False)

# ---- forward-only guard parity with Track A's main._status_regresses -------
# The local copy must never drift from the canonical guard the SR sync uses.
import main  # noqa: E402  (imported here only to assert parity)
_PARITY = [
    (None, "In-Transit"), ("", "In-Transit"), ("Processing", "In-Transit"),
    ("In-Transit", "In-Transit"), ("Ready to Book", "Processing"),
    ("Arrived", "In-Transit"), ("Cancelled", "In-Transit"),
    ("On Hold - Customer", "In-Transit"), ("Out of Stock", "In-Transit"),
]
check("forward-only guard matches main._status_regresses",
      all(pt._status_regresses(c, n) == main._status_regresses(c, n) for c, n in _PARITY))

# ---- group_parcel_shipments ----------------------------------------------
# The real #BE66021836346 shape: 4 UPS fulfillments, one shipmentId, overlapping tracking.
FFS = [
    {"shipmentId": "SID1", "extOrderId": "#BE1", "shippingCarrier": "UPS", "status": "submitted",
     "tags": ["Airtable", "LTL"], "trackingNumber": ["1Z-A", "1Z-SHARED"]},
    {"shipmentId": "SID1", "extOrderId": "#BE1", "shippingCarrier": "UPS", "status": "submitted",
     "tags": ["Airtable", "LTL"], "trackingNumber": ["1Z-B", "1Z-SHARED"]},
    {"shipmentId": "SID1", "extOrderId": "#BE1", "shippingCarrier": "UPS", "status": "submitted",
     "tags": ["Airtable", "LTL"], "trackingNumber": ["1Z-SHARED"]},
    # Freight carrier -> excluded entirely.
    {"shipmentId": "SID2", "extOrderId": "#BE2", "shippingCarrier": "Estes Express", "status": "submitted",
     "tags": ["Airtable"], "trackingNumber": ["FREIGHT-1"]},
    # Parcel but no tracking yet -> not shipped, excluded.
    {"shipmentId": "SID3", "extOrderId": "#BE3", "shippingCarrier": "UPS", "status": "submitted",
     "tags": ["Airtable"], "trackingNumber": []},
    # Cancelled fulfillment -> excluded.
    {"shipmentId": "SID4", "extOrderId": "#BE4", "shippingCarrier": "FedEx", "status": "cancelled",
     "tags": ["Airtable"], "trackingNumber": ["CANCELLED-1"]},
    # Not Airtable-tagged (a DTC fulfillment) -> excluded when require_tag set.
    {"shipmentId": "SID5", "extOrderId": "#DTC", "shippingCarrier": "UPS", "status": "submitted",
     "tags": ["Shopify"], "trackingNumber": ["DTC-1"]},
]
groups = pt.group_parcel_shipments(FFS, require_tag="Airtable")
check("only SID1 survives (parcel + tracking + Airtable)", list(groups.keys()) == ["SID1"])
check("SID1 tracking de-duped in order",
      groups["SID1"]["tracking"] == ["1Z-A", "1Z-SHARED", "1Z-B"])
check("SID1 carries extOrderId", groups["SID1"]["extOrderId"] == "#BE1")
check("freight SID2 excluded", "SID2" not in groups)
check("no-tracking SID3 excluded", "SID3" not in groups)
check("cancelled SID4 excluded", "SID4" not in groups)
check("untagged DTC SID5 excluded with require_tag", "SID5" not in groups)
check("without require_tag DTC SID5 included",
      "SID5" in pt.group_parcel_shipments(FFS, require_tag=None))

# ---- plan_shipment_update (forward-only Status + tracking) -----------------
# Processing -> In-Transit advances; tracking written.
p = pt.plan_shipment_update("Processing", None, ["1Z-A", "1Z-B"])
check("Processing advances to In-Transit", p.get(F_STATUS) == SHIP_STATUS_IN_TRANSIT)
check("tracking written joined", p.get(F_PARCEL_TRACKING) == "1Z-A, 1Z-B")

# Already In-Transit with the same tracking -> no write at all.
check("idempotent: no change", pt.plan_shipment_update("In-Transit", "1Z-A, 1Z-B", ["1Z-A", "1Z-B"]) == {})

# Already In-Transit but tracking changed -> only tracking written, not Status.
p = pt.plan_shipment_update("In-Transit", "1Z-OLD", ["1Z-A"])
check("In-Transit keeps status, updates tracking",
      F_STATUS not in p and p.get(F_PARCEL_TRACKING) == "1Z-A")

# More-advanced status (Arrived) -> Status held, tracking still written.
p = pt.plan_shipment_update("Arrived", None, ["1Z-A"])
check("Arrived not dragged back to In-Transit", F_STATUS not in p)
check("Arrived still gets tracking", p.get(F_PARCEL_TRACKING) == "1Z-A")

# Unmanaged/human status -> Status preserved, tracking still written.
p = pt.plan_shipment_update("On Hold - Customer", None, ["1Z-A"])
check("human status preserved", F_STATUS not in p)

# Blank current status -> In-Transit written.
p = pt.plan_shipment_update(None, None, ["1Z-A"])
check("blank status advances to In-Transit", p.get(F_STATUS) == SHIP_STATUS_IN_TRANSIT)

# ---- reconcile (end-to-end with fakes; update-only, SR resolution) --------
class FakeP17:
    def __init__(self, sr_map):
        self.sr_map = sr_map        # ext_order -> list of SRs
        self.calls = []

    def get_order_shipments(self, ext_order):
        self.calls.append(ext_order)
        return self.sr_map.get(ext_order, [])


class FakeAT:
    def __init__(self, records):
        self.records = records      # ext_shipment -> record dict (or missing)
        self.writes = []

    def find_record(self, table, field_name, value):
        assert field_name == SHIPMENT_NUMBER_NAME
        return self.records.get(value)

    def update_record(self, table, record_id, fields):
        self.writes.append((record_id, fields))
        return {"id": record_id}


# SID1 -> SR extShipmentId "#BE1-S1" (the Airtable Shipment Number); that row is "Processing".
p17 = FakeP17({"#BE1": [{"shipmentId": "SID1", "extShipmentId": "#BE1-S1"}]})
at = FakeAT({"#BE1-S1": {"id": "recAAA", "fields": {F_STATUS: "Processing"}}})
counts, updates = pt.reconcile(FFS, p17, at, dry_run=False, require_tag="Airtable")
check("reconcile updated exactly 1", counts["updated"] == 1)
check("reconcile wrote to recAAA", at.writes and at.writes[0][0] == "recAAA")
check("reconcile wrote In-Transit + tracking",
      at.writes[0][1].get(F_STATUS) == SHIP_STATUS_IN_TRANSIT
      and at.writes[0][1].get(F_PARCEL_TRACKING) == "1Z-A, 1Z-SHARED, 1Z-B")
check("reconcile resolved SRs once per order", p17.calls == ["#BE1"])

# Shipment not in Airtable -> never created, counted not_in_airtable.
at2 = FakeAT({})
counts2, _ = pt.reconcile(FFS, FakeP17({"#BE1": [{"shipmentId": "SID1", "extShipmentId": "#BE1-S1"}]}),
                          at2, dry_run=False, require_tag="Airtable")
check("missing Airtable row -> no write (update-only)", at2.writes == [])
check("missing Airtable row counted", counts2["not_in_airtable"] == 1)

# No SR match for the shipmentId -> skipped (no_sr), no write.
at3 = FakeAT({"#BE1-S1": {"id": "recAAA", "fields": {F_STATUS: "Processing"}}})
counts3, _ = pt.reconcile(FFS, FakeP17({"#BE1": []}), at3, dry_run=False, require_tag="Airtable")
check("no SR resolution -> no write", at3.writes == [])
check("no SR resolution counted", counts3["no_sr"] == 1)

# Dry-run -> plans but writes nothing.
at4 = FakeAT({"#BE1-S1": {"id": "recAAA", "fields": {F_STATUS: "Processing"}}})
counts4, updates4 = pt.reconcile(FFS, FakeP17({"#BE1": [{"shipmentId": "SID1", "extShipmentId": "#BE1-S1"}]}),
                                 at4, dry_run=True, require_tag="Airtable")
check("dry-run writes nothing", at4.writes == [])
check("dry-run counts would_update", counts4["would_update"] == 1 and counts4["updated"] == 0)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
