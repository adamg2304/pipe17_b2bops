# Track B additions to config.py and pipe17_client.py

Your `config.py` and `pipe17_client.py` on disk are the source of truth (they hold your
Track A code + edits). This file just documents the Track B pieces that must be present in
them, so you can verify after committing. Don't overwrite your disk files with anything here.

## config.py — Track B block (appended after the Track A section)
```python
# ===========================================================================
# Track B  (HubSpot deposit-paid deals -> Pipe17 draft sales orders)
# ===========================================================================
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() == "true"

HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
HUBSPOT_API_BASE = os.environ.get("HUBSPOT_API_BASE", "https://api.hubapi.com")
HS_LOOKBACK_MINUTES = int(os.environ.get("HS_LOOKBACK_MINUTES", "60"))
HS_TRIGGER_MODE = os.environ.get("HS_TRIGGER_MODE", "datetime_property")
HS_DEPOSIT_PAID_DATE_PROP = os.environ.get("HS_DEPOSIT_PAID_DATE_PROP", "deposit_paid_date")
HS_DEPOSIT_PAID_STAGE_ID = os.environ.get("HS_DEPOSIT_PAID_STAGE_ID", "")
HS_DEAL_PIPELINE_ID = os.environ.get("HS_DEAL_PIPELINE_ID", "")
HS_PIPE17_ORDER_ID_PROP = os.environ.get("HS_PIPE17_ORDER_ID_PROP", "pipe17_order_id")
HS_ORDER_NUMBER_PROP = os.environ.get("HS_ORDER_NUMBER_PROP", "order_number")
HS_CURRENCY_PROP = os.environ.get("HS_CURRENCY_PROP", "deal_currency_code")
HS_TAX_TOTAL_PROP = os.environ.get("HS_TAX_TOTAL_PROP", "committed_tax_total")
HS_SHIP_ADDR_PROPS = {
    "name":    os.environ.get("HS_SHIP_NAME_PROP", "shipping_name"),
    "address": os.environ.get("HS_SHIP_ADDR_PROP", "shipping_address"),
    "city":    os.environ.get("HS_SHIP_CITY_PROP", "shipping_city"),
    "state":   os.environ.get("HS_SHIP_STATE_PROP", "shipping_state"),
    "zip":     os.environ.get("HS_SHIP_ZIP_PROP", "shipping_zip"),
    "country": os.environ.get("HS_SHIP_COUNTRY_PROP", "shipping_country"),
    "email":   os.environ.get("HS_SHIP_EMAIL_PROP", "shipping_email"),
}
HS_LI_PROPS = {
    "sku":      os.environ.get("HS_LI_SKU_PROP", "hs_sku"),
    "name":     os.environ.get("HS_LI_NAME_PROP", "name"),
    "quantity": os.environ.get("HS_LI_QTY_PROP", "quantity"),
    "price":    os.environ.get("HS_LI_PRICE_PROP", "price"),
}
CURRENCY_CHANNEL_MAP = {  # legacy/no longer used for routing (key selects the channel)
    "USD": os.environ.get("PIPE17_CHANNEL_US", ""),
    "CAD": os.environ.get("PIPE17_CHANNEL_CA", ""),
}
PIPE17_DRAFT_STATUS = os.environ.get("PIPE17_DRAFT_STATUS", "draft")
PIPE17_AIRTABLE_TAG = os.environ.get("PIPE17_AIRTABLE_TAG", "Airtable")

# --- Branch-specific overrides (last definition wins) ---
HS_TRIGGER_MODE = "stage"
HS_DEPOSIT_PAID_STAGE_ID = os.environ.get("HS_DEPOSIT_PAID_STAGE_ID", "10492961")
HS_SHIP_ADDR_PROPS = {
    "address":  "delivery_address_1",
    "address2": "delivery_address_2",
    "city":     "delivery_city",
    "state":    "delivery_state_province",
    "zip":      "delivery_zip_postal_code",
}

# two-key routing (the API key selects the US/CA channel), order-create knobs
PIPE17_API_KEY_US = os.environ.get("PIPE17_API_KEY_US", os.environ.get("PIPE17_API_KEY", ""))
PIPE17_API_KEY_CA = os.environ.get("PIPE17_API_KEY_CA", os.environ.get("PIPE17_API_KEY", ""))
CURRENCY_API_KEY_MAP = {"USD": PIPE17_API_KEY_US, "CAD": PIPE17_API_KEY_CA}
PIPE17_ORDER_SOURCE = os.environ.get("PIPE17_ORDER_SOURCE", "hubspot")

SERVICE_SKUS = {
    "White Glove Delivery", "Free Shipping",
    "Union Labor Surcharge", "Stair Carry Surcharge", "Expedited Shipping",
    "After Hours Surcharge", "Freight Shipping",
}
PIPE17_ORDER_PREFIX_MAP = {
    "USD": os.environ.get("PIPE17_PREFIX_US", "#BE"),
    "CAD": os.environ.get("PIPE17_PREFIX_CA", "#CEN"),
}
HS_DELIVERY_CONTACT_LABEL = os.environ.get("HS_DELIVERY_CONTACT_LABEL", "End User - Delivery Contact")
```

Also confirm the shared Pipe17 transport at the top of config.py is:
```python
PIPE17_API_BASE   = os.environ.get("PIPE17_API_BASE", "https://api-v3.pipe17.com/api/v3")
PIPE17_AUTH_HEADER = os.environ.get("PIPE17_AUTH_HEADER", "X-Pipe17-Key")
PIPE17_ORDERS_PATH = os.environ.get("PIPE17_ORDERS_PATH", "/orders")
```

## pipe17_client.py — Track B append (write helpers used by Track A repo's read client)
Note: Track B actually uses `pipe17_write.py` for writes; these three functions were
appended to pipe17_client.py earlier and are harmless if present. Safe to leave.
```python
def _post(path, body, max_retries=4):
    url = f"{PIPE17_API_BASE}{path}"
    last = None
    for attempt in range(max_retries):
        last = requests.post(url, headers=HEADERS, json=body, timeout=30)
        if last.status_code == 429 or last.status_code >= 500:
            time.sleep(2 ** attempt); continue
        last.raise_for_status()
        return last.json() if last.content else {}
    last.raise_for_status()

def get_order_by_ext_id(ext_order_id):
    if not ext_order_id:
        return None
    data = _get(PIPE17_ORDERS_PATH, params={"extOrderId": ext_order_id, "count": 1})
    orders = data.get("orders", [])
    return orders[0] if orders else None

def create_order(order_body):
    resp = _post(PIPE17_ORDERS_PATH, order_body)
    return resp.get("order", resp)
```
