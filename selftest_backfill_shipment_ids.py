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
got = {num: sid for _, sid, num in matches}
check("3 rows matched, none unresolved", len(matches) == 3 and unresolved == [])
check("#BE63027606419 -> fulfilled Visalia SR, not the canceled backorder SR",
      got.get("#BE63027606419") == "cead8dd5563caab7")
check("#BE63027606419.2 -> In-Desk Power SR, not the canceled backorder SR",
      got.get("#BE63027606419.2") == "604e19849f556c56")
check("#BE63027606419.3 -> its only SR", got.get("#BE63027606419.3") == "0c64cbec5be41092")

# ---- overwritten rows (2026-11-09): content matches the canceled SRs, but those were
# created after the rows, so nothing is written for them; CSV suggests the timely SR.
matches, unresolved = bf.plan_backfill(FIX["airtable_rows_2026_11_09_overwritten"], SRS, LOCS)
check("overwritten state: only .3 written", [m[2] for m in matches] == ["#BE63027606419.3"])
by_num = {u["shipment_number"]: u for u in unresolved}
check("overwritten base row -> CSV, no_exact_match",
      by_num["#BE63027606419"]["reason"] == "no_exact_match")
check("...suggests the SR the row was created for",
      by_num["#BE63027606419"]["suggested_shipment_id"] == "cead8dd5563caab7")
check("overwritten .2 row suggests the In-Desk Power SR",
      by_num["#BE63027606419.2"]["suggested_shipment_id"] == "604e19849f556c56")
check("CSV candidates show the canceled SR failing the time check",
      "d80e16ccb89961a5 canceledRestock lines=True loc=True time=False"
      in by_num["#BE63027606419"]["candidates"])

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
at = FakeAT(FIX["airtable_rows_2026_11_09_overwritten"])
bf.run(FakeP17(), at, LOCS, apply=False, csv_path=path)
check("dry run writes nothing", at.writes == [])
rows = list(csv.DictReader(open(path)))
check("CSV has the two overwritten rows", sorted(r["shipment_number"] for r in rows)
      == ["#BE63027606419", "#BE63027606419.2"])
bf.run(FakeP17(), at, LOCS, apply=True, csv_path=path)
check("--apply writes only the unambiguous match",
      at.writes == [("rec0HOQvanaQp2YDt", {KEY: "0c64cbec5be41092"})])
at2 = FakeAT(FIX["airtable_rows_2026_11_18"])
bf.run(FakeP17(), at2, LOCS, apply=False, csv_path=path, only_order="#BE99")
check("--order filters to that order's rows", at2.writes == [])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
