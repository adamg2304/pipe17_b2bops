"""Map a Pipe17 shipping request -> Airtable Shipments field dict.

Mirrors the skubana-era Shipments shape. The Order Link (parent) is resolved and
injected in main.py, because it needs the parent Order's Airtable recordId.

NOMENCLATURE: Pipe17 numbers splits with a dot (#TestCA1040.2), but the Airtable
base was built for skubana's parenthesis convention (#TestCA1040(2)) — the
"Parent Order (FX)" formula and the "Child / Parent - Relational Database"
automation both parse on "(". So we normalize .N -> (N) before writing, which lets
every existing formula/automation keep working unchanged.
"""
import re

from config import (
    LOCATION_MAP, STATUS_MAP, DEFAULT_SHIPMENT_STATUS, NORMALIZE_SHIPMENT_NUMBER,
)

_SPLIT_SUFFIX = re.compile(r"\.(\d+)$")


def normalize_shipment_number(ext_shipment_id):
    """'#TestCA1040.2' -> '#TestCA1040(2)'. Bare (first split / single) unchanged."""
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
    """sr = one Pipe17 shipping request (see list-shipping-requests payload)."""
    addr = sr.get("shippingAddress") or {}
    name = " ".join(x for x in [addr.get("firstName"), addr.get("lastName")] if x).strip()

    fields = {
        # Primary / merge key: normalized to skubana paren shape.
        "Shipment Number": normalize_shipment_number(sr.get("extShipmentId")),
        "Customer Name": name or None,
        "Delivery Address": addr.get("address1"),
        "City": addr.get("city"),
        "Zip Code": addr.get("zipCodeOrPostalCode"),
        "Customer Email": addr.get("email"),
        "Line Items": _fmt_line_items(sr.get("lineItems")),
    }

    state = addr.get("stateOrProvince")
    if state:
        fields["State"] = state

    wh = LOCATION_MAP.get(sr.get("locationId"))
    if wh:
        fields["Origin WH"] = wh

    status = STATUS_MAP.get(sr.get("status"), DEFAULT_SHIPMENT_STATUS)
    if status:
        fields["Status"] = status

    created = sr.get("createdAt")
    if created:
        fields["Shipment Creation Date"] = created[:10]

    return {k: v for k, v in fields.items() if v not in (None, "")}


def order_number_of(sr):
    """Parent order human id, used to resolve the Order Link (always the base #)."""
    return sr.get("extOrderId")
