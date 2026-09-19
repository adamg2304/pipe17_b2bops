"""Map a Pipe17 order -> Airtable Orders field dict (field-ID keyed).

Mirrors the target fields of the existing HubSpot/Shopify -> Airtable Orders
connector, sourced from Pipe17 instead. Writes are keyed by Airtable field IDs
(see config.py, O_* constants).

CONTACT SOURCE (decision 2026-09-17): customer name/email/phone/company do NOT live
on the Pipe17 order's shippingAddress (that only carries a concatenated company
string). The fix lives in Track B (HubSpot -> Pipe17), which creates or looks up the
Pipe17 customer by email at order creation. Once that lands, the order carries a
customer and this transform reads contact from it. Until then, pass customer=None and
contact fields come through blank. Company is taken from the customer, never from the
messy shippingAddress.company.

NOT set here (filled by later steps):
  - Order Details / Order Attachments: the generated order-slip PDF (doc step).
  - Notes: composed downstream (QBO invoice no., etc.), not present on the Pipe17 order.
"""
import json

from config import (
    ORDER_STATUS_SEED, PIPE17_TAG_FILTER,
    O_ORDER_NUMBER, O_ORDER_DATE, O_STATUS, O_CUSTOMER_NAME, O_COMPANY_NAME,
    O_DEAL_NAME, O_DEAL_VALUE, O_DELIVERY_ADDRESS, O_SUITE_NUMBER, O_CITY,
    O_STATE, O_ZIP_CODE, O_CUSTOMER_EMAIL, O_CUSTOMER_PHONE, O_ORDER_TAGS,
    O_PIPE17_ORDER_ID, O_ORDER_LINE_ITEMS,
)


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


def _contact(order, customer):
    """(name, email, phone, company) from the Pipe17 customer when present.

    `customer` is the Pipe17 customer object (fetched by main via order.customerId).
    Company comes from the customer, NOT from the concatenated shippingAddress.company.
    """
    c = customer or {}
    name = " ".join(x for x in [c.get("firstName"), c.get("lastName")] if x).strip()
    email = c.get("email") or order.get("email")
    phone = c.get("phone")
    company = c.get("company")
    return name or None, email or None, phone or None, company or None


def order_to_airtable(order, customer=None):
    """order = one Pipe17 order (see get-orders payload). customer = its Pipe17
    customer object, or None until Track B attaches one."""
    addr = order.get("shippingAddress") or {}
    name, email, phone, company = _contact(order, customer)
    order_no = order.get("extOrderId")

    deal_name = " - ".join(x for x in [order_no, company, name] if x)
    order_date = (order.get("extOrderCreatedAt") or order.get("createdAt") or "")[:10]

    fields = {
        O_ORDER_NUMBER: order_no,
        O_ORDER_DATE: order_date or None,
        O_STATUS: ORDER_STATUS_SEED,
        O_DEAL_VALUE: order.get("totalPrice"),
        O_DELIVERY_ADDRESS: addr.get("address1"),
        O_SUITE_NUMBER: addr.get("address2"),
        O_CITY: addr.get("city"),
        O_STATE: addr.get("stateOrProvince"),
        O_ZIP_CODE: addr.get("zipCodeOrPostalCode"),
        O_PIPE17_ORDER_ID: order.get("orderId"),
        O_ORDER_LINE_ITEMS: _order_line_items_json(order.get("lineItems")),
        # contact (blank until Track B populates the Pipe17 customer)
        O_CUSTOMER_NAME: name,
        O_CUSTOMER_EMAIL: email,
        O_CUSTOMER_PHONE: phone,
        O_COMPANY_NAME: company,
        O_DEAL_NAME: deal_name or None,
    }

    # Tags: pass through, minus the internal Airtable gate tag. typecast on the
    # upsert matches existing options and creates any that are new.
    tags = [t for t in (order.get("tags") or []) if t and t != PIPE17_TAG_FILTER]
    if tags:
        fields[O_ORDER_TAGS] = tags

    return {k: v for k, v in fields.items() if v not in (None, "")}
