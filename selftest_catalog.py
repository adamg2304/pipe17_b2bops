"""Offline checks for pipe17_catalog.SkuResolver (no network).

Monkeypatches _iter_products so the resolver's catalog read is faked:
  - a normal catalog -> exact / alias resolution
  - a catalog read that raises (e.g. 403) -> fail open (passthrough), order flow survives
  - PIPE17_CATALOG_API_KEY selection

    python3 selftest_catalog.py
"""
import requests
import pipe17_catalog as pc
from pipe17_catalog import SkuResolver

US_INT = pc.CURRENCY_CHANNEL_INTEGRATION["USD"]

passed = failed = 0
def check(label, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {label}")
    else: failed += 1; print(f"  FAIL  {label}")


print("1) normal catalog -> exact + alias resolution")
def fake_catalog(_key):
    yield {"sku": "11-01-00-40-V6", "published": [{"integrationId": US_INT, "sku": "11-01-00-40"}]}
    yield {"sku": "13-11-00-00", "published": []}
pc._iter_products = fake_catalog
pc._CACHE.clear()
r = SkuResolver("USD")
check("catalog available", r.catalog_unavailable is False)
check("base alias -> versioned product", r.resolve("11-01-00-40") == ("11-01-00-40-V6", "alias"))
check("exact product sku passes through", r.resolve("13-11-00-00") == ("13-11-00-00", "exact"))
check("unknown sku -> unresolved", r.resolve("99-99-99-99") == ("99-99-99-99", "unresolved"))

print("2) catalog read fails (e.g. 403) -> fail open (passthrough)")
def boom(_key):
    raise requests.exceptions.HTTPError("403 Client Error: Forbidden")
    yield  # pragma: no cover
pc._iter_products = boom
pc._CACHE.clear()
r2 = SkuResolver("USD")
check("catalog_unavailable flagged", r2.catalog_unavailable is True)
check("SKU passes through, status 'passthrough' (no crash)",
      r2.resolve("11-01-00-40") == ("11-01-00-40", "passthrough"))
check("empty sku still 'unresolved'", r2.resolve("") == ("", "unresolved"))

print("3) PIPE17_CATALOG_API_KEY is preferred when set")
_saved = pc.PIPE17_CATALOG_API_KEY
try:
    pc.PIPE17_CATALOG_API_KEY = "CATKEY"
    check("catalog key used for any currency", pc._api_key("USD") == "CATKEY" and pc._api_key("CAD") == "CATKEY")
    pc.PIPE17_CATALOG_API_KEY = ""
    check("falls back to channel key when unset",
          pc._api_key("CAD") == pc.PIPE17_API_KEY_CA and pc._api_key("USD") == pc.PIPE17_API_KEY_US)
finally:
    pc.PIPE17_CATALOG_API_KEY = _saved

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
