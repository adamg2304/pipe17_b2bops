"""Attach a rendered PDF straight to an Airtable attachment field.

Uses Airtable's content upload endpoint (base64 file content, no external hosting).
Requires the PAT to have data.records:write on the base. Files must be <= 5 MB.
"""
import base64
import requests
from config import AIRTABLE_API_KEY, AIRTABLE_BASE_ID

_CONTENT = "https://content.airtable.com/v0"


def attach_pdf(record_id, field_id, filename, pdf_bytes):
    url = f"{_CONTENT}/{AIRTABLE_BASE_ID}/{record_id}/{field_id}/uploadAttachment"
    payload = {"contentType": "application/pdf", "filename": filename,
               "file": base64.b64encode(pdf_bytes).decode()}
    resp = requests.post(url, headers={"Authorization": f"Bearer {AIRTABLE_API_KEY}",
                                       "Content-Type": "application/json"},
                         json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()
