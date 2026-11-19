"""One-time backfill: write the Pipe17 shipping request ID (shipmentId) onto existing
Airtable Shipments rows, so the shipmentId-keyed sync adopts them instead of creating
duplicates. Dry run by default; --apply writes.

For each Pipe17-created row (Pipe17 Shipping Request Link set) with no Pipe17 Shipping
Request ID, the order's shipping requests are fetched by extOrderId and every SR with the
row's Shipment Number is scored:

  lines    the row's Line Items ("1 x SKU - name; ...") parse to the same {SKU: qty} as
           the SR's line items. Airtable never stored line uniqueIds, so SKU + quantity
           is the line identity available on the row.
  location the row's Origin WH equals the SR location's name (blank Origin WH = unknown,
           not a mismatch).
  time     the SR was created no later than the row (+ ROW_LAG_HOURS). A row cannot
           belong to an SR created after it; this catches rows that a later SR with a
           reused number overwrote (#BE63027606419 / .2 now show the canceled backorder
           SR's lines but were created for the earlier live SRs).

The ID is written only when exactly one SR passes all three and no other row in the
order claims it. Everything else goes to the CSV with the reason, every candidate's
scores, and a suggested ID when exactly one candidate is time-consistent.

Usage (locally, or as the pipe17-airtable-sync job via the "Run Cloud Run Job" workflow
with args `backfill_shipment_ids.py` and log_lines raised; every unresolved row is logged
as a `BACKFILL_UNRESOLVED {json}` line):
  python backfill_shipment_ids.py                         # dry run, all rows
  python backfill_shipment_ids.py --order '#BE63027606419'
  python backfill_shipment_ids.py --csv /tmp/backfill.csv
  python backfill_shipment_ids.py --apply                 # write unambiguous matches
"""
import argparse
import csv
import datetime as dt
import json
import logging
import re
import sys
from collections import Counter, defaultdict

from config import (
    SHIPMENTS_TABLE, F_SHIP_REQUEST_ID, SHIP_REQUEST_ID_NAME, F_SHIPMENT_NUMBER,
    F_LINE_ITEMS, F_ORIGIN_WH, F_PIPE17_REQUEST_LINK,
)

log = logging.getLogger("pipe17-airtable")

ROW_LAG_HOURS = 6
PIPE17_REQUEST_LINK_NAME = "Pipe17 Shipping Request Link"
_SUFFIX = re.compile(r"\.\s*\d+$")
_LINE = re.compile(r"^\s*(\d+)\s*x\s*(\S+)")

CSV_COLUMNS = ["record_id", "shipment_number", "order", "reason", "suggested_shipment_id",
               "candidates", "row_origin_wh", "row_line_items"]


def order_of(number):
    """'#BE63027606419.2' -> '#BE63027606419' (a base number is its own order)."""
    return _SUFFIX.sub("", (number or "").strip())


def row_lines(text):
    """Counter{sku: qty} from a row's Line Items text, or None when it can't be parsed."""
    if not text:
        return None
    out = Counter()
    for part in str(text).split(";"):
        m = _LINE.match(part)
        if not m:
            return None
        out[m.group(2)] += int(m.group(1))
    return out


def sr_lines(sr):
    out = Counter()
    for li in sr.get("lineItems") or []:
        out[li.get("sku")] += int(li.get("quantity") or 0)
    return out


def _ts(v):
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def score(row, sr, loc_names):
    """(lines_ok, loc_ok, time_ok) for one row/SR pair. loc_ok is None when the row has
    no Origin WH; time_ok is None when either timestamp is missing."""
    f = row.get("fields") or {}
    lines_ok = row_lines(f.get(F_LINE_ITEMS)) == sr_lines(sr)
    wh = f.get(F_ORIGIN_WH)
    wh = wh.get("name") if isinstance(wh, dict) else wh
    loc_ok = None if not wh else wh == (loc_names or {}).get(sr.get("locationId"))
    row_t, sr_t = _ts(row.get("createdTime")), _ts(sr.get("createdAt"))
    time_ok = None if not (row_t and sr_t) else sr_t <= row_t + dt.timedelta(hours=ROW_LAG_HOURS)
    return lines_ok, loc_ok, time_ok


