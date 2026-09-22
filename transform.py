"""Map a Pipe17 shipping request -> Airtable Shipments field dict (field-ID keyed)."""
import re
from config import (
    LOCATION_MAP, STATUS_MAP, DEFAULT_SHIPMENT_STATUS, NORMALIZE_SHIPMENT_NUMBER,
    F_SHIPMENT_NUMBER, F_CUSTOMER_NAME, F_DELIVERY_ADDRESS, F_CITY, F_ZIP_CODE,
    F_CUSTOMER_EMAIL, F_LINE_ITEMS, F_STATE, F_ORIGIN_WH, F_STATUS,
    F_SHIPMENT_CREATION_DATE, F_PIPE17_REQUEST_LINK,
    WRITE_CROSSLINKS, PIPE17_APP_BASE, PIPE17_APP_ORG, PIPE17_SHIPMENT_URL_TMPL,
)

_SPLIT_SUFFIX = re.compile(r"\.(\d+)$")


def normalize_shipment_number(ext_shipment_id):
    if not ext_shipment_id or not NORMALIZE_SHIPMENT_NUMBER:
        return ext_shipment_id
    return _SPLIT_SUFFIX.sub(r"(\1)", ext_shipment_id)


def _fmt_line_items(line_items):
    parts = []
    for li in line_items or []:
        qty, sku, name = li.get("quantity"), li.get("sku", ""), li.get("name", "")
        parts.append(f"{qty} x {sku} - {name}".strip(" -"))
    return "; ".join(parts)


def shipment_to_airtable(sr):
    addr = sr.get("shippingAddress") or {}
    name = " ".join(x for x in [addr.get("firstName"), addr.get("lastName")] if x).strip()
    fields = {
        F_SHIPMENT_NUMBER: normalize_shipment_number(sr.get("extShipmentId")),
        F_CUSTOMER_NAME: name or None,
        F_DELIVERY_ADDRESS: addr.get("address1"),
        F_CITY: addr.get("city"),
        F_ZIP_CODE: addr.get("zipCodeOrPostalCode"),
        F_CUSTOMER_EMAIL: addr.get("email"),
        F_LINE_ITEMS: _fmt_line_items(sr.get("lineItems")),
    }
    state = addr.get("stateOrProvince")
    if state:
        fields[F_STATE] = state
    wh = LOCATION_MAP.get(sr.get("locationId"))
    if wh:
        fields[F_ORIGIN_WH] = wh
    status = STATUS_MAP.get(sr.get("status"), DEFAULT_SHIPMENT_STATUS)
    if status:
        fields[F_STATUS] = status
    created = sr.get("createdAt")
    if created:
        fields[F_SHIPMENT_CREATION_DATE] = created[:10]
    if WRITE_CROSSLINKS:
        num = (sr.get("extShipmentId") or "").lstrip("#")
        if num:
            fields[F_PIPE17_REQUEST_LINK] = PIPE17_SHIPMENT_URL_TMPL.format(
                base=PIPE17_APP_BASE, org=PIPE17_APP_ORG, num=num)
    return {k: v for k, v in fields.items() if v not in (None, "")}


def order_number_of(sr):
    return sr.get("extOrderId")
