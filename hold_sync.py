"""Inventory-hold (V2) reconcile for Track B.

Second trigger on the SAME Pipe17 order the Ready-for-Ops flow later releases
(one order per deal across the lifecycle, extOrderId = #BE/#CEN<dealId>).

Per deal, classify by stage + the inventory_hold_approved checkbox, then reconcile
the Pipe17 order to the desired shape:

  hold       stage in HOLD_STAGES and checkbox set
             -> order at readyForFulfillment, tag B2BInventoryHold (no Airtable)
  confirmed  stage == Ready for Ops
             -> order at readyForFulfillment, tags Airtable + B2BConfirmReview + routing
  withdrawn  stage == Closed lost (supersedes the checkbox), OR checkbox cleared in a
             hold stage -> cancel the order IF it is still a hold (pre-release)
  noop       anything else

Both order shapes commit inventory and generate shipping requests; an Ops tag-keyed
automation parks the SRs in Review Required. Our code only sets the tag + status.

Update is signature-gated: an existing held/confirmed order is PATCHed only when its
line items (sorted sku+qty+price) or its tags actually differ, so we don't churn on
every deal edit. Cancel only touches orders that still carry the hold tag, so a
released (Ready-for-Ops) order is never cancelled by a later loss.

reconcile_deal takes hs / p17w / key_map injected so selftest can stub them.
"""
import datetime as dt
import logging

import transform_order
from transform_order import build_order, currency_of, ext_order_id_of
from config import (
    HS_DEAL_STAGE_PROP, HS_INVENTORY_HOLD_PROP,
    HS_HOLD_STAGE_IDS, HS_READY_FOR_OPS_STAGE_ID, HS_LOST_STAGE_ID,
    PIPE17_RFF_STATUS, PIPE17_HOLD_TAG, PIPE17_CONFIRM_REVIEW_TAG, PIPE17_AIRTABLE_TAG,
)

log = logging.getLogger("hubspot-pipe17")

import os
_DEBUG = os.environ.get("HOLD_SYNC_DEBUG", "false").lower() == "true"

_HOLD_STAGES = {str(s) for s in HS_HOLD_STAGE_IDS}
_RFO = str(HS_READY_FOR_OPS_STAGE_ID)
_LOST = str(HS_LOST_STAGE_ID)


def _truthy(v):
    return str(v).strip().lower() == "true"


def _num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


# --- pure helpers (unit-tested offline) ------------------------------------

def classify(deal):
    """-> "hold" | "confirmed" | "withdrawn" | "noop" from stage + checkbox."""
    props = deal.get("properties") or {}
    stage = str(props.get(HS_DEAL_STAGE_PROP) or "")
    approved = _truthy(props.get(HS_INVENTORY_HOLD_PROP))
    if stage == _RFO:
        return "confirmed"
    if stage == _LOST:            # stage supersedes the checkbox
        return "withdrawn"
    if stage in _HOLD_STAGES:
        return "hold" if approved else "withdrawn"
    return "noop"


