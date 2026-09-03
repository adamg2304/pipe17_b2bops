"""Map a Pipe17 shipping request -> Airtable Shipments field dict.

Mirrors the skubana-era Shipments shape. Writes are keyed by Airtable FIELD IDs
(see config.py) so a UI rename can't silently break the sync. The Order Link (parent)
is resolved and injected in main.py, because it needs the parent Order's recordId.

NOMENCLATURE (decision, reversed): keep Pipe17's raw dot form (#TestCA1040.2) so ids
round-trip to Pipe17 cleanly. The Airtable "Parent Order (FX)" formula was made
delimiter-agnostic (handles "(" or "." or bare), so no data transformation is needed
and every existing formula/automation keeps working. normalize_shipment_number is
retained behind NORMALIZE_SHIPMENT_NUMBER (default False) for optionality only.

STATE CAVEAT: F_STATE ("State" on Shipments) is a singleSelect, and airtable_client
upserts with typecast=True. A Pipe17 stateOrProvince value that isn't an existing
option (e.g. "California" vs option "CA") will AUTO-CREATE a new select option. If
Pipe17's format doesn't match the base's State options, add a STATE_MAP gate here
(like LOCATION_MAP/STATUS_MAP) or drop typecast for this field before going live.
"""
import re

from config import (
    LOCATION_MAP, STATUS_MAP, DEFAULT_SHIPMENT_STATUS, NORMALIZE_SHIPMENT_NUMBER,
    F_SHIPMENT_NUMBER, F_CUSTOMER_NAME, F_DELIVERY_ADDRESS, F_CITY, F_ZIP_CODE,
    F_CUSTOMER_EMAIL, F_LINE_ITEMS, F_STATE, F_ORIGIN_WH, F_STATUS,
    F_SHIPMENT_CREATION_DATE,
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
        # Primary / merge key: raw Pipe17 dot form by default (normalization off).
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
        fields[F_STATE] = state  # singleSelect + typecast — see STATE CAVEAT above

    wh = LOCATION_MAP.get(sr.get("locationId"))
    if wh:
        fields[F_ORIGIN_WH] = wh

    status = STATUS_MAP.get(sr.get("status"), DEFAULT_SHIPMENT_STATUS)
    if status:
        fields[F_STATUS] = status

    created = sr.get("createdAt")
    if created:
        fields[F_SHIPMENT_CREATION_DATE] = created[:10]

    return {k: v for k, v in fields.items() if v not in (None, "")}


def order_number_of(sr):
    """Parent order human id, used to resolve the Order Link (always the base #)."""
    return sr.get("extOrderId")
