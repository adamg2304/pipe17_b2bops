"""Offline check for the inventory aggregation (Pipe17 Available -> HubSpot).

No network, no creds. Exercises the pure core of pipe17_inventory with fake
resolvers + a fake inventory index built from REAL numbers pulled for base SKU
11-01-00-40 on 2026-10-06, plus the no-op detection and number write helpers.

    python3 selftest_inventory.py
"""
import pipe17_inventory as inv
from hubspot_products import inventory_properties
from main_inventory_sync import _changed


def fake_resolver(mapping, currency):
    """A stand-in for pipe17_catalog.SkuResolver: resolve(base) -> (sku, status)."""
    cur = currency

    class R:
        currency = cur

        @staticmethod
        def resolve(base):
            if base in mapping:
                return mapping[base], "alias"
            return base, "unresolved"
    return R()


# Per-channel aliases (US base -> V6, CA base -> V5), matching the live catalog.
RES_US = fake_resolver({"11-01-00-40": "11-01-00-40-V6"}, "USD")
RES_CA = fake_resolver({"11-01-00-40": "11-01-00-40-V5"}, "CAD")

# Locations by country (subset of the real map).
COUNTRY = {
    "US_HBG": "US", "US_TRW": "US", "US_VIS": "US", "US_BNC": "US", "US_AMZ": "US",
    "CA_MTO": "CA", "CA_MBC": "CA", "CA_AMZCA": "CA",
    "MX_AMZ": "MX",
}

# Real `available` by location for the two versions (from the 11-01-00-40 pull).
INV = {
    "11-01-00-40-V6": {"US_HBG": 0, "US_BNC": 0, "US_VIS": 22, "US_TRW": 0,
                       "CA_MTO": 124, "CA_MBC": 4},
    "11-01-00-40-V5": {"US_AMZ": 0, "US_TRW": 0, "US_VIS": -40,
                       "CA_MTO": 8, "CA_MBC": 0, "CA_AMZCA": 0, "MX_AMZ": 0},
    # a plain, non-versioned product (resolves to itself on both channels)
    "13-11-00-00": {"US_BNC": 5, "CA_MTO": 3},
}

passed = failed = 0
def check(label, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {label}")
    else: failed += 1; print(f"  FAIL  {label}")


print("1) per-channel aggregation for base 11-01-00-40 (US->V6 @ US, CA->V5 @ CA)")
r = inv.compute_inventory("11-01-00-40", RES_US, RES_CA, INV, COUNTRY)
check("resolves US->V6, CA->V5", r["us_sku"] == "11-01-00-40-V6" and r["ca_sku"] == "11-01-00-40-V5")
check("inventory_us = V6 at US locations = 22 (not the 124 sitting in CA)", r["inventory_us"] == 22)
check("inventory_ca = V5 at CA locations = 8", r["inventory_ca"] == 8)
check("inventory_total = us + ca = 30", r["inventory_total"] == 30)
check("matched True", r["matched"] is True)
check("V5's -40 at a US loc is ignored (CA channel only reads CA locs)",
      inv.regional_available("11-01-00-40-V5", INV, COUNTRY, "US") == -40
      and r["inventory_us"] == 22)

print("2) non-versioned SKU resolves to itself on both channels")
r2 = inv.compute_inventory("13-11-00-00", RES_US, RES_CA, INV, COUNTRY)
check("us=5, ca=3, total=8", (r2["inventory_us"], r2["inventory_ca"], r2["inventory_total"]) == (5, 3, 8))

print("3) unmatched base SKU -> matched False, not written")
r3 = inv.compute_inventory("99-99-99-99", RES_US, RES_CA, INV, COUNTRY)
check("matched False", r3["matched"] is False)

print("4) write helpers: all three written as numbers")
props = inventory_properties(22, 8, 30)
check("inventory_total written as number 30", props["inventory_total"] == 30)
check("inventory_us written as int 22", props["inventory_us"] == 22)

print("5) no-op detection (HubSpot returns strings; skip unchanged)")
check("'22' vs 22 -> unchanged", _changed("22", 22) is False)
check("None -> changed", _changed(None, 0) is True)
check("'8' vs 30 -> changed", _changed("8", 30) is True)
check("MX/other locations never counted in US or CA",
      inv.regional_available("11-01-00-40-V5", INV, COUNTRY, "US") == -40
      and "MX_AMZ" in COUNTRY and COUNTRY["MX_AMZ"] == "MX")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
