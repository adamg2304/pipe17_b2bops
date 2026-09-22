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
