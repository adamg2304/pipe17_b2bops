"""Pipe17 REST client: incremental, paginated, tag-filtered reads with backoff."""
import time
import requests
from config import (
    PIPE17_API_BASE, PIPE17_API_KEY, PIPE17_AUTH_HEADER, PIPE17_SINCE_PARAM,
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


def _iter(path, list_key, updated_since_iso, page_size=100, extra_params=None):
    skip = 0
    while True:
        params = {PIPE17_SINCE_PARAM: updated_since_iso, "count": page_size,
                  "skip": skip, "keys": "*"}
        if extra_params:
            params.update(extra_params)
        data = _get(path, params=params)
        items = data.get(list_key, [])
        for item in items:
            yield item
        page = data.get("pagination") or {}
        if page.get("last") is True or len(items) < page_size:
            break
        skip += page_size


def iter_shipping_requests(updated_since_iso, tag=None, page_size=100):
    extra = {"tags": tag} if tag else None
    yield from _iter(PIPE17_SHIPMENTS_PATH, "shipments", updated_since_iso, page_size, extra)


def iter_orders(updated_since_iso, tag=None, statuses=None, page_size=100):
    """Yield tagged orders in the window. Each status is queried on its OWN request:
    passing several as one `status` list makes requests send a repeated query param
    (status=a&status=b), and Pipe17 honors only one of them — which silently dropped
    every readyForFulfillment order once sentToFulfillment was added alongside it.
    One request per status (a single value) is the behavior Pipe17 actually honors.
    Results are de-duplicated in case an order changes status between the calls."""
    base = {}
    if tag:
        base["tags"] = tag
    status_values = list(statuses) if statuses else [None]
    seen = set()
    for status in status_values:
        extra = dict(base)
        if status:
            extra["status"] = status
        for order in _iter(PIPE17_ORDERS_PATH, "orders", updated_since_iso, page_size,
                           extra or None):
            key = order.get("orderId") or order.get("id") or order.get("extOrderId")
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            yield order


def get_customer(customer_id):
    if not customer_id:
        return {}
    data = _get(f"/customers/{customer_id}")
    return data.get("customer", data) or {}
