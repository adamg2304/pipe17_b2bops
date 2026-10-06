"""Resolve a base (HubSpot) SKU to the correct Pipe17 product SKU for a channel.

Pipe17 matches order lines by EXACT product SKU. Branch products are versioned
(e.g. "11-01-00-40-V6"); the bare base SKU exists only as the per-channel Channel SKU
alias on the current-version product (published[].sku keyed by integrationId). Pipe17
does not auto-apply that alias for custom API integrations, so we read the catalog and
translate each line's SKU before order-create.

Resolution per line (for the order's currency/channel):
  1. exact product SKU match -> use as-is   (non-versioned items, e.g. 13-11-00-00)
  2. channel alias match     -> product whose published[sku]==base for this channel
  3. neither                 -> return base unchanged, status "unresolved"
"""
import logging
import time
import requests

from config import (
    PIPE17_API_BASE, PIPE17_AUTH_HEADER, PIPE17_API_KEY_US, PIPE17_API_KEY_CA,
    PIPE17_CATALOG_API_KEY, CURRENCY_CHANNEL_INTEGRATION,
)

log = logging.getLogger("pipe17-catalog")

_PRODUCTS_PATH = "/products"
_PAGE = 250


def _api_key(currency):
    # The product catalog is org-wide. The B2B order-channel keys are not authorized
    # for /products (403), so prefer a dedicated catalog-scoped key when one is
    # configured; otherwise fall back to the channel key (and fail open — see below).
    if PIPE17_CATALOG_API_KEY:
        return PIPE17_CATALOG_API_KEY
    return PIPE17_API_KEY_CA if (currency or "").upper() == "CAD" else PIPE17_API_KEY_US


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


def _iter_products(api_key):
    skip = 0
    while True:
        data = _get(_PRODUCTS_PATH, api_key, params={"count": _PAGE, "skip": skip, "keys": "*"})
        items = data.get("products", [])
        for p in items:
            yield p
        page = data.get("pagination") or {}
        if page.get("last") is True or len(items) < _PAGE:
            break
        skip += _PAGE


class SkuResolver:
    def __init__(self, currency):
        self.currency = (currency or "USD").upper()
        self.integration_id = CURRENCY_CHANNEL_INTEGRATION.get(self.currency)
        self._product_skus = set()
        self._alias = {}
        self.collisions = {}
        # Fail open: if the catalog can't be read (e.g. the channel key is not
        # authorized for /products -> 403), don't take the order flow down. Leave
        # the maps empty and pass SKUs through unchanged until a catalog-scoped key
        # (PIPE17_CATALOG_API_KEY) is configured. Orders then go out with the base
        # SKU, as they did before SKU resolution existed.
        self.catalog_unavailable = False
        try:
            for p in _iter_products(_api_key(self.currency)):
                sku = p.get("sku")
                if sku:
                    self._product_skus.add(sku)
                for pub in p.get("published") or []:
                    if pub.get("integrationId") != self.integration_id:
                        continue
                    alias = pub.get("sku")
                    if not alias:
                        continue
                    if alias in self._alias and self._alias[alias] != sku:
                        self.collisions.setdefault(alias, {self._alias[alias]}).add(sku)
                    self._alias[alias] = sku
        except Exception as e:
            self.catalog_unavailable = True
            log.warning("Pipe17 catalog read failed on %s channel (%s) — sending SKUs "
                        "through unresolved. Set PIPE17_CATALOG_API_KEY to a key with "
                        "/products read scope to enable versioned resolution.",
                        self.currency, e)

    def resolve(self, base_sku):
        if not base_sku:
            return base_sku, "unresolved"
        if self.catalog_unavailable:
            return base_sku, "passthrough"
        if base_sku in self._product_skus:
            return base_sku, "exact"
        if base_sku in self._alias:
            return self._alias[base_sku], "alias"
        return base_sku, "unresolved"


_CACHE = {}


def get_resolver(currency):
    cur = (currency or "USD").upper()
    if cur not in _CACHE:
        _CACHE[cur] = SkuResolver(cur)
    return _CACHE[cur]
