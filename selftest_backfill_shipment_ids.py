"""Offline tests for backfill_shipment_ids (matching existing Airtable rows to Pipe17
shipping request IDs). No network, no creds. Uses the #BE63027606419 fixture: the live
Airtable rows as of 2026-11-18, and the same rows as of 2026-11-09 when the canceled
backorder SRs had overwritten #BE63027606419 and #BE63027606419.2.

Run: PIPE17_API_KEY=x AIRTABLE_API_KEY=x HUBSPOT_TOKEN=x python3 selftest_backfill_shipment_ids.py
"""
import csv
import json
import os
import tempfile

import backfill_shipment_ids as bf
from config import F_SHIP_REQUEST_ID as KEY, F_SHIPMENT_NUMBER as NUM, F_LINE_ITEMS, F_ORIGIN_WH

passed = failed = 0


def check(name, cond):
    global passed, failed
    passed, failed = (passed + 1, failed) if cond else (passed, failed + 1)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


HERE = os.path.dirname(os.path.abspath(__file__))
FIX = json.load(open(os.path.join(HERE, "fixtures", "BE63027606419_shipping_requests.json")))
SRS = {"#BE63027606419": FIX["shipments"]}
LOCS = FIX["locations"]

# ---- helpers
check("order_of strips the split suffix", bf.order_of("#BE63027606419.2") == "#BE63027606419")
check("order_of keeps a base number", bf.order_of("#BE63027606419") == "#BE63027606419")
check("order_of handles a stray space", bf.order_of("BE15505_REPL. 2") == "BE15505_REPL")
check("row_lines parses SKU + qty and sums repeats",
      bf.row_lines("1 x 88-03-00-99 - Leg; 1 x 88-03-00-99 - Leg; 2 x A - b") == {"88-03-00-99": 2, "A": 2})
check("row_lines rejects unparseable text", bf.row_lines("see notes") is None)

# ---- live rows (2026-11-18): all three unambiguous
matches, unresolved = bf.plan_backfill(FIX["airtable_rows_2026_11_18"], SRS, LOCS)
got = {num: sid for _, sid, num, _ in matches}
check("3 rows matched, none unresolved", len(matches) == 3 and unresolved == [])
check("#BE63027606419 -> fulfilled Visalia SR, not the canceled backorder SR",
      got.get("#BE63027606419") == "cead8dd5563caab7")
check("#BE63027606419.2 -> In-Desk Power SR, not the canceled backorder SR",
      got.get("#BE63027606419.2") == "604e19849f556c56")
check("#BE63027606419.3 -> its only SR", got.get("#BE63027606419.3") == "0c64cbec5be41092")

# ---- overwritten rows (2026-11-09): base and .2 show the canceled backorder SR's lines,
# which was created after them. live_candidate keys each to the live SR it was created for.
matches, unresolved = bf.plan_backfill(FIX["airtable_rows_2026_11_09_overwritten"], SRS, LOCS)
got = {num: (sid, rule) for _, sid, num, rule in matches}
check("overwritten state: all 3 rows keyed, none unresolved", len(matches) == 3 and not unresolved)
check("overwritten base row -> live Visalia SR (live_candidate)",
      got["#BE63027606419"] == ("cead8dd5563caab7", "live_candidate"))
check("overwritten .2 row -> live In-Desk Power SR (live_candidate)",
      got["#BE63027606419.2"] == ("604e19849f556c56", "live_candidate"))
check(".3 still exact", got["#BE63027606419.3"] == ("0c64cbec5be41092", "exact"))
check("the canceled backorder SRs are never picked",
      not {"d80e16ccb89961a5", "357c27692e38bf57"} & {sid for sid, _ in got.values()})

# ---- the new rules on synthetic rows (shapes seen in the 2026-11-19 dry run)
def _row(rid, num, created, wh="Inland Star - Visalia (VIS)", li="2 x 99-73-00-99 - Base"):
    return {"id": rid, "createdTime": created,
            "fields": {NUM: num, F_ORIGIN_WH: wh, F_LINE_ITEMS: li}}