def _dedup(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def desired_tags(action, routing_tags):
    """Tag set for the order. routing_tags already carries B2B + any shipping tags."""
    routing = list(routing_tags or [])
    if action == "hold":
        return _dedup(routing + [PIPE17_HOLD_TAG])
    if action == "confirmed":
        return _dedup([PIPE17_AIRTABLE_TAG] + routing + [PIPE17_CONFIRM_REVIEW_TAG])
    return _dedup(routing)


def line_signature(line_items):
    """Stable (sku, qty, price) signature. Works on Pipe17 order lineItems and on a
    build_order body's lineItems (both carry sku/quantity/itemPrice)."""
    sig = []
    for li in line_items or []:
        price = li.get("itemPrice")
        if price is None:
            price = li.get("price")
        sig.append((str(li.get("sku")), _num(li.get("quantity")), _num(price)))
    return tuple(sorted(sig, key=lambda t: (t[0], t[1] or 0, t[2] or 0)))


def tags_differ(existing, desired):
    return set(existing or []) != set(desired or [])


# --- reconcile one deal (Pipe17/HubSpot injected for testability) ----------

def reconcile_deal(deal, *, hs, p17w, key_map, dry_run, order_suffix=""):
    """Returns (result, ext_order_id). result is one of: created / updated / unchanged
    / canceled / would_create / would_update / would_cancel / noop / noop_released /
    nokey."""
    action = classify(deal)
    deal_id = deal.get("id")
    if action == "noop":
        return "noop", None

    currency = currency_of(deal)
    api_key = key_map.get(currency)
    if not api_key:
        log.warning("Deal %s: no Pipe17 key for currency %s — skipping.", deal_id, currency)
        return "nokey", None

    ext = ext_order_id_of(deal, currency, order_suffix)
    existing = p17w.get_order_by_ext_id(ext, api_key)

    if action in ("hold", "confirmed"):
        line_items = hs.get_line_items(deal_id)
        contact = hs.delivery_contact_of(deal_id)
        tags = desired_tags(action, transform_order.shipping_tags(line_items))
        body, _, _ = build_order(deal, line_items, contact, order_suffix,
                                 status=PIPE17_RFF_STATUS, tags=tags)
        if not existing:
            if dry_run:
                log.info("[DRY_RUN] %s: would CREATE %s (%s), tags=%s, %d items",
                         action, ext, currency, tags, len(body.get("lineItems", [])))
                return "would_create", ext
            order = p17w.create_order(body, api_key)
            hs.set_pipe17_order_id(deal_id, str(p17w.order_id_of(order) or ext))
            log.info("%s: created Pipe17 order %s for deal %s", action, ext, deal_id)
            return "created", ext

        oid = p17w.order_id_of(existing) or ext
        sig_existing = line_signature(existing.get("lineItems"))
        sig_body = line_signature(body.get("lineItems"))
        same_lines = sig_existing == sig_body
        same_tags = not tags_differ(existing.get("tags"), tags)
        if not same_lines and _DEBUG:
            log.info("[DEBUG] %s line mismatch:\n  existing=%s\n  body    =%s",
                     ext, sig_existing, sig_body)
        if not same_tags and _DEBUG:
            log.info("[DEBUG] %s tag change: existing=%s desired=%s",
                     ext, existing.get("tags"), tags)
        if same_lines and same_tags:
            return "unchanged", ext
        if dry_run:
            log.info("[DRY_RUN] %s: would UPDATE %s (lines_changed=%s tags_changed=%s)",
                     action, ext, not same_lines, not same_tags)
            return "would_update", ext
        p17w.update_order(oid, body, api_key)
        hs.set_pipe17_order_id(deal_id, str(oid))
        log.info("%s: updated Pipe17 order %s for deal %s", action, ext, deal_id)
        return "updated", ext

    # action == "withdrawn": cancel only a still-held order (pre-release).
    if not existing:
        return "noop", ext
    if PIPE17_HOLD_TAG not in (existing.get("tags") or []):
        return "noop_released", ext   # already released/confirmed — leave it alone
    oid = p17w.order_id_of(existing) or ext
    if dry_run:
        log.info("[DRY_RUN] withdrawn: would CANCEL held order %s (deal %s)", ext, deal_id)
        return "would_cancel", ext
    p17w.cancel_order(oid, api_key)
    log.info("withdrawn: cancelled held order %s for deal %s", ext, deal_id)
    return "canceled", ext


def run(dry_run, deal_id=None, order_suffix=""):
    """Entry point used by main_hubspot_to_pipe17 when INVENTORY_HOLD_ENABLED."""
    import hubspot_client as hs
    import pipe17_write as p17w
    from config import HS_LOOKBACK_MINUTES, CURRENCY_API_KEY_MAP

    if deal_id:
        deals = [hs.get_deal(deal_id)]
    else:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=HS_LOOKBACK_MINUTES)
        deals = hs.find_hold_sync_deals(int(since.timestamp() * 1000))

    counts = {}
    for deal in deals:
        try:
            result, _ = reconcile_deal(deal, hs=hs, p17w=p17w,
                                       key_map=CURRENCY_API_KEY_MAP, dry_run=dry_run,
                                       order_suffix=order_suffix)
        except Exception:
            log.exception("Inventory-hold reconcile failed for deal %s", deal.get("id"))
            result = "failed"
        counts[result] = counts.get(result, 0) + 1
    log.info("Inventory-hold sync done (%s): %s", "DRY_RUN" if dry_run else "LIVE", counts)
    if counts.get("failed"):
        raise RuntimeError(f"inventory-hold reconcile had {counts['failed']} failures")
    return counts


def _arg(name):
    import sys
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


if __name__ == "__main__":
    # Standalone dry-run entrypoint so the hold path can be exercised WITHOUT touching
    # INVENTORY_HOLD_ENABLED (which only gates the path inside main_hubspot_to_pipe17).
    # DRY_RUN defaults to "true", so this reads live HubSpot + Pipe17 but writes nothing;
    # it logs the would-CREATE / would-UPDATE / would-CANCEL decisions per deal.
    #   python hold_sync.py --deal-id 65791268757     # one deal (recommended first test)
    #   python hold_sync.py                           # all in-window hold/RFO/lost deals
    # To actually write (don't, until Ops automations exist): DRY_RUN=false python hold_sync.py --deal-id ...
    import logging as _logging
    from config import DRY_RUN
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("hold_sync standalone | mode=%s", "DRY_RUN" if DRY_RUN else "LIVE")
    run(DRY_RUN, deal_id=_arg("--deal-id"), order_suffix=_arg("--order-suffix") or "")
