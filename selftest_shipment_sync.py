"""Offline tests for the shipmentId-keyed shipment sync + replacement sweep.
No network, no creds. Test data: the live #BE63027606419 shipping requests in
fixtures/BE63027606419_shipping_requests.json (two extShipmentId collisions, two canceled
backorder SRs, line-7/line-8 with no live SR), plus small synthetic orders for the
one-to-one, split and chain cases.

Run: PIPE17_API_KEY=x AIRTABLE_API_KEY=x HUBSPOT_TOKEN=x python3 selftest_shipment_sync.py
"""
import datetime as dt
import json
import os

# The sweep's link/flag fields don't exist in the base yet; give them test IDs so the
# tests exercise the full write path. Must be set before config is imported.
os.environ.setdefault("F_REPLACED_BY", "fldTESTREPLACEDBY")
os.environ.setdefault("F_REPLACES", "fldTESTREPLACES00")
os.environ.setdefault("F_ORPHANED", "fldTESTORPHANED00")

import shipment_sync as ss  # noqa: E402
from config import (  # noqa: E402
    F_SHIP_REQUEST_ID as KEY, F_SHIPMENT_NUMBER as NUM, F_STATUS, F_ORIGIN_WH, F_LINE_ITEMS,
    F_REPLACED_BY, F_REPLACES, F_ORPHANED, F_SHIP_NOTES, SHIP_REQUEST_ID_NAME,
)

passed = failed = 0


def check(name, cond):
    global passed, failed
    passed, failed = (passed + 1, failed) if cond else (passed, failed + 1)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


HERE = os.path.dirname(os.path.abspath(__file__))
FIX = json.load(open(os.path.join(HERE, "fixtures", "BE63027606419_shipping_requests.json")))
SRS = FIX["shipments"]
LOCS = FIX["locations"]
NOW = dt.datetime(2026, 11, 18, 22, 0, tzinfo=dt.timezone.utc)
BY_SID = {s["shipmentId"]: s for s in SRS}
D80E, C357 = "d80e16ccb89961a5", "357c27692e38bf57"
CEAD, P604, C0C6 = "cead8dd5563caab7", "604e19849f556c56", "0c64cbec5be41092"


# --------------------------------------------------------------------------- fakes
class FakeAT:
    """In-memory Shipments table: records keyed by Airtable id, fields by field id.
    Select values are stored as plain names (what the REST API returns)."""

    def __init__(self, rows=None):
        self.rows = {}
        self.n = 0
        self.upserts = []
        self.updates = []
        for fields in rows or []:
            self._new(fields)

    def _new(self, fields):
        self.n += 1
        rid = f"rec{self.n:03d}"
        self.rows[rid] = {"id": rid, "createdTime": f"2026-11-0{min(self.n, 9)}T00:00:00Z",
                          "fields": dict(fields)}
        return rid

    def find_records_by_values(self, table, field_name, field_id, values):
        assert field_name == SHIP_REQUEST_ID_NAME and field_id == KEY
        found, dupes = {}, {}
        for r in sorted(self.rows.values(), key=lambda r: r["createdTime"]):
            v = r["fields"].get(KEY)
            if v in values:
                if v in found:
                    dupes.setdefault(v, []).append(r["id"])
                else:
                    found[v] = {"id": r["id"], "createdTime": r["createdTime"],
                                "fields": dict(r["fields"])}
        return found, dupes

    def upsert(self, table, records, merge_fields):
        assert merge_fields == [KEY], "Shipments must upsert on the shipping request ID only"
        self.upserts.append(records)
        out, created, updated = [], 0, 0
        for f in records:
            hit = next((r for r in self.rows.values() if r["fields"].get(KEY) == f[KEY]), None)
            if hit:
                hit["fields"].update(f)
                updated += 1
            else:
                hit = self.rows[self._new(f)]
                created += 1
            out.append({"id": hit["id"], "fields": dict(hit["fields"])})
        return created, updated, out

    def update_records(self, table, updates):
        self.updates.append(updates)
        out = []
        for rid, f in updates:
            self.rows[rid]["fields"].update(f)
            out.append({"id": rid, "fields": dict(self.rows[rid]["fields"])})
        return out

    def by_sid(self, sid):
        return next(r for r in self.rows.values() if r["fields"].get(KEY) == sid)


