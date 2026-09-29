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
