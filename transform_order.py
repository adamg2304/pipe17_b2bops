"""Map a HubSpot deal (+ line items) -> a Pipe17 create-order payload.

Pricing: itemPrice = NET unit price (line net amount / qty), not list price.
Payment: subTotalPrice = deal amount (net); totalPrice = subtotal + tax;
orderTax = frozen tax from the deal (deferred — Shopify tax via Alex — so 0/omitted now).
extOrderId = <currency prefix><deal id>. No channelId — the API key selects US/CA channel.
"""
import datetime as dt

from config import (
    PIPE17_DRAFT_STATUS, PIPE17_AIRTABLE_TAG, PIPE17_ORDER_SOURCE,
    PIPE17_ORDER_PREFIX_MAP, SERVICE_SKUS,
    HS_CURRENCY_PROP, HS_SHIP_ADDR_PROPS, HS_TAX_TOTAL_PROP,
)

COUNTRY_BY_CURRENCY = {"USD": "US", "CAD": "CA"}


def _num(value):
    if value in (None, ""):
        return None
    try:
        f = float(value)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def currency_of(deal):
    return ((deal.get("properties") or {}).get(HS_CURRENCY_PROP) or "USD").upper()


def ext_order_id_of(deal, currency, suffix=""):
    prefix = PIPE17_ORDER_PREFIX_MAP.get(currency, "")
    return f"{prefix}{deal.get('id')}{suffix or ''}"


def _prop(props, key):
    name = HS_SHIP_ADDR_PROPS.get(key)
    return props.get(name) if name else None


def _ship_to(props, currency, contact=None):
    contact = contact or {}
    addr = {
        "firstName": contact.get("firstName"),
        "lastName": contact.get("lastName"),
        "company": props.get("dealname"),
        "address1": _prop(props, "address"),
        "address2": _prop(props, "address2"),
        "city": _prop(props, "city"),
        "stateOrProvince": _prop(props, "state"),
        "zipCodeOrPostalCode": _prop(props, "zip"),
        "country": _prop(props, "country") or COUNTRY_BY_CURRENCY.get(currency, ""),
        "email": contact.get("email") or _prop(props, "email"),
        "phone": contact.get("phone"),
    }
    return {k: v for k, v in addr.items() if v}


def _net_unit_price(li):
    """Net unit price = net line amount / quantity (falls back to list price)."""
    qty = _num(li.get("quantity")) or 1
    amount = _num(li.get("amount"))
    if amount is not None and qty:
        return round(amount / qty, 2)
    return _num(li.get("price"))


def _line_items(items):
    out = []
    for idx, li in enumerate(items or [], start=1):
        qty = _num(li.get("quantity"))
        sku = li.get("sku") or ""
        entry = {
            "sku": sku,
            "quantity": qty if qty is not None else 1,
            "name": li.get("name") or sku,
            "uniqueId": f"line-{idx}",
            "requiresShipping": sku not in SERVICE_SKUS,
        }
        net = _net_unit_price(li)
        if net is not None:
            entry["itemPrice"] = net
        out.append(entry)
    return out


def build_order(deal, line_items, delivery_contact=None, order_suffix=""):
    """Returns (order_body, currency, ext_order_id).

    order_suffix is appended to the minted extOrderId (default empty). Used for
    test iterations so a re-run of the same deal gets a fresh order number that
    doesn't collide with Pipe17's retained (cancelled) shipping-request keys.
    """
    props = deal.get("properties") or {}
    currency = currency_of(deal)
    ext_order_id = ext_order_id_of(deal, currency, order_suffix)

    body = {
        "extOrderId": ext_order_id,
        "status": PIPE17_DRAFT_STATUS,
        "currency": currency,
        "extOrderCreatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tags": [PIPE17_AIRTABLE_TAG],
        "shippingAddress": _ship_to(props, currency, delivery_contact),
        "lineItems": _line_items(line_items),
        "customFields": [{"name": "hubspot_deal_id", "value": str(deal.get("id"))}],
    }

    # Customer (the order's CRM customer, searchable by name/email in Pipe17) — from the
    # deal's delivery contact. Separate from shippingAddress; Pipe17 finds/creates it.
    if delivery_contact:
        customer = {
            "firstName": delivery_contact.get("firstName"),
            "lastName": delivery_contact.get("lastName"),
            "email": delivery_contact.get("email"),
            "phone": delivery_contact.get("phone"),
            "company": props.get("dealname"),
        }
        customer = {k: v for k, v in customer.items() if v}
        if customer:
            body["customer"] = customer

    # Payment: subtotal = sum of the line items' net totals (itemPrice * qty). The deal
    # "Amount" field is deliberately ignored — it's a manual field that's often blank; the
    # line items are the source of truth (matches HubSpot's TCV / Subtotal). Tax deferred
    # (Shopify via Alex) — total = subtotal + tax.
    subtotal = round(sum((li.get("itemPrice") or 0) * (li.get("quantity") or 0)
                         for li in body["lineItems"]), 2)
    tax = _num(props.get(HS_TAX_TOTAL_PROP))
    body["subTotalPrice"] = subtotal
    body["totalPrice"] = round(subtotal + (tax or 0), 2)
    if tax is not None:
        body["orderTax"] = tax

    if PIPE17_ORDER_SOURCE:
        body["orderSource"] = PIPE17_ORDER_SOURCE
    if not body["shippingAddress"]:
        del body["shippingAddress"]
    return body, currency, ext_order_id
