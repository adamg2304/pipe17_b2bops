"""Pipe17 REST client: incremental, paginated reads with retry/backoff.

Field names (extShipmentId, extOrderId, orderId, locationId, status, lineItems, ...)
are confirmed against the live shipping-request payload. VERIFY only the transport
details against your API creds: the auth header (PIPE17_AUTH_HEADER) and that
shipping requests live at PIPE17_SHIPMENTS_PATH ("/shipments").
"""
import time
import requests

from config import (
    PIPE17_API_BASE, PIPE17_API_KEY, PIPE17_AUTH_HEADER,
    PIPE17_SHIPMENTS_PATH, PIPE17_ORDERS_PATH,
)

HEADERS = {PIPE17_AUTH_HEADER: PIPE17_API_KEY, "Accept": "application/json"}


def _get(path, params=None, max_retries=4):
    url = f"{PIPE17_API_BASE}{path}"
    last = None
    for attempt in range(max_retries):
        last = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if last.status_code == 429 or last.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        last.raise_for_status()
        return last.json()
    last.raise_for_status()


def _iter(path, list_key, updated_since_iso, page_size=100):
    skip = 0
    while True:
        data = _get(path, params={
            "updatedSince": updated_since_iso,
            "count": page_size,
            "skip": skip,
            "keys": "*",
        })
        items = data.get(list_key, [])
        for item in items:
            yield item
        page = data.get("pagination") or {}
        if page.get("last") is True or len(items) < page_size:
            break
        skip += page_size


def iter_shipping_requests(updated_since_iso, page_size=100):
    """Yield Pipe17 shipping requests (a.k.a. shipments) updated since a timestamp.
    One order fans out into N of these; each carries extShipmentId + parent extOrderId.
    """
    yield from _iter(PIPE17_SHIPMENTS_PATH, "shipments", updated_since_iso, page_size)


def iter_orders(updated_since_iso, page_size=100):
    yield from _iter(PIPE17_ORDERS_PATH, "orders", updated_since_iso, page_size)
