"""Offline check for the inventory aggregation (Pipe17 Available -> HubSpot).

No network, no creds. Exercises the pure core of pipe17_inventory: version-family
stem grouping, warehouse-country bucketing, and the number write helper. The
headline fixture is the REAL 11-01-00-50 family pulled 2026-10-06 (bare + V5 + V6),
which sums to US 1 / CA 428 / total 429.

    python3 selftest_inventory.py
"""
import pipe17_inventory as inv
from pipe17_inventory import base_stem, _index_rows, compute_inventory
from hubspot_products import inventory_properties
from main_inventory_sync import _changed

# Locations by country (subset of the real map).
COUNTRY = {
    "VIS": "US", "TRW": "US", "BNC": "US", "HBG": "US", "AMZ_US": "US",
    "MTO": "CA", "MBC": "CA", "AMZ_CA": "CA",
    "AMZ_MX": "MX", "AMZ_BR": "BR",
}

# Real 11-01-00-50 availability by (sku, location), across all three family members.
ROWS_50 = [
    {"sku": "11-01-00-50-V5", "locationId": "VIS", "available": 1},    # US
    {"sku": "11-01-00-50-V5", "locationId": "MTO", "available": 14},   # CA
    {"sku": "11-01-00-50-V5", "locationId": "TRW", "available": 0},    # committed -> 0
    {"sku": "11-01-00-50-V6", "locationId": "MBC", "available": 42},   # CA
    {"sku": "11-01-00-50-V6", "locationId": "MTO", "available": 365},  # CA
    {"sku": "11-01-00-50-V6", "locationId": "VIS", "available": 0},    # US
    {"sku": "11-01-00-50",    "locationId": "MTO", "available": 7},    # CA (bare base)
    {"sku": "11-01-00-50",    "locationId": "AMZ_MX", "available": 0}, # excluded region
    # a non-versioned product + a negative-availability family to exercise edges
    {"sku": "13-11-00-00", "locationId": "BNC", "available": 386},     # US
    {"sku": "13-11-00-00", "locationId": "MTO", "available": 437},     # CA
    {"sku": "77-00-00-00-V1", "locationId": "VIS", "available": -40},  # US, oversold
    {"sku": "77-00-00-00-V2", "locationId": "VIS", "available": 50},   # US
]

INDEX = _index_rows(ROWS_50)

passed = failed = 0
def check(label, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {label}")
    else: failed += 1; print(f"  FAIL  {label}")


print("1) base_stem strips the version suffix")
check("...-V6 -> base", base_stem("11-01-00-50-V6") == "11-01-00-50")
check("...-V12 -> base", base_stem("11-01-00-50-V12") == "11-01-00-50")
check("bare base unchanged", base_stem("11-01-00-50") == "11-01-00-50")
check("non-versioned unchanged", base_stem("13-11-00-00") == "13-11-00-00")

print("2) 11-01-00-50 family (bare + V5 + V6) summed by warehouse country")
r = compute_inventory("11-01-00-50", INDEX, COUNTRY)
check("inventory_us = 1 (only V5 @ Visalia)", r["inventory_us"] == 1)
check("inventory_ca = 428 (V6 365+42, V5 14, bare 7)", r["inventory_ca"] == 428)
check("inventory_total = 429", r["inventory_total"] == 429)
check("matched True", r["matched"] is True)
check("the three versions roll into one base key",
      set(INDEX["11-01-00-50"].keys()) == {"VIS", "MTO", "TRW", "MBC", "AMZ_MX"})

print("3) non-versioned SKU is its own base")
r2 = compute_inventory("13-11-00-00", INDEX, COUNTRY)
check("us=386, ca=437, total=823",
      (r2["inventory_us"], r2["inventory_ca"], r2["inventory_total"]) == (386, 437, 823))

print("4) availability sums across versions, negatives included")
r3 = compute_inventory("77-00-00-00", INDEX, COUNTRY)
check("US = -40 + 50 = 10 across V1+V2", r3["inventory_us"] == 10)

print("5) unmatched base SKU -> matched False")
r4 = compute_inventory("99-99-99-99", INDEX, COUNTRY)
check("matched False", r4["matched"] is False)

print("6) MX/BR warehouses never counted in US or CA")
check("AMZ_MX excluded", inv.regional_available(INDEX["11-01-00-50"], COUNTRY, "US") == 1
      and inv.regional_available(INDEX["11-01-00-50"], COUNTRY, "CA") == 428)

print("7) write helpers: all three written as numbers")
props = inventory_properties(1, 428, 429)
check("inventory_total number 429", props["inventory_total"] == 429)
check("inventory_us int 1", props["inventory_us"] == 1)

print("8) no-op detection (HubSpot returns strings)")
check("'428' vs 428 -> unchanged", _changed("428", 428) is False)
check("None -> changed", _changed(None, 0) is True)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
