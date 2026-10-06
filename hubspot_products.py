"""HubSpot product-library client: list products by SKU, batch-patch inventory.

Writes only the three inventory properties on the product object. inventory_us /
inventory_ca / inventory_total are all number properties.
"""
import time
import requests

from config import (
    HUBSPOT_TOKEN, HUBSPOT_API_BASE, HS_PRODUCT_OBJECT, HS_PRODUCT_SKU_PROP,
    HS_INVENTORY_US_PROP, HS_INVENTORY_CA_PROP, HS_INVENTORY_TOTAL_PROP,
)

HEADERS = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
_BASE = f"/crm/v3/objects/{HS_PRODUCT_OBJECT}"


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


_READ_PROPS = [HS_PRODUCT_SKU_PROP, HS_INVENTORY_US_PROP,
               HS_INVENTORY_CA_PROP, HS_INVENTORY_TOTAL_PROP]


def iter_products():
    """Yield {id, sku, inventory_us, inventory_ca, inventory_total} for every product.

    The inventory values are the CURRENT HubSpot values (strings as HubSpot returns
    them), so the caller can skip no-op writes.
    """
    after = None
    while True:
        params = {"limit": 100, "properties": ",".join(_READ_PROPS), "archived": "false"}
        if after:
            params["after"] = after
        data = _request("GET", _BASE, params=params)
        for row in data.get("results", []):
            p = row.get("properties", {})
            yield {
                "id": row.get("id"),
                "sku": p.get(HS_PRODUCT_SKU_PROP),
                "inventory_us": p.get(HS_INVENTORY_US_PROP),
                "inventory_ca": p.get(HS_INVENTORY_CA_PROP),
                "inventory_total": p.get(HS_INVENTORY_TOTAL_PROP),
            }
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(0.15)


def inventory_properties(inv_us, inv_ca, inv_total):
    """The property payload for a product (all three are HubSpot number properties)."""
    return {
        HS_INVENTORY_US_PROP: inv_us,
        HS_INVENTORY_CA_PROP: inv_ca,
        HS_INVENTORY_TOTAL_PROP: inv_total,
    }


def batch_update(updates):
    """updates: [{"id": str, "properties": {...}}]. Chunks of 100. Returns count written."""
    written = 0
    for i in range(0, len(updates), 100):
        chunk = updates[i:i + 100]
        _request("POST", f"{_BASE}/batch/update", json={"inputs": chunk})
        written += len(chunk)
        if i + 100 < len(updates):
            time.sleep(0.15)
    return written
