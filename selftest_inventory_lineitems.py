"""Offline tests for the inventory line-item fan-out. No network, no creds.

Run: HUBSPOT_TOKEN=x PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_inventory_lineitems.py
"""
import main_inventory_sync as m

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


# A fixed inventory snapshot: base 11-01-00-50 has stock, 99-99-99-99 has none.
INV_BY_BASE = {"11-01-00-50": {"usLoc": 7, "caLoc": 3}}
COUNTRY = {"usLoc": "US", "caLoc": "CA"}


def _compute(base):
    return m.inv.compute_inventory(base, INV_BY_BASE, COUNTRY)


# ---- line-item pass -------------------------------------------------------

def run_line_items(line_items, dry_run=False):
    """Drive _sync_line_items with a stubbed HubSpot line-item client."""
    captured = []
    m.hsli.iter_active_deal_line_items = lambda: iter(line_items)
    m.hsli.batch_update = lambda updates: captured.extend(updates) or len(updates)
    old_dry = m.DRY_RUN
    m.DRY_RUN = dry_run
    try:
        m._sync_line_items(_compute)
    finally:
        m.DRY_RUN = old_dry
    return captured


US, CA, TOTAL = (m.hsli.HS_LI_INVENTORY_US_PROP, m.hsli.HS_LI_INVENTORY_CA_PROP,
                 m.hsli.HS_LI_INVENTORY_TOTAL_PROP)

# Case 1: a line item whose SKU is a -V variant rolls up to the base family, and gets
# ALL THREE numbers written (never a region choice).
got = run_line_items([{"id": "li1", "sku": "11-01-00-50-V6",
                       "inventory_us": None, "inventory_ca": None, "inventory_total": None}])
check("versioned SKU rolls up to base family", len(got) == 1 and got[0]["id"] == "li1")
check("all three numbers written",
      got and got[0]["properties"].get(US) == 7
      and got[0]["properties"].get(CA) == 3
      and got[0]["properties"].get(TOTAL) == 10)

# Case 2: unchanged line item (HubSpot already has the exact numbers) is skipped.
got = run_line_items([{"id": "li2", "sku": "11-01-00-50",
                       "inventory_us": "7", "inventory_ca": "3", "inventory_total": "10"}])
check("unchanged line item is not rewritten", got == [])

# Case 3: a changed line item (one value stale) IS rewritten with all three.
got = run_line_items([{"id": "li3", "sku": "11-01-00-50",
                       "inventory_us": "7", "inventory_ca": "0", "inventory_total": "7"}])
check("stale line item rewritten", len(got) == 1 and got[0]["id"] == "li3"
      and got[0]["properties"].get(CA) == 3 and got[0]["properties"].get(TOTAL) == 10)

# Case 4: SKU with no Pipe17 inventory record is left untouched.
got = run_line_items([{"id": "li4", "sku": "99-99-99-99",
                       "inventory_us": None, "inventory_ca": None, "inventory_total": None}])
check("unknown SKU not written", got == [])

# Case 5: a line item with no SKU is skipped, not errored.
got = run_line_items([{"id": "li5", "sku": None,
                       "inventory_us": None, "inventory_ca": None, "inventory_total": None}])
check("no-SKU line item skipped", got == [])

# Case 6: DRY_RUN writes nothing (batch_update never called).
dry_called = {"n": 0}
m.hsli.iter_active_deal_line_items = lambda: iter(
    [{"id": "li6", "sku": "11-01-00-50", "inventory_us": None,
      "inventory_ca": None, "inventory_total": None}])
m.hsli.batch_update = lambda updates: dry_called.__setitem__("n", dry_called["n"] + 1)
_dry = m.DRY_RUN
m.DRY_RUN = True
try:
    m._sync_line_items(_compute)
finally:
    m.DRY_RUN = _dry
check("DRY_RUN writes nothing", dry_called["n"] == 0)

# Case 7: drift guard — product and line item resolve to the SAME numbers for the
# same SKU, because both go through the one memoized compute.
pr = _compute("11-01-00-50")
li_props = run_line_items([{"id": "li7", "sku": "11-01-00-50-V9",
                            "inventory_us": None, "inventory_ca": None,
                            "inventory_total": None}])[0]["properties"]
check("product and line item agree on the numbers",
      li_props[US] == pr["inventory_us"] and li_props[CA] == pr["inventory_ca"]
      and li_props[TOTAL] == pr["inventory_total"])

# ---- line-item ID gather (dedup across deals) -----------------------------
import hubspot_lineitems as hsli

calls = {"assoc": []}


def _fake_request(method, path, **kwargs):
    if path.endswith("/deals/search"):
        return {"results": [{"id": "d1"}, {"id": "d2"}], "paging": {}}
    if "associations/deals/line_items/batch/read" in path:
        calls["assoc"].append(kwargs.get("json"))
        # d1 and d2 both point at the SAME line item li-shared, plus their own.
        return {"results": [
            {"from": {"id": "d1"}, "to": [{"toObjectId": "liA"}, {"toObjectId": "liShared"}]},
            {"from": {"id": "d2"}, "to": [{"toObjectId": "liShared"}, {"toObjectId": "liB"}]},
        ]}
    raise AssertionError("unexpected path " + path)


hsli._request = _fake_request
ids = hsli._line_item_ids_for_deals(["d1", "d2"])
check("line-item ids deduped across deals", sorted(ids) == ["liA", "liB", "liShared"])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
