"""Offline tests for the forward-only shipment Status guard. No network, no creds.

Run: HUBSPOT_TOKEN=x PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_shipment_status.py
"""
import main
from config import SHIP_STATUS_IN_REVIEW, SHIP_STATUS_PROCESSING

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


R = main._status_regresses

# Blank / missing current -> always writable (new row, or Status never set yet).
check("blank current writes", R(None, SHIP_STATUS_PROCESSING) is False)
check("empty current writes", R("", "Ready to Book") is False)

# Same rank -> not a regression (idempotent write allowed).
check("equal status not a regression", R(SHIP_STATUS_PROCESSING, SHIP_STATUS_PROCESSING) is False)

# Advancing -> allowed.
check("Processing -> Ready to Book advances", R(SHIP_STATUS_PROCESSING, "Ready to Book") is False)
check("Out of Stock -> Processing advances", R("Out of Stock", SHIP_STATUS_PROCESSING) is False)
check("In Review -> Processing advances", R(SHIP_STATUS_IN_REVIEW, SHIP_STATUS_PROCESSING) is False)
check("In-Transit -> Cancelled advances", R("In-Transit", "Cancelled") is False)

# Regressing (the reported bug) -> suppressed.
check("Ready to Book -> Processing regresses (HELD)", R("Ready to Book", SHIP_STATUS_PROCESSING) is True)
check("In-Transit -> Ready to Book regresses (HELD)", R("In-Transit", "Ready to Book") is True)
check("Arrived -> Processing regresses (HELD)", R("Arrived", SHIP_STATUS_PROCESSING) is True)
check("Cancelled -> Processing regresses (HELD)", R("Cancelled", SHIP_STATUS_PROCESSING) is True)

# A current status we don't manage (human/custom) is never clobbered.
check("unmanaged current preserved", R("On Hold - Customer", SHIP_STATUS_PROCESSING) is True)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