def _sr(sid, num, status, created, loc="34b98f63b3ad51f6", lines=(("99-73-00-99", 2),)):
    return {"shipmentId": sid, "extShipmentId": num, "status": status, "locationId": loc,
            "createdAt": created, "lineItems": [{"sku": k, "quantity": q} for k, q in lines]}


def _pick(row, srs, order):
    m, u = bf.plan_backfill([row], {order: srs}, LOCS)
    return (m[0][1], m[0][3]) if m else (None, u[0]["reason"])


# #BE15xxx shape: row made by the pre-Pipe17 flow, Pipe17's only SR created later.
check("single_candidate: one SR, lines + location match, created after the row",
      _pick(_row("r1", "#BE15659", "2026-10-01T00:00:00Z"),
            [_sr("S1", "#BE15659", "canceled", "2026-10-20T00:00:00Z")], "#BE15659")
      == ("S1", "single_candidate"))
# #BE15561 shape: one live SR, nothing matches and it postdates the row -> left alone.
check("single live SR with no line/time match is not forced",
      _pick(_row("r2", "#BE15561", "2026-10-01T00:00:00Z", li=""),
            [_sr("S2", "#BE15561", "reviewRequired", "2026-10-20T00:00:00Z")], "#BE15561")
      == (None, "no_exact_match"))
# #BE66642454884 shape: original canceled, live replacement reused the number + overwrote.
check("live_candidate: the one live SR wins over the canceled original",
      _pick(_row("r3", "#BE7", "2026-10-01T00:00:00Z"),
            [_sr("ORIG", "#BE7", "canceledRestock", "2026-09-30T00:00:00Z", lines=(("X", 1),)),
             _sr("LIVE", "#BE7", "fulfilled", "2026-10-10T00:00:00Z")], "#BE7")
      == ("LIVE", "live_candidate"))
# #BE65572617229 shape: every candidate canceled, one created before the row.
check("all_canceled: the SR created before the row",
      _pick(_row("r4", "#BE8", "2026-10-01T00:00:00Z"),
            [_sr("OLD", "#BE8", "canceled", "2026-09-30T00:00:00Z", lines=(("X", 1),)),
             _sr("NEW", "#BE8", "canceledRestock", "2026-10-10T00:00:00Z")], "#BE8")
      == ("OLD", "all_canceled"))
check("all canceled, none created before the row -> left alone",
      _pick(_row("r5", "#BE9", "2026-10-01T00:00:00Z"),
            [_sr("A", "#BE9", "canceled", "2026-10-05T00:00:00Z", lines=(("X", 1),)),
             _sr("B", "#BE9", "canceled", "2026-10-10T00:00:00Z", lines=(("Y", 1),))], "#BE9")
      == (None, "no_exact_match"))
check("two live SRs, neither exact -> left alone",
      _pick(_row("r6", "#BE10", "2026-10-01T00:00:00Z"),
            [_sr("A", "#BE10", "reviewRequired", "2026-09-30T00:00:00Z", lines=(("X", 1),)),
             _sr("B", "#BE10", "sentToFulfillment", "2026-09-30T00:00:00Z", lines=(("Y", 1),))],
            "#BE10")
      == (None, "no_exact_match"))

# ---- other reasons
claimed = {"cead8dd5563caab7", "604e19849f556c56", "0c64cbec5be41092"}
matches, unresolved = bf.plan_backfill(FIX["airtable_rows_2026_11_18"], SRS, LOCS, claimed=claimed)
check("IDs already on another row are never reassigned",
      matches == [] and all(u["reason"] in ("no_exact_match", "no_sr_with_this_number")
                            for u in unresolved))

matches, unresolved = bf.plan_backfill(FIX["airtable_rows_2026_11_18"], {}, LOCS)
check("order missing in Pipe17 -> pipe17_order_not_found",
      not matches and {u["reason"] for u in unresolved} == {"pipe17_order_not_found"})

twin = dict(FIX["shipments"][2], shipmentId="TWIN0000000000000")
matches, unresolved = bf.plan_backfill(FIX["airtable_rows_2026_11_18"][:1],
                                       {"#BE63027606419": [FIX["shipments"][2], twin]}, LOCS)
