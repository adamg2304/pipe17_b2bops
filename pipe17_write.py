"""Track B write path: create Pipe17 orders with the currency-appropriate API key.

Kept separate from pipe17_client.py (Track A read path). Pipe17's order-create has NO
channelId field — the API KEY selects the channel/org (US vs CA), which is why there are
two keys. Auth header is X-Pipe17-Key (confirmed).
"""
import time
import requests

from config import (
    PIPE17_API_BASE, PIPE17_ORDERS_PATH,
    PIPE17_ORDER_UPDATE_PATH, PIPE17_ORDER_CANCEL_PATH,
)


def _headers(api_key):
    return {"X-Pipe17-Key": api_key, "Accept": "application/json",
            "Content-Type": "application/json"}


def _request(method, path, api_key, params=None, body=None, max_retries=4):
    url = f"{PIPE17_API_BASE}{path}"
    last = None
    for attempt in range(max_retries):
        last = requests.request(method, url, headers=_headers(api_key),
                                params=params, json=body, timeout=30)
        if last.status_code == 429 or last.status_code >= 500:
            time.sleep(2 ** attempt); continue
        last.raise_for_status()
        return last.json() if last.content else {}
    last.raise_for_status()


def get_order_by_ext_id(ext_order_id, api_key):
    if not ext_order_id:
        return None
    data = _request("GET", PIPE17_ORDERS_PATH, api_key,
                    params={"extOrderId": ext_order_id, "count": 1})
    orders = data.get("orders", [])
    return orders[0] if orders else None


def create_order(order_body, api_key):
    resp = _request("POST", PIPE17_ORDERS_PATH, api_key, body=order_body)
    return resp.get("order", resp)


def order_id_of(order):
    """The Pipe17 internal order id from a get/create response."""
    return order.get("orderId") or order.get("id") if order else None


def update_order(order_id, order_body, api_key):
    """Re-sync a held order's line items / tags while it sits at Ready-for-Fulfillment
    (before its shipping requests are released). Pipe17 allows editing in that window.
    PATCH path is env-overridable; verify against Pipe17 before live use."""
    path = PIPE17_ORDER_UPDATE_PATH.format(id=order_id)
    resp = _request("PATCH", path, api_key, body=order_body)
    return resp.get("order", resp)


def cancel_order(order_id, api_key):
    """Cancel a held order to release the committed inventory (deal lost / hold
    withdrawn before Ready for Ops). Path is env-overridable; verify before live use."""
    resp = _request("POST", PIPE17_ORDER_CANCEL_PATH.format(id=order_id), api_key)
    return resp.get("order", resp)
