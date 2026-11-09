"""Decide which Pipe17 shipping request owns an Airtable Shipments row, and which of its
fields it may write. Pure functions; no network. See SR_COMMITTED_STATUSES in config.py.

Two problems this guards against:
  1. A provisional SR (not yet sent to fulfillment) overwriting the warehouse, line
     items or creation date of a row. Its warehouse is often a virtual backorder
     location that Pipe17 will change before it ships.
  2. Two SRs sharing one extShipmentId (Pipe17 cancels an SR and re-creates one under
     the same number). Only one can own the row; the live one wins.
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


def _rank(sr):
    """Higher wins: live beats canceled, committed beats provisional, newer beats older."""
    return (not is_canceled(sr), is_committed(sr), (sr or {}).get("updatedAt") or "")


def prefer(a, b):
    """Of two SRs with the same shipment number, return the one that should own the row."""
    if a is None:
        return b
    if b is None:
        return a
    return b if _rank(b) > _rank(a) else a


def pick_owner(srs, ext_shipment_id):
    """The SR that owns `ext_shipment_id` among `srs` (all SRs of one order), or None."""
    owner = None
    for s in srs or []:
        if s.get("extShipmentId") == ext_shipment_id:
            owner = prefer(owner, s)
    return owner


def gate_routing_fields(fields, sr, row_exists):
    """Drop Origin WH / Line Items / Shipment Creation Date from `fields` (in place) when
    the row already exists and the SR is not committed. Returns the dropped field IDs."""
    if not row_exists or is_committed(sr):
        return []
    dropped = [k for k in ROUTING_FIELDS if k in fields]
    for k in dropped:
        del fields[k]
    return dropped