check("two identical SRs -> multiple_exact_matches, nothing written",
      not matches and unresolved[0]["reason"] == "multiple_exact_matches")

dup_rows = [FIX["airtable_rows_2026_11_18"][0],
            dict(FIX["airtable_rows_2026_11_18"][0], id="recDUPLICATE00000")]
matches, unresolved = bf.plan_backfill(dup_rows, SRS, LOCS)
check("two rows claiming one SR -> neither written",
      not matches and {u["reason"] for u in unresolved} == {"shipment_id_matched_by_several_rows"})

blank_wh = [dict(FIX["airtable_rows_2026_11_18"][0],
                 fields=dict(FIX["airtable_rows_2026_11_18"][0]["fields"], **{F_ORIGIN_WH: None}))]
matches, _ = bf.plan_backfill(blank_wh, SRS, LOCS)
check("blank Origin WH is unknown, not a mismatch", len(matches) == 1)


# ---- run(): dry run writes nothing, --apply writes exactly the matches, CSV written
class FakeAT:
    def __init__(self, rows):
        self.rows, self.writes = rows, []

    def list_records(self, table, formula, field_ids=None):
        if formula.startswith("NOT("):
            return []                  # nothing keyed yet
        return self.rows

    def update_records(self, table, updates):
        self.writes.extend(updates)
        return []


class FakeP17:
    def get_order_shipments(self, ext):
        return SRS.get(ext, [])


tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "out.csv")
NO_SR = _row("recNOSR", "#BE63027606419.9", "2026-11-01T00:00:00Z")      # number Pipe17 never issued
HANDMADE = _row("recHAND", "#BE63027606419p", "2026-11-01T00:00:00Z")    # ops-made "p" copy
RUN_ROWS = FIX["airtable_rows_2026_11_09_overwritten"] + [NO_SR, HANDMADE]
at = FakeAT(RUN_ROWS)
bf.run(FakeP17(), at, LOCS, apply=False, csv_path=path)
check("dry run writes nothing", at.writes == [])
rows = list(csv.DictReader(open(path)))
check("CSV has the two unresolvable rows", sorted(r["shipment_number"] for r in rows)
      == ["#BE63027606419.9", "#BE63027606419p"])
bf.run(FakeP17(), at, LOCS, apply=True, csv_path=path)
check("--apply writes exactly the 3 matches",
      sorted(at.writes) == sorted([("rec0HOQvanaQp2YDt", {KEY: "0c64cbec5be41092"}),
                                   ("recN3ByfNCxMPIrew", {KEY: "cead8dd5563caab7"}),
                                   ("recbETsKPT1Ln7RzM", {KEY: "604e19849f556c56"})]))
at2 = FakeAT(FIX["airtable_rows_2026_11_18"])
bf.run(FakeP17(), at2, LOCS, apply=False, csv_path=path, only_order="#BE99")
check("--order filters to that order's rows", at2.writes == [])

# ---- unresolved rows are logged one JSON line each (Cloud Run has no CSV to read)
import logging  # noqa: E402


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap = _Cap()
bf.log.addHandler(cap)
bf.log.setLevel(logging.INFO)
bf.run(FakeP17(), FakeAT(RUN_ROWS), LOCS, csv_path=path)
bf.log.removeHandler(cap)
logged = [json.loads(l.split(" ", 1)[1]) for l in cap.lines if l.startswith("BACKFILL_UNRESOLVED ")]
check("each unresolved row logged as one JSON line",
      sorted(r["shipment_number"] for r in logged) == ["#BE63027606419.9", "#BE63027606419p"])
check("logged line carries the reason",
      {r["reason"] for r in logged} == {"no_sr_with_this_number", "pipe17_order_not_found"})
check("would-write lines name the rule",
      any("cead8dd5563caab7 (live_candidate)" in l for l in cap.lines))
check("summary line counts matches by rule and unresolved by reason",
      any("exact=1, live_candidate=2" in l and "no_sr_with_this_number=1" in l
          for l in cap.lines))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
