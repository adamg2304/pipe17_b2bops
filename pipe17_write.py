"""Track B write path: create Pipe17 orders with the currency-appropriate API key.

Kept separate from pipe17_client.py (Track A read path). Pipe17's order-create has NO
channelId field — the API KEY selects the channel/org (US vs CA), which is why there are
two keys. Auth header is X-Pipe17-Key (confirmed).
"""
import time
import requests

from config import (
    PIPE17_API_BASE, PIPE17_ORDERS_PATH, PIPE17_SHIPMENTS_PATH,
    PIPE17_ORDER_UPDATE_PATH, PIPE17_CANCELED_STATUS,
    PIPE17_ORDER_SOURCE, HS_DEAL_ID_CUSTOM_FIELD,
)


def _headers(api_key):
    return {"X-Pipe17-Key": api_key, "Accept": "application/json",
            "Content-Type": "application/json"}


def _raise_with_body(resp):
    """raise_for_status, but include Pipe17's response body in the error so a 4xx
    validation rejection says WHICH field it rejected (plain raise_for_status hides it)."""
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        detail = (resp.text or "").strip()[:1500]
        raise requests.HTTPError(f"{e} | pipe17 response: {detail}", response=resp) from None


def _request(method, path, api_key, params=None, body=None, max_retries=4):
    url = f"{PIPE17_API_BASE}{path}"
    last = None
    for attempt in range(max_retries):
        last = requests.request(method, url, headers=_headers(api_key),
                                params=params, json=body, timeout=30)
        if last.status_code == 429 or last.status_code >= 500:
            time.sleep(2 ** attempt); continue
        _raise_with_body(last)
        return last.json() if last.content else {}
    _raise_with_body(last)


def get_order_by_ext_id(ext_order_id, api_key):
    """Look an order up by its external id. keys=* is required so the response
    carries lineItems and tags (the plain list view omits them) — the hold-sync
    reconcile compares both against the desired order."""
    if not ext_order_id:
        return None
    data = _request("GET", PIPE17_ORDERS_PATH, api_key,
                    params={"extOrderId": ext_order_id, "count": 1, "keys": "*"})
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
    Pipe17 v3 update is PUT /api/v3/orders/{orderId} (confirmed against Pipe17's own
    Flexport integration guide, which PUTs to that path to set status)."""
    path = PIPE17_ORDER_UPDATE_PATH.format(id=order_id)
    resp = _request("PUT", path, api_key, body=order_body)
    return resp.get("order", resp)


def cancel_order(order_id, api_key):
    """Cancel a held order to release the committed inventory (deal lost / hold
    withdrawn before Ready for Ops). Pipe17 v3 has no dedicated cancel endpoint — the
    same PUT /orders/{id} sets status=canceled. Safe only pre-release (hold_sync guards
    on shipping-request status / the hold tag); a released order's 3PL shipment is not
    recalled by a status flip."""
    path = PIPE17_ORDER_UPDATE_PATH.format(id=order_id)
    resp = _request("PUT", path, api_key, body={"status": PIPE17_CANCELED_STATUS})
    return resp.get("order", resp)


def get_order_shipments(ext_order_id, api_key):
    """Every shipping request for one order (by external id), read with the order's own
    channel key. Used by the closed-lost sweep to decide whether anything is in flight
    before cancelling. Returns [] when the order has no SRs."""
    if not ext_order_id:
        return []
    data = _request("GET", PIPE17_SHIPMENTS_PATH, api_key,
                    params={"extOrderId": ext_order_id, "count": 100, "keys": "*"})
    return data.get("shipments", []) or []


def _deal_id_of(order):
    for c in (order.get("customFields") or []):
        if c.get("name") == HS_DEAL_ID_CUSTOM_FIELD:
            return c.get("value")
    return None


def find_orders_for_deal(deal_id, prefix, api_key, max_scan=1000):
    """All Pipe17 orders for a HubSpot deal: the base order (extOrderId = <prefix><dealId>)
    plus any manual replacement that reuses that extOrderId with a suffix or carries the
    same hubspot_deal_id custom field.

    Pipe17's /orders filter matches extOrderId exactly, so the base is fetched exactly and
    replacements are found by paging recent hubspot-source orders (newest first) and
    prefix / custom-field matching, bounded by max_scan. The base order is always found via
    the exact fetch regardless of age; only a replacement older than max_scan orders back
    could be missed (rare, and the feature is gated for careful rollout)."""
    base_ext = f"{prefix}{deal_id}"
    found = {}
    base = get_order_by_ext_id(base_ext, api_key)
    if base:
        found[order_id_of(base) or base_ext] = base
    scanned = 0
    skip = 0
    while scanned < max_scan:
        params = {"orderSource": PIPE17_ORDER_SOURCE, "count": 100, "skip": skip,
                  "order": "createdAt:desc", "keys": "orderId,extOrderId,status,customFields"}
        data = _request("GET", PIPE17_ORDERS_PATH, api_key, params=params)
        orders = data.get("orders", []) or []
        for o in orders:
            o_ext = str(o.get("extOrderId") or "")
            did = _deal_id_of(o)
            if (o_ext != base_ext and o_ext.startswith(base_ext)) or (did and str(did) == str(deal_id)):
                found[order_id_of(o) or o_ext] = o
        scanned += len(orders)
        info = data.get("pagination") or {}
        if info.get("last") is True or len(orders) < 100:
            break
        skip += 100
    return list(found.values())