class FakeP17:
    def __init__(self, srs, order=None):
        self.srs, self.order = srs, order

    def get_order_shipments(self, ext):
        return list(self.srs)

    def get_order_by_ext_id(self, ext):
        return self.order


def run(srs, at, order=None, now=NOW, dry_run=False, notify=None):
    return ss.sync_order("#BE63027606419", p17=FakeP17(srs, order), at=at, loc_names=LOCS,
                         order_row_id="recORDER", dry_run=dry_run, now=now, notify=notify)


def sr(sid, status, lines, created, loc="7e17305c31fc642e", canceled=None, num="#X"):
    d = {"shipmentId": sid, "extShipmentId": num, "status": status, "locationId": loc,
         "createdAt": created, "updatedAt": canceled or created,
         "lineItems": [{"uniqueId": u, "sku": u, "quantity": 1} for u in lines]}
    if canceled:
        d["canceledAt"] = canceled
    return d


# =========================================================================== 1. dedupe
print("-- dedupe on colliding extShipmentIds (#BE63027606419 fixture)")
check("fixture has two collisions on extShipmentId",
      sorted(s["extShipmentId"] for s in SRS).count("#BE63027606419") == 2
      and sorted(s["extShipmentId"] for s in SRS).count("#BE63027606419.2") == 2)
at = FakeAT()
rep = run(SRS, at)
sids = sorted(r["fields"][KEY] for r in at.rows.values())
check("5 SRs -> 5 distinct Airtable rows", len(at.rows) == 5 and len(set(sids)) == 5)
check("rows keyed by shipmentId", set(sids) == set(BY_SID))
check("both #BE63027606419 rows exist side by side",
      sorted(r["fields"][NUM] for r in at.rows.values()).count("#BE63027606419") == 2)
check("fulfilled cead8dd5 keeps its own Visalia row",
      at.by_sid(CEAD)["fields"][F_ORIGIN_WH] == "Inland Star - Visalia (VIS)")
check("canceled d80e row carries the backorder lines, not cead8dd5's",
      "14-03-00-77" in at.by_sid(D80E)["fields"][F_LINE_ITEMS]
      and "14-03-00-77" not in at.by_sid(CEAD)["fields"][F_LINE_ITEMS])
check("live .2 (In-Desk Power) row untouched by canceled .2",
      "13-11-00-00" in at.by_sid(P604)["fields"][F_LINE_ITEMS])

# Existing row for the live SR, keyed: a canceled SR reusing its number creates its own row.
at = FakeAT([{KEY: CEAD, NUM: "#BE63027606419", F_STATUS: "Booked", F_LINE_ITEMS: "kept"}])
run([BY_SID[CEAD], BY_SID[D80E]], at)
check("canceled SR reusing a number never touches the live row",
      at.by_sid(CEAD)["fields"][F_STATUS] == "Booked"
      and at.by_sid(CEAD)["fields"][F_LINE_ITEMS] != "1 x 14-03-00-77 - Credenza - Right / Woodgrain")

# A row with the same Shipment Number but no key is never matched.
at = FakeAT([{NUM: "#BE63027606419", F_STATUS: "Booked"}])
run([BY_SID[CEAD]], at)
check("unkeyed row with the same Shipment Number is not matched (new keyed row created)",
      len(at.rows) == 2 and at.rows["rec001"]["fields"].get(KEY) is None)

# =========================================================================== 2. orphans
print("-- orphan detection (fixture: line-7/line-8 have no live SR)")
plan = ss.plan_replacements(SRS, NOW)
check("both canceled SRs planned", set(plan) == {D80E, C357})
check("d80e orphaned on line-7, line-8",
      plan[D80E]["orphaned"] and plan[D80E]["orphan_lines"] == ["line-7", "line-8"]
      and plan[D80E]["replaced_by"] == [])
check("357c orphaned on line-7, line-8", plan[C357]["orphaned"] and not plan[C357]["replaced_by"])
at = FakeAT()
alerts = []
rep = run(SRS, at, notify=alerts.append)
check("orphaned rows flagged + Cancelled",
      all(at.by_sid(s)["fields"].get(F_ORPHANED) is True
          and at.by_sid(s)["fields"].get(F_STATUS) == "Cancelled" for s in (D80E, C357)))
