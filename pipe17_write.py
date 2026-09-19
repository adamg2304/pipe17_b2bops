"""Track B write path: create Pipe17 orders with the currency-appropriate API key.

Kept separate from pipe17_client.py (Track A read path). Pipe17's order-create has NO
channelId field — the API KEY selects the channel/org (US vs CA), which is why there are
two keys. Auth header is X-Pipe17-Key (confirmed).
"""
import time
import requests

from config import PIPE17_API_BASE, PIPE17_ORDERS_PATH


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
