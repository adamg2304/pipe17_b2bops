"""Orders dry-run reconciliation. Runs in Cloud Shell (needs live creds).

For each tagged Pipe17 order in the lookback window, maps it with the real transform,
fetches the existing Airtable Orders row (created today by the HubSpot -> Airtable
connector), and diffs field by field. Writes NOTHING. Use it during parallel run to
confirm the Pipe17 sync would produce data equivalent to the current connector before
cutover.

Fields fall in three buckets:
  SHOULD MATCH   order#, date, value, address, city/state/zip, and SKU+Qty of line items
  KNOWN DIFF     contact (pending Track B), Pipe17 Order ID (new), status seed, tags,
                 line-item Product ID (Pipe17 has no Shopify id), doc/notes fields
  MISMATCH       a SHOULD-MATCH field that disagrees -> investigate

Run:  python3 reconcile_orders.py            (uses SYNC_LOOKBACK_MINUTES)
      LOOKBACK_MIN=43200 python3 reconcile_orders.py   (30-day window)
"""
import datetime as dt
import json
import os

import requests

import config as c
import pipe17_client as p17
from order_transform import order_to_airtable

LABELS = {
    c.O_ORDER_NUMBER: "Order Number", c.O_ORDER_DATE: "Order Date",
    c.O_DEAL_VALUE: "Deal Value", c.O_DELIVERY_ADDRESS: "Delivery Address",
    c.O_SUITE_NUMBER: "Suite Number", c.O_CITY: "City", c.O_STATE: "State",
    c.O_ZIP_CODE: "Zip Code", c.O_ORDER_LINE_ITEMS: "Line Items (SKU+Qty)",
    c.O_CUSTOMER_NAME: "Customer Name", c.O_CUSTOMER_EMAIL: "Customer Email",
    c.O_CUSTOMER_PHONE: "Customer Phone", c.O_COMPANY_NAME: "Company Name",
    c.O_DEAL_NAME: "Deal Name", c.O_PIPE17_ORDER_ID: "Pipe17 Order ID",
    c.O_STATUS: "Status", c.O_ORDER_TAGS: "Order Tags",
    c.O_ORDER_DETAILS: "Order Details", c.O_NOTES: "Notes",
}

SHOULD_MATCH = [c.O_ORDER_NUMBER, c.O_ORDER_DATE, c.O_DEAL_VALUE,
                c.O_DELIVERY_ADDRESS, c.O_SUITE_NUMBER, c.O_CITY, c.O_STATE, c.O_ZIP_CODE]

KNOWN_DIFF = {
    c.O_CUSTOMER_NAME: "pending Track B customer",
    c.O_CUSTOMER_EMAIL: "pending Track B customer",
    c.O_CUSTOMER_PHONE: "pending Track B customer",
    c.O_COMPANY_NAME: "pending Track B customer",
    c.O_DEAL_NAME: "depends on contact",
    c.O_PIPE17_ORDER_ID: "new field; connector rows predate it",
    c.O_STATUS: "seed vs base-derived status",
    c.O_ORDER_TAGS: "Pipe17 tags vs HubSpot tags",
    c.O_ORDER_DETAILS: "filled by doc step",
    c.O_NOTES: "composed downstream",
}


def _norm(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v).strip()


def _match(a, b):
    na, nb = _norm(a), _norm(b)
    if isinstance(na, float) and isinstance(nb, float):
        return abs(na - nb) < 0.005
    return na == nb


def _pairs(li_json):
    """[(sku, qty), ...] sorted, from an Order Line Items JSON string (Product ID ignored)."""
    try:
        d = json.loads(li_json) if li_json else {}
    except (TypeError, ValueError):
        return None
    return sorted(zip(d.get("SKU", []), d.get("Quantity", [])))


def reconcile(p17_fields, at_fields):
    matched, mism, info = [], [], []
    for fid in SHOULD_MATCH:
        if _match(p17_fields.get(fid), at_fields.get(fid)):
            matched.append(fid)
        else:
            mism.append((fid, p17_fields.get(fid), at_fields.get(fid)))
    # line items: SKU+Qty only
    pp, ap = _pairs(p17_fields.get(c.O_ORDER_LINE_ITEMS)), _pairs(at_fields.get(c.O_ORDER_LINE_ITEMS))
    if pp == ap:
        matched.append(c.O_ORDER_LINE_ITEMS)
    else:
        mism.append((c.O_ORDER_LINE_ITEMS, pp, ap))
    for fid, reason in KNOWN_DIFF.items():
        if not _match(p17_fields.get(fid), at_fields.get(fid)):
            info.append((fid, reason))
    return matched, mism, info


def airtable_order(order_number):
    url = f"https://api.airtable.com/v0/{c.AIRTABLE_BASE_ID}/{c.ORDERS_TABLE}"
    formula = '{%s}="%s"' % (c.ORDER_NUMBER_FIELD, order_number)
    r = requests.get(url, headers={"Authorization": f"Bearer {c.AIRTABLE_API_KEY}"},
                     params={"filterByFormula": formula, "maxRecords": 1,
                             "returnFieldsByFieldId": "true"}, timeout=30)
    r.raise_for_status()
    recs = r.json().get("records", [])
    return recs[0]["fields"] if recs else None


def main():
    minutes = int(os.environ.get("LOOKBACK_MIN", c.SYNC_LOOKBACK_MINUTES))
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)).isoformat()
    tag = c.PIPE17_TAG_FILTER.strip()
    print(f"Reconciling tagged Pipe17 orders (status={c.ORDER_SYNC_STATUSES}) since {since}\n")

    checked = clean = to_create = 0
    for o in p17.iter_orders(since, tag=tag or None, statuses=c.ORDER_SYNC_STATUSES):
        if tag and tag not in (o.get("tags") or []):
            continue
        fields = order_to_airtable(o, p17.get_customer(o.get("customerId")))
        on = fields.get(c.O_ORDER_NUMBER)
        if not on:
            continue
        at = airtable_order(on)
        if at is None:
            to_create += 1
            print(f"{on}: no Airtable row yet -> sync would CREATE it")
            continue
        matched, mism, info = reconcile(fields, at)
        checked += 1
        status = "CLEAN" if not mism else f"{len(mism)} MISMATCH"
        print(f"{on}: {len(matched)}/{len(SHOULD_MATCH)+1} should-match OK  [{status}]")
        for fid, pv, av in mism:
            print(f"    MISMATCH {LABELS.get(fid, fid)}: pipe17={pv!r}  airtable={av!r}")
        for fid, reason in info:
            print(f"    diff (expected) {LABELS.get(fid, fid)}: {reason}")
        if not mism:
            clean += 1

    print(f"\n{checked} existing orders checked, {clean} clean on should-match fields, "
          f"{to_create} would be newly created.")


if __name__ == "__main__":
    main()