check("one Slack alert naming both SRs and the lines",
      len(alerts) == 1 and D80E in alerts[0] and C357 in alerts[0] and "line-7" in alerts[0])
rep2 = run(SRS, at, notify=alerts.append)
check("orphan alert not repeated on the next run", len(alerts) == 1)

just = dt.datetime(2026, 11, 12, 23, 50, tzinfo=dt.timezone.utc)   # 3 min after 357c canceled
p = ss.plan_replacements(SRS, just)
check("inside the grace window: pending, not orphaned",
      p[C357]["pending"] and not p[C357]["orphaned"])
check("canceled order: missing lines are not orphans",
      not ss.plan_replacements(SRS, NOW, order={"status": "canceled"})[D80E]["orphaned"])
check("order no longer has the line: not an orphan",
      not ss.plan_replacements(SRS, NOW, order={"status": "readyForFulfillment",
                                                 "lineItems": [{"uniqueId": "line-6", "quantity": 1}]})[D80E]["orphaned"])
check("bundle child counts as needed via its parent line",
      ss._needed("line-1_2", {"line-1"}) and not ss._needed("line-2_1", {"line-1"}))

# =========================================================================== 3. 1:1
print("-- one-to-one replacement")
A = sr("A", "canceledRestock", ["line-7"], "2026-11-05T10:00:00Z", loc="8f1d050785329517",
       canceled="2026-11-06T10:00:00Z", num="#O1")
B = sr("B", "sentToFulfillment", ["line-7"], "2026-11-06T10:05:00Z", num="#O1")
at = FakeAT()
run([A, B], at)
check("A -> Replaced", at.by_sid("A")["fields"][F_STATUS] == "Replaced")
check("A Replaced By = B's row", at.by_sid("A")["fields"][F_REPLACED_BY] == [at.by_sid("B")["id"]])
check("B Replaces = A's row", at.by_sid("B")["fields"][F_REPLACES] == [at.by_sid("A")["id"]])
check("A not orphaned", at.by_sid("A")["fields"].get(F_ORPHANED) is False
      or not at.by_sid("A")["fields"].get(F_ORPHANED))

# Late replacement: orphan first, then the replacement arrives -> Replaced, flag cleared.
at = FakeAT()
run([A], at)
check("before the reroute: A orphaned", at.by_sid("A")["fields"][F_ORPHANED] is True)
run([A, B], at)
check("after the reroute: A Replaced, Orphaned cleared",
      at.by_sid("A")["fields"][F_STATUS] == "Replaced" and at.by_sid("A")["fields"][F_ORPHANED] is False)

# =========================================================================== 4. 1:many
print("-- one-to-many split")
A2 = sr("A2", "canceled", ["line-7", "line-8"], "2026-11-05T10:00:00Z", canceled="2026-11-06T10:00:00Z")
B2 = sr("B2", "sentToFulfillment", ["line-7"], "2026-11-06T10:05:00Z")
C2 = sr("C2", "reviewRequired", ["line-8"], "2026-11-06T10:06:00Z")
plan = ss.plan_replacements([A2, B2, C2], NOW)
check("split resolves to both live SRs, newest first", plan["A2"]["replaced_by"] == ["C2", "B2"])
at = FakeAT()
run([A2, B2, C2], at)
check("A2 Replaced By both rows",
      sorted(at.by_sid("A2")["fields"][F_REPLACED_BY]) == sorted([at.by_sid("B2")["id"], at.by_sid("C2")["id"]]))
check("each replacement Replaces A2",
      at.by_sid("B2")["fields"][F_REPLACES] == [at.by_sid("A2")["id"]]
      and at.by_sid("C2")["fields"][F_REPLACES] == [at.by_sid("A2")["id"]])
# Partial split: one line rerouted, the other has nowhere to go.
plan = ss.plan_replacements([A2, B2], NOW)
check("partial split: Replaced By B2, line-8 orphaned",
      plan["A2"]["replaced_by"] == ["B2"] and plan["A2"]["orphan_lines"] == ["line-8"]
      and plan["A2"]["orphaned"])
