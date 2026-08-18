"""Airtable client: idempotent batched upsert + parent-order lookup.

Upsert merges on the primary field (Shipment Number / Order Number), so re-runs
update in place. Order-link resolution looks up the parent Order's recordId by
Order Number.
"""
import time
import requests

from config import AIRTABLE_API_KEY, AIRTABLE_BASE_ID

BASE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json",
}


def _escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def find_record_id(table, field_name, value):
    """Return the recordId of the first row where field_name == value, or None."""
    url = f"{BASE_URL}/{table}"
    formula = f'{{{field_name}}}="{_escape(value)}"'
    resp = requests.get(url, headers=HEADERS,
                        params={"filterByFormula": formula, "maxRecords": 1},
                        timeout=30)
    resp.raise_for_status()
    recs = resp.json().get("records", [])
    return recs[0]["id"] if recs else None


def upsert(table, records, merge_fields, typecast=True):
    """Upsert records (list of {field: value}) merging on merge_fields.
    Returns (created_count, updated_count)."""
    url = f"{BASE_URL}/{table}"
    created = updated = 0
    for i in range(0, len(records), 10):
        batch = records[i:i + 10]
        payload = {
            "performUpsert": {"fieldsToMergeOn": merge_fields},
            "records": [{"fields": r} for r in batch],
            "typecast": typecast,
        }
        for attempt in range(4):
            resp = requests.patch(url, headers=HEADERS, json=payload, timeout=30)
            if resp.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            body = resp.json()
            created += len(body.get("createdRecords", []))
            updated += len(body.get("updatedRecords", []))
            break
        time.sleep(0.25)  # stay under 5 req/s
    return created, updated