def plan_backfill(rows, srs_by_order, loc_names, claimed=()):
    """(matches, unresolved). matches: [(record_id, shipmentId, number)]. unresolved: CSV
    dicts. `claimed` = shipmentIds already on some row (never assigned twice)."""
    claimed = set(claimed)
    by_order = defaultdict(list)
    for r in rows:
        by_order[order_of((r.get("fields") or {}).get(F_SHIPMENT_NUMBER))].append(r)

    matches, unresolved = [], []
    for order, order_rows in sorted(by_order.items()):
        srs = srs_by_order.get(order)
        proposals = {}
        for r in order_rows:
            f = r.get("fields") or {}
            num = f.get(F_SHIPMENT_NUMBER)
            base = {"record_id": r["id"], "shipment_number": num, "order": order,
                    "row_origin_wh": _name(f.get(F_ORIGIN_WH)),
                    "row_line_items": f.get(F_LINE_ITEMS) or "",
                    "suggested_shipment_id": "", "candidates": ""}
            if srs is None:
                unresolved.append(dict(base, reason="pipe17_order_not_found"))
                continue
            cands = [s for s in srs if s.get("extShipmentId") == num
                     and s.get("shipmentId") not in claimed]
            scored = [(s, score(r, s, loc_names)) for s in cands]
            desc = "; ".join(
                f"{s['shipmentId']} {s.get('status')} lines={l} loc={lo} time={t}"
                for s, (l, lo, t) in scored)
            exact = [s for s, (l, lo, t) in scored if l and lo is not False and t is not False]
            timely = [s for s, (l, lo, t) in scored if t is not False]
            suggest = timely[0]["shipmentId"] if len(timely) == 1 else ""
            if not cands:
                unresolved.append(dict(base, reason="no_sr_with_this_number"))
            elif len(exact) == 1:
                proposals[r["id"]] = (exact[0]["shipmentId"], base, desc)
            elif not exact:
                unresolved.append(dict(base, reason="no_exact_match", candidates=desc,
                                       suggested_shipment_id=suggest))
            else:
                unresolved.append(dict(base, reason="multiple_exact_matches", candidates=desc,
                                       suggested_shipment_id=suggest))
        taken = Counter(sid for sid, _, _ in proposals.values())
        for rid, (sid, base, desc) in proposals.items():
            if taken[sid] > 1:
                unresolved.append(dict(base, reason="shipment_id_matched_by_several_rows",
                                       candidates=desc))
            else:
                matches.append((rid, sid, base["shipment_number"]))
    return matches, unresolved


def _name(v):
    return v.get("name") if isinstance(v, dict) else (v or "")


def write_csv(path, unresolved):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for row in unresolved:
            w.writerow({k: row.get(k, "") for k in CSV_COLUMNS})


def run(p17, at, loc_names, *, apply=False, only_order=None, csv_path="backfill_shipment_ids.csv"):
    formula = 'AND({%s}="", NOT({%s}=""))' % (SHIP_REQUEST_ID_NAME, PIPE17_REQUEST_LINK_NAME)
    rows = at.list_records(SHIPMENTS_TABLE, formula, field_ids=[
        F_SHIPMENT_NUMBER, F_LINE_ITEMS, F_ORIGIN_WH, F_PIPE17_REQUEST_LINK, F_SHIP_REQUEST_ID])
    if only_order:
        rows = [r for r in rows
                if order_of((r.get("fields") or {}).get(F_SHIPMENT_NUMBER)) == only_order]
    claimed = {(r.get("fields") or {}).get(F_SHIP_REQUEST_ID) for r in at.list_records(
        SHIPMENTS_TABLE, 'NOT({%s}="")' % SHIP_REQUEST_ID_NAME, field_ids=[F_SHIP_REQUEST_ID])}
    orders = sorted({order_of((r.get("fields") or {}).get(F_SHIPMENT_NUMBER)) for r in rows})
    log.info("Backfill: %d unkeyed Pipe17 row(s) across %d order(s); %d ID(s) already claimed",
             len(rows), len(orders), len(claimed - {None}))
    srs_by_order = {}
    for o in orders:
        try:
            srs = p17.get_order_shipments(o)
        except Exception:
            log.exception("Backfill: Pipe17 lookup failed for %s", o)
            continue
        if srs:
            srs_by_order[o] = srs
    matches, unresolved = plan_backfill(rows, srs_by_order, loc_names, claimed - {None})
    for rid, sid, num in matches:
        log.info("%s %s %s -> %s", "WRITE" if apply else "[DRY_RUN] would write", rid, num, sid)
    # Each unresolved row is also logged as one JSON line (same columns as the CSV), so a
    # Cloud Run execution, whose local CSV is lost with the container, can be reviewed from
    # its logs: filter on BACKFILL_UNRESOLVED. No customer name/address/email is included.
    for row in unresolved:
        log.info("BACKFILL_UNRESOLVED %s",
                 json.dumps({k: row.get(k, "") for k in CSV_COLUMNS}, sort_keys=False))
    write_csv(csv_path, unresolved)
    by_reason = Counter(u["reason"] for u in unresolved)
    log.info("Backfill: %d unambiguous match(es), %d to review (%s) in %s",
             len(matches), len(unresolved),
             ", ".join(f"{r}={n}" for r, n in sorted(by_reason.items())) or "none", csv_path)
    if apply and matches:
        at.update_records(SHIPMENTS_TABLE, [(rid, {F_SHIP_REQUEST_ID: sid}) for rid, sid, _ in matches])
        log.info("Backfill: wrote %d shipping request ID(s)", len(matches))
    return matches, unresolved


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write matches (default: dry run)")
    ap.add_argument("--order", help="only rows of this extOrderId, e.g. '#BE63027606419'")
    ap.add_argument("--csv", default="backfill_shipment_ids.csv",
                    help="where ambiguous/unmatched rows are written")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import pipe17_client as p17
    import airtable_client as at
    import main as sync
    run(p17, at, sync._location_names(), apply=args.apply, only_order=args.order,
        csv_path=args.csv)


if __name__ == "__main__":
    sys.exit(main())
