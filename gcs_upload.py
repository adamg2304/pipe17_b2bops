"""Upload an order-slip PDF to a public GCS bucket and return its stable public URL.

No-ops (returns None) when GCS_ORDER_SLIP_BUCKET is unset, so the sync keeps working
before the bucket exists. google-cloud-storage is imported lazily so the offline
selftests need no GCP dependency or credentials. The Cloud Run job authenticates with
its runtime service account (ADC); that SA needs object-write on the bucket, and the
bucket must allow public read for the returned URL to resolve without a login.
"""
import logging
import uuid

from config import GCS_ORDER_SLIP_BUCKET, GCS_ORDER_SLIP_PREFIX

log = logging.getLogger("pipe17-airtable")

_client = None


def _bucket():
    global _client
    from google.cloud import storage  # lazy: offline tests never import this
    if _client is None:
        _client = storage.Client()
    return _client.bucket(GCS_ORDER_SLIP_BUCKET)


def upload_order_slip(order_number, pdf_bytes):
    """Upload the PDF and return its public URL, or None when no bucket is configured.
    The object name carries a random suffix so the public URL can't be guessed from the
    order number."""
    if not GCS_ORDER_SLIP_BUCKET:
        return None
    safe = str(order_number or "order").lstrip("#").replace("/", "-")
    path = f"{GCS_ORDER_SLIP_PREFIX}{safe}-{uuid.uuid4().hex[:12]}.pdf"
    blob = _bucket().blob(path)
    blob.upload_from_string(pdf_bytes, content_type="application/pdf")
    return f"https://storage.googleapis.com/{GCS_ORDER_SLIP_BUCKET}/{path}"
