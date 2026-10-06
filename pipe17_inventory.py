"""Read Pipe17 availability and aggregate it to US / CA per base SKU.

Read-only. Pipe17 tracks inventory per SKU per location; the `available` field
already nets out committed, so we read it directly (no recompute). Locations are
classified US/CA by their address.country (read live from /locations), so a new
warehouse needs no code change.

Per-channel version mapping (decided with Adam): the US number reads the product
the US channel aliases the base SKU to, at US locations; the CA number reads the
CA-aliased product at CA locations. A base SKU with no versioning resolves to
itself on both channels. See pipe17_catalog for the alias resolution.
"""
import time
import requests

from config import (
    PIPE17_API_BASE, PIPE17_AUTH_HEADER,
    PIPE17_INVENTORY_PATH, PIPE17_LOCATIONS_PATH,
)

_PAGE = 500


def _get(path, api_key, params=None, max_retries=4):
    url = f"{PIPE17_API_BASE}{path}"
    headers = {PIPE17_AUTH_HEADER: api_key, "Accept": "application/json"}
    last = None
    for attempt in range(max_retries):
        last = requests.get(url, headers=headers, params=params, timeout=30)
        if last.status_code == 429 or last.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        last.raise_for_status()
        return last.json()
    last.raise_for_status()


def _iter(path, list_key, api_key, params=None):
    skip = 0
    while True:
        p = {"count": _PAGE, "skip": skip, "keys": "*"}
        if params:
            p.update(params)
        data = _get(path, api_key, params=p)
        items = data.get(list_key, [])
        for it in items:
            yield it
        page = data.get("pagination") or {}
        if page.get("last") is True or len(items) < _PAGE:
            break
        skip += _PAGE


def location_country_map(api_key):
    """{locationId: country} from /locations, country taken from address.country."""
    out = {}
    for loc in _iter(PIPE17_LOCATIONS_PATH, "locations", api_key):
        loc_id = loc.get("locationId")
        country = ((loc.get("address") or {}).get("country") or "").upper()
        if loc_id:
            out[loc_id] = country
    return out


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def inventory_index(api_key):
    """{sku: {locationId: available}} for the whole catalog, one paginated scan.
    `available` already accounts for committed stock, so it is used as-is."""
    idx = {}
    for row in _iter(PIPE17_INVENTORY_PATH, "inventory", api_key):
        sku = row.get("sku")
        loc = row.get("locationId")
        if not sku or not loc:
            continue
        idx.setdefault(sku, {})[loc] = _as_int(row.get("available"))
    return idx


# --- pure aggregation (unit-tested offline) --------------------------------

def regional_available(sku, inv_index, country_map, country):
    """Sum `available` for one product SKU across every location in `country`."""
    total = 0
    for loc_id, avail in (inv_index.get(sku) or {}).items():
        if country_map.get(loc_id) == country:
            total += avail or 0
    return total


def compute_inventory(base_sku, resolver_us, resolver_ca, inv_index, country_map):
    """Resolve the base SKU per channel and aggregate availability by region.

    Returns a dict with the resolved SKUs, their resolution status, the US/CA/total
    numbers, and `matched` (False only when neither channel knows the base SKU, i.e.
    there is no Pipe17 product behind it).
    """
    us_sku, us_status = resolver_us.resolve(base_sku)
    ca_sku, ca_status = resolver_ca.resolve(base_sku)
    inv_us = regional_available(us_sku, inv_index, country_map, "US")
    inv_ca = regional_available(ca_sku, inv_index, country_map, "CA")
    return {
        "base_sku": base_sku,
        "us_sku": us_sku, "us_status": us_status,
        "ca_sku": ca_sku, "ca_status": ca_status,
        "inventory_us": inv_us,
        "inventory_ca": inv_ca,
        "inventory_total": inv_us + inv_ca,
        "matched": us_status != "unresolved" or ca_status != "unresolved",
    }
