"""Which fields a Pipe17 shipping request may write on its Airtable Shipments row.
Pure functions; no network. See SR_COMMITTED_STATUSES in config.py.

Rows are keyed on the shipping request ID (see shipment_sync), so each SR owns exactly one
row and no SR can overwrite another's. What remains here is the provisional-SR rule: an SR
not yet sent to fulfillment (often on a virtual backorder location Pipe17 will change)
must not overwrite the warehouse, line items or creation date of an EXISTING row, which
also keeps a hand-corrected Origin WH in place until the SR is actually sent.
"""
from config import (
    SR_COMMITTED_STATUSES, SR_CANCELED_STATUSES,
    F_ORIGIN_WH, F_LINE_ITEMS, F_SHIPMENT_CREATION_DATE,
)

ROUTING_FIELDS = (F_ORIGIN_WH, F_LINE_ITEMS, F_SHIPMENT_CREATION_DATE)


def is_committed(sr):
    """True once the SR has been handed to the warehouse (its routing is final)."""
    return (sr or {}).get("status") in SR_COMMITTED_STATUSES


def is_canceled(sr):
    return (sr or {}).get("status") in SR_CANCELED_STATUSES


def gate_routing_fields(fields, sr, row_exists):
    """Drop Origin WH / Line Items / Shipment Creation Date from `fields` (in place) when
    the row already exists and the SR is not committed. Returns the dropped field IDs."""
    if not row_exists or is_committed(sr):
        return []
    dropped = [k for k in ROUTING_FIELDS if k in fields]
    for k in dropped:
        del fields[k]
    return dropped
