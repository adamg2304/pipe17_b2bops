"""HubSpot line-item client for the inventory fan-out: list line items on active
deals and batch-patch the three inventory properties onto them.

Reporting in HubSpot runs on line items, not products, so the inventory sync stamps
the same us/ca/total numbers onto every line item of an active (open) deal. This
module only gathers + writes line items; the numbers come from the product-side
computation in main_inventory_sync, so both sides are one value from one snapshot.

Writes only the three number properties on the line_item object. Never touches the
deal, the product, order quantities, price, or anything shown on a customer quote.
"""
import time
import requests

from config import (
    HUBSPOT_TOKEN, HUBSPOT_API_BASE,
    HS_LINE_ITEM_OBJECT, HS_LINE_ITEM_SKU_PROP,
    HS_LI_INVENTORY_US_PROP, HS_LI_INVENTORY_CA_PROP, HS_LI_INVENTORY_TOTAL_PROP,
    HS_ACTIVE_DEAL_STAGE_PROP, HS_ACTIVE_DEAL_STAGES,
)

HEADERS = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
_LI_BASE = f"/crm/v3/objects/{HS_LINE_ITEM_OBJECT}"

_READ_PROPS = [HS_LINE_ITEM_SKU_PROP, HS_LI_INVENTORY_US_PROP,
               HS_LI_INVENTORY_CA_PROP, HS_LI_INVENTORY_TOTAL_PROP]


def _request(method, path, **kwargs):
    url = f"{HUBSPOT_API_BASE}{path}"
    resp = None
    for attempt in range(4):
        resp = requests.request(method, url, headers=HEADERS, timeout=30, **kwargs)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    resp.raise_for_status()


def _active_deal_ids():
    """Deal ids for every deal in an active Sales Pipeline stage (HS_ACTIVE_DEAL_STAGES).

    Paginated CRM search. NOTE: HubSpot search caps at 10,000 results per query; with
    more active-stage deals than that it would need window/incremental paging. Flagged
    in the PR as a known limit — fine for current B2B deal volume.
    """
    after = None
    flt = [{"propertyName": HS_ACTIVE_DEAL_STAGE_PROP,
            "operator": "IN", "values": list(HS_ACTIVE_DEAL_STAGES)}]
    while True:
        body = {
            "filterGroups": [{"filters": flt}],
            "properties": ["hs_object_id"],
            "limit": 100,
            "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
        }
        if after:
            body["after"] = after
        data = _request("POST", "/crm/v3/objects/deals/search", json=body)
        for row in data.get("results", []):
            if row.get("id"):
                yield row["id"]
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(0.2)


def _line_item_ids_for_deals(deal_ids):
    """Line-item ids associated to the given deals, deduped. Batched v4 association
    reads, 100 deals per call."""
    out, seen = [], set()
    for i in range(0, len(deal_ids), 100):
        chunk = [{"id": str(d)} for d in deal_ids[i:i + 100] if d]
        if not chunk:
            continue
        data = _request("POST", "/crm/v4/associations/deals/line_items/batch/read",
                        json={"inputs": chunk})
        for res in data.get("results", []):
            for to in res.get("to", []):
                lid = to.get("toObjectId")
                if lid and lid not in seen:
                    seen.add(lid)
                    out.append(lid)
        time.sleep(0.15)
    return out


def _read_line_items(li_ids):
    """Yield {id, sku, inventory_us, inventory_ca, inventory_total} for each line item.
    Inventory values are the CURRENT HubSpot values (strings as returned), so the
    caller can skip no-op writes."""
    for i in range(0, len(li_ids), 100):
        chunk = [{"id": str(x)} for x in li_ids[i:i + 100]]
        if not chunk:
            continue
        data = _request("POST", f"{_LI_BASE}/batch/read",
                        json={"inputs": chunk, "properties": _READ_PROPS})
        for row in data.get("results", []):
            p = row.get("properties", {})
            yield {
                "id": row.get("id"),
                "sku": p.get(HS_LINE_ITEM_SKU_PROP),
                "inventory_us": p.get(HS_LI_INVENTORY_US_PROP),
                "inventory_ca": p.get(HS_LI_INVENTORY_CA_PROP),
                "inventory_total": p.get(HS_LI_INVENTORY_TOTAL_PROP),
            }
        time.sleep(0.15)


def iter_active_deal_line_items():
    """Every line item on an active deal, as {id, sku, inventory_*}. One seam the
    inventory sync drives; the deal -> line-item join lives entirely here."""
    deal_ids = list(_active_deal_ids())
    li_ids = _line_item_ids_for_deals(deal_ids)
    yield from _read_line_items(li_ids)


def inventory_properties(inv_us, inv_ca, inv_total):
    """The property payload for a line item (all three are HubSpot number properties)."""
    return {
        HS_LI_INVENTORY_US_PROP: inv_us,
        HS_LI_INVENTORY_CA_PROP: inv_ca,
        HS_LI_INVENTORY_TOTAL_PROP: inv_total,
    }


def batch_update(updates):
    """updates: [{"id": str, "properties": {...}}]. Chunks of 100. Returns count written."""
    written = 0
    for i in range(0, len(updates), 100):
        chunk = updates[i:i + 100]
        _request("POST", f"{_LI_BASE}/batch/update", json={"inputs": chunk})
        written += len(chunk)
        if i + 100 < len(updates):
            time.sleep(0.15)
    return written
