"""Map a Pipe17 order -> Airtable Orders field dict (field-ID keyed).

Mirrors the target fields of the existing HubSpot/Shopify -> Airtable Orders
connector, sourced from Pipe17 instead. Writes are keyed by Airtable field IDs
(see config.py, O_* constants).

CONTACT SOURCE (decision 2026-09-20): read customer name/email/phone from the order's
shippingAddress. Track B (HubSpot -> Pipe17) puts the delivery contact on shippingAddress,
and Pipe17 reliably retains name/email/phone there (unlike the top-level customer object,
which came back without email/phone). Company comes from shippingAddress.company (the deal
name). If shippingAddress lacks a field, fall back to the customer object when present.

NOT set here (filled by later steps):
  - Order Details / Order Attachments: the generated order-slip PDF (doc step).
  - Notes: composed downstream (QBO invoice no., etc.), not present on the Pipe17 order.
"""
import json

from config import (
    ORDER_STATUS_SEED,
    O_ORDER_NUMBER, O_ORDER_DATE, O_STATUS, O_CUSTOMER_NAME, O_COMPANY_NAME,
    O_DEAL_NAME, O_DEAL_VALUE, O_DELIVERY_ADDRESS, O_SUITE_NUMBER, O_CITY,
    O_STATE, O_ZIP_CODE, O_CUSTOMER_EMAIL, O_CUSTOMER_PHONE, O_ORDER_TAGS,
    O_PIPE17_ORDER_ID, O_ORDER_LINE_ITEMS,
    O_HUBSPOT_DEAL_LINK, O_PIPE17_ORDER_LINK, WRITE_CROSSLINKS,
    HS_APP_BASE, HS_PORTAL_ID, PIPE17_APP_BASE, PIPE17_APP_ORG,
    PIPE17_ORDER_URL_TMPL, PIPE17_ORDER_PREFIX_MAP,
)


def _hubspot_deal_id(order):
    """Deal id behind a Pipe17 order: the hubspot_deal_id custom field Track B sets,
    else the digits left after stripping the currency prefix off extOrderId."""
    for cf in order.get("customFields") or []:
        if cf.get("name") == "hubspot_deal_id" and cf.get("value"):
            return str(cf["value"])
    ext = order.get("extOrderId") or ""
    for prefix in PIPE17_ORDER_PREFIX_MAP.values():
        if prefix and ext.startswith(prefix):
            return ext[len(prefix):] or None
    return None


def _crosslinks(order):
    """HubSpot deal + Pipe17 order deep links for the Orders row."""
    links = {}
    deal_id = _hubspot_deal_id(order)
    if deal_id:
        links[O_HUBSPOT_DEAL_LINK] = f"{HS_APP_BASE}/contacts/{HS_PORTAL_ID}/record/0-3/{deal_id}"
    num = (order.get("extOrderId") or "").lstrip("#")
    if num:
        links[O_PIPE17_ORDER_LINK] = PIPE17_ORDER_URL_TMPL.format(
            base=PIPE17_APP_BASE, org=PIPE17_APP_ORG, num=num)
    return links


def _order_line_items_json(line_items):
    """{"Product ID": [], "Quantity": [...], "SKU": [...]} for real product lines.

    Product ID stays empty (Pipe17 has no Shopify product id). Service lines
    (requiresShipping == False, e.g. "White Glove Delivery") are excluded, matching
    the existing connector's output, which kept only shippable products.
    """
    skus, qtys = [], []
    for li in line_items or []:
        if li.get("requiresShipping") is False:
            continue
        sku = li.get("sku")
        if not sku:
            continue
        skus.append(sku)
        qtys.append(li.get("quantity"))
    return json.dumps({"Product ID": [], "Quantity": qtys, "SKU": skus})


def _contact(order):
    """(name, email, phone, company) primarily from the order's shippingAddress,
    falling back to the top-level customer object if a field is missing there."""
    addr = order.get("shippingAddress") or {}
    cust = order.get("customer") or {}

    def pick(key):
        return addr.get(key) or cust.get(key)

    name = " ".join(x for x in [pick("firstName"), pick("lastName")] if x).strip()
    email = pick("email")
    phone = pick("phone")
    company = pick("company")
    return name or None, email or None, phone or None, company or None


def order_to_airtable(order, customer=None, is_new=True):
    """order = one Pipe17 order (see get-orders payload). `customer` is accepted for
    backward compatibility but no longer required — contact reads from shippingAddress."""
    addr = order.get("shippingAddress") or {}
    name, email, phone, company = _contact(order)
    order_no = order.get("extOrderId")

    deal_name = " - ".join(x for x in [order_no, company, name] if x)
    order_date = (order.get("extOrderCreatedAt") or order.get("createdAt") or "")[:10]

    fields = {
        O_ORDER_NUMBER: order_no,
        O_ORDER_DATE: order_date or None,
        O_DEAL_VALUE: order.get("totalPrice"),
        O_DELIVERY_ADDRESS: addr.get("address1"),
        O_SUITE_NUMBER: addr.get("address2"),
        O_CITY: addr.get("city"),
        O_STATE: addr.get("stateOrProvince"),
        O_ZIP_CODE: addr.get("zipCodeOrPostalCode"),
        O_PIPE17_ORDER_ID: order.get("orderId"),
        O_ORDER_LINE_ITEMS: _order_line_items_json(order.get("lineItems")),
        O_CUSTOMER_NAME: name,
        O_CUSTOMER_EMAIL: email,
        O_CUSTOMER_PHONE: phone,
        O_COMPANY_NAME: company,
        O_DEAL_NAME: deal_name or None,
    }

    # Status is CREATE-ONLY: seed it on insert, never on update (Ops/automations
    # own it after creation; writing it every sync would clobber their changes).
    if is_new:
        fields[O_STATUS] = ORDER_STATUS_SEED

    from config import PIPE17_TAG_FILTER
    tags = [t for t in (order.get("tags") or []) if t and t != PIPE17_TAG_FILTER]
    if tags:
        fields[O_ORDER_TAGS] = tags

    if WRITE_CROSSLINKS:
        fields.update(_crosslinks(order))

    return {k: v for k, v in fields.items() if v not in (None, "")}