# An older live SR carrying the same line is not a replacement.
OLD = sr("OLD", "fulfilled", ["line-7"], "2026-11-01T00:00:00Z")
check("live SR created before the canceled one is not its replacement",
      ss.plan_replacements([OLD, A2, B2, C2], NOW)["A2"]["replaced_by"] == ["C2", "B2"])

# =========================================================================== 5. chain
print("-- double-cancel chain")
A3 = sr("A3", "canceledRestock", ["line-7", "line-8"], "2026-11-05T10:00:00Z", canceled="2026-11-05T11:00:00Z")
B3 = sr("B3", "canceledRestock", ["line-7", "line-8"], "2026-11-05T11:05:00Z", canceled="2026-11-07T09:00:00Z")
C3 = sr("C3", "sentToFulfillment", ["line-7", "line-8"], "2026-11-07T09:05:00Z")
plan = ss.plan_replacements([A3, B3, C3], NOW)
check("first cancel resolves to the newest live SR, not the canceled middle one",
      plan["A3"]["replaced_by"] == ["C3"])
check("second cancel resolves to the same live SR", plan["B3"]["replaced_by"] == ["C3"])
at = FakeAT()
run([A3, B3, C3], at)
check("C3 Replaces both canceled rows",
      sorted(at.by_sid("C3")["fields"][F_REPLACES]) == sorted([at.by_sid("A3")["id"], at.by_sid("B3")["id"]]))
check("both canceled rows Replaced",
      at.by_sid("A3")["fields"][F_STATUS] == "Replaced" and at.by_sid("B3")["fields"][F_STATUS] == "Replaced")

# =========================================================================== 6. forward-only + backorder
print("-- forward-only status + backorder handling")
SENT = sr("S", "sentToFulfillment", ["line-1"], "2026-11-01T00:00:00Z")
BACK = sr("BO", "reviewRequired", ["line-7"], "2026-11-05T00:00:00Z", loc="8f1d050785329517")
f = ss.plan_sr_fields(BACK, None, LOCS)
check("new backorder SR -> Status Backordered", f[F_STATUS] == "Backordered")
check("backorder recognised by location name too",
      ss.is_backorder({"locationId": "zzz"}, {"zzz": "Canada Backorders Simons"}))
check("backorder recognised by no-fulfillment integration",
      ss.is_backorder({"locationId": "zzz", "fulfillmentIntegrationId": "no-fulfillment-integration"}))
check("real warehouse SR is not backorder", not ss.is_backorder(BY_SID[CEAD], LOCS))
for cur, sr_status, label in [
        ("Processing", "reviewRequired", "Processing (sent) is not moved back to In Review"),
        ("Ready to Book", "sentToFulfillment", "Ready to Book is not moved back to Processing"),
        ("Processing", None, "Processing is not moved back to Backordered")]:
    the_sr = dict(SENT, status=sr_status) if sr_status else dict(BACK)
    if sr_status is None:
        the_sr["status"] = "reviewRequired"
    f = ss.plan_sr_fields(the_sr, {KEY: the_sr["shipmentId"], F_STATUS: cur}, LOCS)
    check(label, F_STATUS not in f)
f = ss.plan_sr_fields(SENT, {KEY: "S", F_STATUS: "In Review"}, LOCS)
check("In Review advances to Processing on send", f.get(F_STATUS) == "Processing")
f = ss.plan_sr_fields(SENT, {KEY: "S", F_STATUS: "Booked"}, LOCS)
check("Ops-set status (Booked) is preserved", F_STATUS not in f)
f = ss.plan_sr_fields(BY_SID[D80E], {KEY: D80E, F_STATUS: "In Review"}, LOCS, canceled_status="Cancelled")
check("canceled SR on an existing row: phase 1 writes no Status (sweep owns it)", F_STATUS not in f)
f = ss.plan_sr_fields(BY_SID[D80E], None, LOCS, canceled_status="Cancelled")
check("canceled SR on a NEW row: created with its final status", f.get(F_STATUS) == "Cancelled")
at = FakeAT()
run(SRS, at)
created = [r for batch in at.upserts for r in batch if r[KEY] in (D80E, C357)]
check("canceled rows are never created with a blank Status (LateShipment webhook)",
      created and all(r.get(F_STATUS) == "Cancelled" for r in created))
