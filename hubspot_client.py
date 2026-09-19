"""HubSpot client: find deposit-paid deals, read their line items (net price + position),
write back the Pipe17 order id."""
import time
import requests

from config import (
    HUBSPOT_TOKEN, HUBSPOT_API_BASE,
    HS_TRIGGER_MODE, HS_DEPOSIT_PAID_DATE_PROP, HS_DEPOSIT_PAID_STAGE_ID,
    HS_DEAL_PIPELINE_ID, HS_PIPE17_ORDER_ID_PROP,
    HS_CURRENCY_PROP, HS_SHIP_ADDR_PROPS, HS_LI_PROPS,
    HS_DELIVERY_CONTACT_LABEL,
)

HEADERS = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

# Extra line-item properties we always pull: net line total (amount) + display order.
_LI_EXTRA = ["amount", "hs_position_on_quote"]


def _request(method, path, **kwargs):
    url = f"{HUBSPOT_API_BASE}{path}"
    for attempt in range(4):
        resp = requests.request(method, url, headers=HEADERS, timeout=30, **kwargs)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2 ** attempt); continue
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    resp.raise_for_status()


def _deal_properties():
    props = {HS_CURRENCY_PROP, HS_PIPE17_ORDER_ID_PROP, "dealname", "amount"}
    props.update(v for v in HS_SHIP_ADDR_PROPS.values() if v)
    return sorted(props)


def _search_filters(since_epoch_ms):
    not_synced = {"propertyName": HS_PIPE17_ORDER_ID_PROP, "operator": "NOT_HAS_PROPERTY"}
    if HS_TRIGGER_MODE == "stage":
        filters = [
            {"propertyName": "dealstage", "operator": "EQ", "value": HS_DEPOSIT_PAID_STAGE_ID},
            {"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": since_epoch_ms},
            not_synced,
        ]
        if HS_DEAL_PIPELINE_ID:
            filters.append({"propertyName": "pipeline", "operator": "EQ", "value": HS_DEAL_PIPELINE_ID})
    else:
        filters = [
            {"propertyName": HS_DEPOSIT_PAID_DATE_PROP, "operator": "GTE", "value": since_epoch_ms},
            not_synced,
        ]
    return [{"filters": filters}]


def find_deposit_paid_deals(since_epoch_ms):
    after = None
    while True:
        body = {
            "filterGroups": _search_filters(since_epoch_ms),
            "properties": _deal_properties(),
            "limit": 100,
            "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "ASCENDING"}],
        }
        if after:
            body["after"] = after
        data = _request("POST", "/crm/v3/objects/deals/search", json=body)
        for row in data.get("results", []):
            yield row
        after = (data.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(0.2)


def get_deal(deal_id):
    """Fetch a single deal by id with the same properties used by the search."""
    return _request("GET", f"/crm/v3/objects/deals/{deal_id}",
                    params={"properties": _deal_properties()})


def get_line_items(deal_id):
    """Return line items in HubSpot display order: [{sku,name,quantity,price,amount,position}].
    Preserves the association order, then sorts by hs_position_on_quote when any is set."""
    assoc = _request("GET", f"/crm/v4/objects/deals/{deal_id}/associations/line_items")
    ids = [r["toObjectId"] for r in assoc.get("results", [])]
    if not ids:
        return []

    wanted = list(HS_LI_PROPS.values()) + _LI_EXTRA
    body = {"inputs": [{"id": str(i)} for i in ids], "properties": wanted}
    data = _request("POST", "/crm/v3/objects/line_items/batch/read", json=body)
    by_id = {row["id"]: row.get("properties", {}) for row in data.get("results", [])}

    items = []
    for i in ids:  # association order
        p = by_id.get(str(i))
        if p is None:
            continue
        items.append({
            "sku": p.get(HS_LI_PROPS["sku"]),
            "name": p.get(HS_LI_PROPS["name"]),
            "quantity": p.get(HS_LI_PROPS["quantity"]),
            "price": p.get(HS_LI_PROPS["price"]),
            "amount": p.get("amount"),
            "position": p.get("hs_position_on_quote"),
        })

    # Order to match HubSpot: sort by hs_position_on_quote when present (it comes back as
    # a string, sometimes "0"), else keep the association order (already the display order).
    def _pos(it):
        p = it.get("position")
        try:
            return (0, int(float(p)))
        except (TypeError, ValueError):
            return (1, 0)  # no position -> after positioned items, association order preserved
    if any(str(it.get("position")) not in ("None", "") for it in items):
        items = sorted(enumerate(items), key=lambda t: (_pos(t[1]), t[0]))
        items = [it for _, it in items]
    return items


def get_labeled_contact(deal_id, label):
    """Return the contact associated to the deal under a specific association label
    (e.g. 'End User - Delivery Contact'), or None. Matches the label case-insensitively."""
    assoc = _request("GET", f"/crm/v4/objects/deals/{deal_id}/associations/contacts")
    want = (label or "").strip().lower()
    cid = None
    for r in assoc.get("results", []):
        for t in r.get("associationTypes", []):
            if (t.get("label") or "").strip().lower() == want:
                cid = r.get("toObjectId")
                break
        if cid:
            break
    if not cid:
        return None
    return _request("GET", f"/crm/v3/objects/contacts/{cid}",
                    params={"properties": ["firstname", "lastname", "email", "phone"]})


def delivery_contact_of(deal_id):
    """The deal's delivery contact as {firstName,lastName,email,phone}, or None."""
    c = get_labeled_contact(deal_id, HS_DELIVERY_CONTACT_LABEL)
    if not c:
        return None
    p = c.get("properties", {})
    contact = {"firstName": p.get("firstname"), "lastName": p.get("lastname"),
               "email": p.get("email"), "phone": p.get("phone")}
    contact = {k: v for k, v in contact.items() if v}
    return contact or None


def set_pipe17_order_id(deal_id, pipe17_order_id):
    body = {"properties": {HS_PIPE17_ORDER_ID_PROP: pipe17_order_id}}
    _request("PATCH", f"/crm/v3/objects/deals/{deal_id}", json=body)
