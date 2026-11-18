"""Airtable client: idempotent batched upsert + parent-order lookup."""
import requests
from config import AIRTABLE_API_KEY, AIRTABLE_BASE_ID

BASE = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}


def find_record_id(table, field_name, value):
    url = f"{BASE}/{table}"
    formula = '{%s}="%s"' % (field_name, value)
    resp = requests.get(url, headers=HEADERS,
                        params={"filterByFormula": formula, "maxRecords": 1}, timeout=30)
    resp.raise_for_status()
    recs = resp.json().get("records", [])
    return recs[0]["id"] if recs else None


def create_comment(table, record_id, text):
    """Post a comment on a record (the repair service's ops-facing 'thread'). Best-effort:
    returns True on success, False on failure — a comment must never fail the repair."""
    if not (table and record_id and text):
        return False
    url = f"{BASE}/{table}/{record_id}/comments"
    try:
        resp = requests.post(url, headers=HEADERS, json={"text": text}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception:
        return False


def find_record(table, field_name, value):
    """Return the full record (fields keyed by field ID) where field_name==value, or None."""
    url = f"{BASE}/{table}"
    formula = '{%s}="%s"' % (field_name, value)
    resp = requests.get(url, headers=HEADERS,
                        params={"filterByFormula": formula, "maxRecords": 1,
                                "returnFieldsByFieldId": "true"}, timeout=30)
    resp.raise_for_status()
    recs = resp.json().get("records", [])
    return recs[0] if recs else None


def list_records(table, formula, field_ids=None, page_size=100):
    """All records matching a filterByFormula, paginated, keyed by field ID. Used by the
    inventory-state recovery pass + backfill to read candidate orders FROM Airtable."""
    url = f"{BASE}/{table}"
    out, offset = [], None
    while True:
        params = [("filterByFormula", formula), ("pageSize", str(page_size)),
                  ("returnFieldsByFieldId", "true")]
        for fid in (field_ids or []):
            params.append(("fields[]", fid))
        if offset:
            params.append(("offset", offset))
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        out.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return out


def _formula_str(v):
    """A value as an Airtable formula string literal (escape backslash + double quote)."""
    return '"%s"' % str(v).replace("\\", "\\\\").replace('"', '\\"')


def find_records_by_values(table, field_name, field_id, values, chunk=40):
    """({value: record}, {value: [extra record ids]}) for every record whose field equals
    one of `values` (fields keyed by field ID). `field_name` builds the formula (formulas
    need names); `field_id` reads the value back. Batched into OR() formulas so one
    order's SRs cost one read. A value on several records keeps the oldest-created one
    and reports the rest as dupes, so a duplicate key is surfaced, never silently used."""
    vals = sorted({str(v) for v in values if v})
    found, dupes = {}, {}
    for i in range(0, len(vals), chunk):
        part = vals[i:i + chunk]
        formula = "OR(%s)" % ",".join("{%s}=%s" % (field_name, _formula_str(v)) for v in part)
        for rec in list_records(table, formula):
            key = (rec.get("fields") or {}).get(field_id)
            key = str(key) if key else None
            if key not in part:
                continue
            if key in found:
                keep, other = sorted([found[key], rec], key=lambda r: r.get("createdTime") or "")
                found[key] = keep
                dupes.setdefault(key, []).append(other["id"])
            else:
                found[key] = rec
    return found, dupes


def update_records(table, updates, typecast=True):
    """PATCH many records, 10 per request. `updates` is [(record_id, fields)]."""
    url = f"{BASE}/{table}"
    out = []
    for i in range(0, len(updates), 10):
        batch = updates[i:i + 10]
        payload = {"records": [{"id": rid, "fields": f} for rid, f in batch],
                   "typecast": typecast, "returnFieldsByFieldId": True}
        resp = requests.patch(url, headers=HEADERS, json=payload, timeout=30)
        resp.raise_for_status()
        out.extend(resp.json().get("records", []))
    return out


def update_record(table, record_id, fields):
    """PATCH fields (keyed by field ID) onto one record — e.g. clear an attachment."""
    url = f"{BASE}/{table}/{record_id}"
    resp = requests.patch(url, headers=HEADERS,
                          json={"fields": fields, "typecast": True}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def upsert(table, records, merge_fields, typecast=True):
    url = f"{BASE}/{table}"
    created = updated = 0
    out = []
    for i in range(0, len(records), 10):
        batch = records[i:i + 10]
        payload = {"performUpsert": {"fieldsToMergeOn": merge_fields},
                   "records": [{"fields": f} for f in batch], "typecast": typecast,
                   "returnFieldsByFieldId": True}
        resp = requests.patch(url, headers=HEADERS, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        created += len(data.get("createdRecords", []))
        updated += len(data.get("updatedRecords", []))
        out.extend(data.get("records", []))
    return created, updated, out


def create_record(table, fields, typecast=True):
    """Create one record (fields keyed by field ID); returns the created record."""
    url = f"{BASE}/{table}"
    resp = requests.post(url, headers=HEADERS,
                         json={"fields": fields, "typecast": typecast,
                               "returnFieldsByFieldId": True}, timeout=30)
    resp.raise_for_status()
    return resp.json()