at = FakeAT()
run([A, B], at)
created = [r for batch in at.upserts for r in batch if r[KEY] == "A"]
check("replaced SR's new row is created as Replaced", created[0].get(F_STATUS) == "Replaced")
at = FakeAT([{KEY: "Z", F_STATUS: "Completed"}])
run([sr("Z", "canceled", ["line-1"], "2026-11-01T00:00:00Z", canceled="2026-11-02T00:00:00Z"),
     sr("Z2", "fulfilled", ["line-1"], "2026-11-02T00:05:00Z")], at)
check("sweep never overwrites a Completed row", at.by_sid("Z")["fields"][F_STATUS] == "Completed")

# =========================================================================== 7. copy + idempotency
print("-- ops field copy + idempotency")
at = FakeAT([{KEY: "A", F_SHIP_NOTES: "Customer wants AM delivery"},
             {KEY: "B", F_SHIP_NOTES: ""}])
run([A, B], at)
check("Notes copied from canceled row to empty replacement",
      at.by_sid("B")["fields"][F_SHIP_NOTES] == "Customer wants AM delivery")
at = FakeAT([{KEY: "A", F_SHIP_NOTES: "old note"}, {KEY: "B", F_SHIP_NOTES: "ops typed this"}])
run([A, B], at)
check("replacement's own Notes never overwritten", at.by_sid("B")["fields"][F_SHIP_NOTES] == "ops typed this")

for label, srs in [("fixture", SRS), ("split", [A2, B2, C2]), ("chain", [A3, B3, C3])]:
    at = FakeAT()
    run(srs, at)
    n_up, n_upd = len(at.upserts), len(at.updates)
    rep = run(srs, at)
    check(f"re-run on {label} plans no writes",
          all(a["action"] == "none" for a in rep["actions"])
          and len(at.upserts) == n_up and len(at.updates) == n_upd)

# Dry run writes nothing and still shows the full plan.
at = FakeAT()
rep = run(SRS, at, dry_run=True)
check("dry run writes nothing", not at.rows and not at.upserts and not at.updates)
check("dry run plans 5 creates", sum(a["action"] == "create" for a in rep["actions"]) == 5)
check("dry run plans the sweep for both canceled SRs",
      {a["sid"] for a in rep["actions"] if a["phase"] == "sweep"} >= {D80E, C357})

# =========================================================================== 8. main wiring
print("-- main.sync_shipments: tag gates the order, every SR on it is synced")
import main  # noqa: E402


class MainP17(FakeP17):
    def iter_shipping_requests(self, since, tag=None):
        return iter([s for s in self.srs if s["shipmentId"] == "A"])   # only A in the window

    def get_shipping_request(self, sid):
        return None


class MainAT(FakeAT):
    def find_record_id(self, table, field, value):
        return "recORDER"

    def list_records(self, table, formula, field_ids=None):
        return []


A_T = dict(A, tags=["Airtable"], extOrderId="#O1")
B_U = dict(B, tags=["Parcel"], extOrderId="#O1")      # replacement routed WITHOUT the tag
main.p17, main.at = MainP17([A_T, B_U]), MainAT()
main.DRY_RUN, main.GENERATE_PACKING_LIST = False, False
main.SHIPMENTS_KEYED_SYNC = False
paused = main.sync_shipments("2026-11-01T00:00:00Z", "Airtable")
check("rollout switch off: shipment sync paused, nothing written",
      paused.get("paused") and not main.at.rows and not main.at.upserts)
main.SHIPMENTS_KEYED_SYNC = True
main._location_names = lambda: LOCS
main.notify.slack = lambda text: None
totals = main.sync_shipments("2026-11-01T00:00:00Z", "Airtable")
check("one order synced", totals["orders"] == 1 and totals["failed"] == 0)
check("untagged replacement on a tagged order still gets a row",
      {r["fields"][KEY] for r in main.at.rows.values()} == {"A", "B"})
check("and is linked as the replacement",
      main.at.by_sid("A")["fields"][F_REPLACED_BY] == [main.at.by_sid("B")["id"]])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
