"""Offline self-test. No network, no GCP, no live creds.

Runs the real transform over a synthetic 6-split #TestCA1040 fixture and prints the
exact Airtable field dicts the sync would upsert, plus the Parent Order that the
Airtable "Parent Order (FX)" formula will derive from each Shipment Number. Proves
the mapping + the delimiter-agnostic parse before any deploy.

Reads transform output by FIELD ID (the sync writes field-ID keys, not names).

Run:  PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest.py
"""
import json
import os
import sys

os.environ.setdefault("PIPE17_API_KEY", "selftest")
os.environ.setdefault("AIRTABLE_API_KEY", "selftest")

from transform import shipment_to_airtable, order_number_of  # noqa: E402
from config import (  # noqa: E402
    NORMALIZE_SHIPMENT_NUMBER,
    F_SHIPMENT_NUMBER, F_ORIGIN_WH, F_STATUS, F_LINE_ITEMS,
)


def parent_order_fx(shipment_number: str) -> str:
    """Python port of the Airtable 'Parent Order (FX)' formula (delimiter-agnostic).
    Cuts at the first '(' or '.'; bare/empty pass through. Must match the base formula."""
    s = shipment_number or ""
    if s == "":
        return ""
    def pos(ch):
        i = s.find(ch)
        return len(s) + 1 if i == -1 else i + 1
    cut = min(pos("("), pos(".")) - 1
    return s[:max(0, cut)]


def main():
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "testca1040.json")
    payload = json.load(open(fixture))
    srs = payload["shipments"]

    print(f"NORMALIZE_SHIPMENT_NUMBER = {NORMALIZE_SHIPMENT_NUMBER} (expected False)\n")
    print(f"{'Shipment Number':<18}{'Parent Order (FX)':<20}{'Origin WH':<28}{'Status':<12}Line Items")
    print("-" * 110)

    failures = []
    for sr in srs:
        f = shipment_to_airtable(sr)
        sn = f.get(F_SHIPMENT_NUMBER, "")
        parent = parent_order_fx(sn)
        print(f"{sn:<18}{parent:<20}{f.get(F_ORIGIN_WH,'(blank)'):<28}{f.get(F_STATUS,'(blank)'):<12}{f.get(F_LINE_ITEMS,'')}")

        if NORMALIZE_SHIPMENT_NUMBER:
            failures.append("normalization should be OFF")
        if sn != sr["extShipmentId"]:
            failures.append(f"{sn}: shipment number was altered (normalization leaked)")
        if parent != "#TestCA1040":
            failures.append(f"{sn}: parent parsed as {parent!r}, expected '#TestCA1040'")
        if order_number_of(sr) != "#TestCA1040":
            failures.append(f"{sn}: order link key wrong")

    unmapped = next(s for s in srs if s["locationId"] == "UNMAPPED_LOC_9999")
    if F_ORIGIN_WH in shipment_to_airtable(unmapped):
        failures.append("unmapped locationId should leave Origin WH blank, not create an option")
    unknown = next(s for s in srs if s["status"] == "someUnknownStatus")
    if F_STATUS in shipment_to_airtable(unknown):
        failures.append("unknown status should leave Status blank (DEFAULT_SHIPMENT_STATUS=None)")

    print()
    if failures:
        print("FAIL:")
        for x in failures:
            print("  -", x)
        sys.exit(1)
    print("PASS: raw dot-form preserved, all 6 splits parse to #TestCA1040, "
          "unmapped WH + unknown status left blank (no junk options created).")


if __name__ == "__main__":
    main()
