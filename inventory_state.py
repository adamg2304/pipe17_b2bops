"""Compute the sync-owned Inventory State + Exception Flag note for a Track A order.

Pure functions. The sync passes in the Pipe17 order object and its shipping requests
(SRs); nothing here touches the network, so the selftest can stub both.

Signal precedence (from real data — read carefully):
  - `exceptions` is STICKY: eRouteOutOfStock lingers on archived/excluded/dead orders,
    so we never key state off the exception alone.
  - order.dispositions can be STALE; the SRs are ground truth for what routed. We compute
    `routed` from SR line quantities, never from order.dispositions.
  - An SR existing (even in reviewRequired) means those units are committed, i.e. routed.

Per order:
  1. ACTIVE GUARD — status in INACTIVE_ORDER_STATUSES or routingStatus == excluded -> the
     order is not awaiting inventory; CLEAR any prior state + note.
  2. oos_active = active AND (OOS_EXCEPTION in exceptions OR routingStatus == pendingInventory).
  3. ordered = sum of shippable order-line qty; routed = sum of all SR-line qty.
  4. State on ORDER-LEVEL totals:
       oos_active and routed == 0        -> Awaiting Inventory
       oos_active and routed > 0         -> Partially Out of Stock
       not oos_active and routed==ordered-> Fully In Stock (note cleared)
       not oos_active and routed<ordered -> UNSET (still routing; assert nothing)

compute() returns a Result:
  write == False  -> leave both fields untouched (the UNSET case).
  write == True   -> set Inventory State to `state` (None clears) and Exception Flag to
                     `note` (None clears).
"""
import logging

from config import (
    INV_FULLY_IN_STOCK, INV_PARTIALLY_OOS, INV_AWAITING, INVENTORY_STATE_OPTIONS,
    OOS_EXCEPTION, ROUTING_PENDING_INVENTORY, ROUTING_EXCLUDED, INACTIVE_ORDER_STATUSES,
)

log = logging.getLogger("pipe17-airtable")


class Result:
    __slots__ = ("write", "state", "note")

    def __init__(self, write, state=None, note=None):
        self.write = write      # False -> omit both fields (leave as-is)
        self.state = state      # Inventory State value, or None to clear
        self.note = note        # Exception Flag note, or None to clear

    def __repr__(self):
        return f"Result(write={self.write}, state={self.state!r}, note={self.note!r})"

    def __eq__(self, other):
        return (isinstance(other, Result) and self.write == other.write
                and self.state == other.state and self.note == other.note)


UNSET = Result(False)


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def is_active(order):
    """An order is active (could be awaiting inventory) unless it's in a dead status or
    its routing was excluded. The guard that makes the sticky exception safe."""
    status = (order.get("status") or "").strip()
    routing = (order.get("routingStatus") or "").strip()
    if status in INACTIVE_ORDER_STATUSES:
        return False
    if routing == ROUTING_EXCLUDED:
        return False
    return True


def oos_active(order):
    if not is_active(order):
        return False
    if OOS_EXCEPTION in (order.get("exceptions") or []):
        return True
    return (order.get("routingStatus") or "").strip() == ROUTING_PENDING_INVENTORY


def _ordered_lines(order):
    """Shippable order lines as [(uniqueId, sku, ordered_qty)]. Service lines
    (requiresShipping is False, e.g. White Glove) are excluded, matching SR contents."""
    out = []
    for li in order.get("lineItems") or []:
        if li.get("requiresShipping") is False:
            continue
        qty = _num(li.get("quantity") if li.get("quantity") is not None else li.get("ordered"))
        out.append((li.get("uniqueId"), li.get("sku"), qty))
    return out


def _routed_maps(shipments):
    """(routed_total, by_uniqueId, by_sku) summed over every SR line. SR lines carry the
    order line's uniqueId (verified), so uniqueId is the primary match key; sku is the
    fallback when a line lacks it."""
    total = 0
    by_uid, by_sku = {}, {}
    for sr in shipments or []:
        for li in sr.get("lineItems") or []:
            q = _num(li.get("quantity"))
            total += q
            uid, sku = li.get("uniqueId"), li.get("sku")
            if uid:
                by_uid[uid] = by_uid.get(uid, 0) + q
            if sku:
                by_sku[sku] = by_sku.get(sku, 0) + q
    return total, by_uid, by_sku


def _note(ordered_lines, by_uid, by_sku):
    """`OOS: <sku> x<short> short (<routed>/<ordered> routed); ...` for lines still short."""
    parts = []
    for uid, sku, ordered in ordered_lines:
        routed = by_uid.get(uid) if uid in by_uid else by_sku.get(sku, 0)
        short = ordered - routed
        if short > 0:
            parts.append(f"{sku} x{short} short ({routed}/{ordered} routed)")
    return "OOS: " + "; ".join(parts) if parts else ""


def compute(order, shipments):
    """-> Result. See module docstring for the precedence rules."""
    if not is_active(order):
        return Result(True, state=None, note=None)   # active guard: clear both

    ordered_lines = _ordered_lines(order)
    ordered_total = sum(q for _, _, q in ordered_lines)
    routed_total, by_uid, by_sku = _routed_maps(shipments)

    if oos_active(order):
        state = INV_AWAITING if routed_total == 0 else INV_PARTIALLY_OOS
        return Result(True, state=state, note=_note(ordered_lines, by_uid, by_sku))

    if ordered_total > 0 and routed_total >= ordered_total:
        return Result(True, state=INV_FULLY_IN_STOCK, note=None)   # fully routed, clear note

    return UNSET   # not OOS but not fully routed -> still routing, assert nothing


def apply_to_fields(fields, order, shipments, state_field, note_field):
    """Merge the computed Inventory State / Exception Flag into an Airtable field dict.
    Never auto-creates a select option: a computed state outside the known options is
    logged and skipped. Mutates and returns `fields`."""
    res = compute(order, shipments)
    if not res.write:
        return fields
    if res.state is not None and res.state not in INVENTORY_STATE_OPTIONS:
        log.warning("Order %s: computed Inventory State %r is not an existing option — skipping",
                    order.get("extOrderId"), res.state)
        return fields
    fields[state_field] = res.state   # None clears the single-select
    fields[note_field] = res.note or None   # None/"" clears the long-text note
    return fields
