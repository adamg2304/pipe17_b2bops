"""Read Pipe17 availability and aggregate it to US / CA per base SKU.

Read-only. Pipe17 tracks inventory per SKU per location; the `available` field
already nets out committed, so we read it directly (no recompute).

A base SKU (e.g. 11-01-00-50) can exist in Pipe17 as several products that share
the stem: the bare base plus versioned variants (11-01-00-50-V5, -V6, ...). All of
them hold sellable stock, so availability sums across the whole family. Locations
are classified US/CA by their address.country (read live from /locations), so the
split is purely by warehouse and a new warehouse needs no code change. The
per-channel Channel SKU alias is NOT used here — that only governs which version an
order draws, not how much stock exists.

  inventory_us    = sum(available) over every stem-matching SKU at US locations
  inventory_ca    = sum(available) over every stem-matching SKU at CA locations
  inventory_total = inventory_us + inventory_ca            (MX/BR excluded)
"""
import re
import time
import requests

from config import (
    PIPE17_API_BASE, PIPE17_AUTH_HEADER,
    PIPE17_INVENTORY_PATH, PIPE17_LOCATIONS_PATH,
)

_PAGE = 500
_VERSION_RE = re.compile(r"-V\d+$")


def base_stem(sku):
    """Strip a trailing -V<n> version suffix: 11-01-00-50-V6 -> 11-01-00-50.
    A SKU with no version suffix (bare base, or a non-versioned product) is
    returned unchanged."""
    return _VERSION_RE.sub("", sku) if sku else sku


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


def _index_rows(rows):
    """Group raw inventory rows into {base_stem: {locationId: summed_available}},
    rolling every version of a base together per location. `available` already
    accounts for committed stock, so it is used as-is."""
    out = {}
    for row in rows:
        sku = row.get("sku")
        loc = row.get("locationId")
        if not sku or not loc:
            continue
        base = base_stem(sku)
        locs = out.setdefault(base, {})
        locs[loc] = locs.get(loc, 0) + _as_int(row.get("available"))
    return out


def inventory_by_base(api_key):
    """{base_stem: {locationId: available}} for the whole catalog, one paginated scan."""
    return _index_rows(_iter(PIPE17_INVENTORY_PATH, "inventory", api_key))


# --- pure aggregation (unit-tested offline) --------------------------------

def regional_available(base_locs, country_map, country):
    """Sum availability for one base across every location in `country`."""
    total = 0
    for loc_id, avail in (base_locs or {}).items():
        if country_map.get(loc_id) == country:
            total += avail or 0
    return total


def compute_inventory(base_sku, inv_by_base, country_map):
    """Aggregate a base SKU's availability (all versions) by region.

    Returns the US/CA/total numbers and `matched` (False only when Pipe17 has no
    inventory record for any SKU in the base's family — i.e. no product behind it).
    """
    base_locs = inv_by_base.get(base_sku)
    inv_us = regional_available(base_locs, country_map, "US")
    inv_ca = regional_available(base_locs, country_map, "CA")
    return {
        "base_sku": base_sku,
        "inventory_us": inv_us,
        "inventory_ca": inv_ca,
        "inventory_total": inv_us + inv_ca,
        "matched": base_locs is not None,
    }
