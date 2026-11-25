"""Inventory-hold / committed-stage reconcile for Track B.

One Pipe17 order per deal across its lifecycle (extOrderId = #BE/#CEN<dealId>),
reconciled to the deal's current shape. Per deal, classify by stage (+ the
inventory_hold_approved checkbox for the optional early hold):

  awaiting_payment  stage in COMMITTED_STAGES (Committed 90% / Deposit Invoice Sent 95%)
                    -> order at readyForFulfillment, tags B2BInventoryHold + "Awaiting
                       Payment" (no Airtable), note "Proposal signed … Do not process."
                       Ingested automatically — no checkbox needed — so inventory is
                       locked while payment is pending.
  hold              stage in HOLD_STAGES (earlier) and checkbox set
                    -> same shape as awaiting_payment but with the DO-NOT-RELEASE note
                       and no "Awaiting Payment" label (the optional sales-requested
                       pre-signature reservation).
  confirmed         stage == Ready for Ops (100%)
                    -> order at readyForFulfillment, tags Airtable + B2BConfirmReview +
                       routing, managed note cleared (released — Ops now processes).
  withdrawn         checkbox cleared in an early hold stage -> cancel the order IF it is
                    still a hold (pre-release).
  closed_lost       stage == Closed lost -> cancel the deal's order(s) so inventory
                    restocks, UNLESS a shipping request is in flight (then alert Ops and
                    leave it). Gated by CLOSED_LOST_CANCEL_ENABLED; off = legacy behavior
                    (cancel a still-held single order only).
  noop              anything else

All reserving shapes (awaiting_payment / hold / confirmed) commit inventory and generate
shipping requests; the B2BInventoryHold tag + an Ops tag-keyed automation park the SRs in
Review Required until Ready for Ops. B2BInventoryHold stays the functional guard for
committed orders so no new Ops automation is needed.

Update is signature-gated: an existing order is PUT only when its line items
(sorted sku+qty+price), tags, status, managed note or tax actually differ, so we don't
churn on every deal edit and never create a second order for a deal.

reconcile_deal takes hs / p17w / key_map injected so selftest can stub them.
"""
import datetime as dt
import logging
import os

import transform_order
import notify
from transform_order import build_order, currency_of, ext_order_id_of
from config import (
    HS_DEAL_STAGE_PROP, HS_INVENTORY_HOLD_PROP,
    HS_HOLD_STAGE_IDS, HS_COMMITTED_STAGE_IDS,
    HS_READY_FOR_OPS_STAGE_ID, HS_LOST_STAGE_ID,
    PIPE17_RFF_STATUS, PIPE17_HOLD_TAG, PIPE17_CONFIRM_REVIEW_TAG, PIPE17_AIRTABLE_TAG,
    PIPE17_AWAITING_PAYMENT_TAG, PIPE17_AWAITING_PAYMENT_NOTE,
    PIPE17_HOLD_NOTE, PIPE17_HOLD_NOTE_FIELD,
    PIPE17_HOLD_CF_NAME, PIPE17_HOLD_CF_HELD, PIPE17_HOLD_CF_RELEASED,
    HOLD_RFO_SLACK_ALERT,
    CLOSED_LOST_CANCEL_ENABLED, CANCEL_INFLIGHT_BLOCK_STATUSES, HS_CANCEL_RESULT_PROP,
    PIPE17_CANCELED_STATUS, PIPE17_ORDER_PREFIX_MAP,
)

log = logging.getLogger("hubspot-pipe17")

_DEBUG = os.environ.get("HOLD_SYNC_DEBUG", "false").lower() == "true"

_HOLD_STAGES = {str(s) for s in HS_HOLD_STAGE_IDS}
_COMMITTED_STAGES = {str(s) for s in HS_COMMITTED_STAGE_IDS}
_RFO = str(HS_READY_FOR_OPS_STAGE_ID)
_LOST = str(HS_LOST_STAGE_ID)

# States that reserve inventory on a readyForFulfillment order.
_RESERVING = ("awaiting_payment", "hold", "confirmed")


def _truthy(v):
    return str(v).strip().lower() == "true"


def _num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def _money(v):
    """A monetary value as a 2dp float (missing/blank -> 0.0), for stable comparison."""
    n = _num(v)
    return round(float(n), 2) if n is not None else 0.0


# --- pure helpers (unit-tested offline) ------------------------------------

def classify(deal):
    """-> "awaiting_payment" | "hold" | "confirmed" | "withdrawn" | "closed_lost" | "noop".

    Committed/quote-signed stages are checked BEFORE the hold stages, so a committed deal
    ingests automatically whether or not the inventory_hold_approved checkbox is set."""
    props = deal.get("properties") or {}
    stage = str(props.get(HS_DEAL_STAGE_PROP) or "")
    approved = _truthy(props.get(HS_INVENTORY_HOLD_PROP))
    if stage == _RFO:
        return "confirmed"
    if stage == _LOST:                 # stage supersedes the checkbox
        return "closed_lost"
    if stage in _COMMITTED_STAGES:     # auto-ingest, checkbox irrelevant
        return "awaiting_payment"
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
    """Tag set for a NEW order. routing_tags already carries B2B + any shipping tags."""
    routing = list(routing_tags or [])
    if action == "awaiting_payment":
        return _dedup(routing + [PIPE17_HOLD_TAG, PIPE17_AWAITING_PAYMENT_TAG])
    if action == "hold":
        return _dedup(routing + [PIPE17_HOLD_TAG])
    if action == "confirmed":
        return _dedup([PIPE17_AIRTABLE_TAG] + routing + [PIPE17_CONFIRM_REVIEW_TAG])
    return _dedup(routing)


# Tags this reconcile owns and sets per state. Every other tag on an existing order
# (freight tags like LTL, and anything Ops or a Pipe17 automation added) is preserved.
_MANAGED_TAGS = (PIPE17_HOLD_TAG, PIPE17_CONFIRM_REVIEW_TAG, PIPE17_AIRTABLE_TAG,
                 PIPE17_AWAITING_PAYMENT_TAG)

# Notes this reconcile owns. On release (confirmed) any of these present on the order is
# stripped, so a released order never carries a do-not-process/do-not-release note; Ops
# edits to the note field survive.
_MANAGED_NOTES = tuple(n for n in (PIPE17_HOLD_NOTE, PIPE17_AWAITING_PAYMENT_NOTE) if n)


def merge_tags(existing_tags, action):
    """Desired tags for an EXISTING order: keep every non-managed tag it already has,
    then set only our managed tags for this state. awaiting_payment -> +HOLD +Awaiting
    Payment; hold -> +HOLD; confirmed -> +Airtable +ConfirmReview (released, hold/awaiting
    dropped). This never strips LTL or Ops-added tags, which a from-scratch rebuild would."""
    kept = [t for t in (existing_tags or []) if t not in _MANAGED_TAGS]
    if action == "awaiting_payment":
        add = [PIPE17_HOLD_TAG, PIPE17_AWAITING_PAYMENT_TAG]
    elif action == "hold":
        add = [PIPE17_HOLD_TAG]
    elif action == "confirmed":
        add = [PIPE17_AIRTABLE_TAG, PIPE17_CONFIRM_REVIEW_TAG]
    else:
        add = []
    return _dedup(kept + add)


def _note_for(action):
    """The managed note a reserving create/update should carry (or None)."""
    if action == "awaiting_payment":
        return PIPE17_AWAITING_PAYMENT_NOTE
    if action == "hold":
        return PIPE17_HOLD_NOTE
    return None   # confirmed / released carries no new note


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


def _set_cf(custom_fields, name, value):
    """Upsert one {name,value} into a Pipe17 customFields list, preserving the rest
    (notably hubspot_deal_id). Returns a new list."""
    out = [dict(c) for c in (custom_fields or []) if c.get("name") != name]
    out.append({"name": name, "value": value})
    return out


# --- reconcile one deal (Pipe17/HubSpot injected for testability) ----------

def reconcile_deal(deal, *, hs, p17w, key_map, dry_run, order_suffix=""):
    """Returns (result, ext_order_id). result is one of: created / updated / unchanged /
    canceled / canceled_partial / blocked_inflight / would_create / would_update /
    would_cancel / would_alert / noop / noop_released / nokey."""
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

    if action in _RESERVING:
        line_items = hs.get_line_items(deal_id)
        contact = hs.delivery_contact_of(deal_id)
        tags = desired_tags(action, transform_order.shipping_tags(line_items))
        note = _note_for(action)   # awaiting-payment / do-not-release note, or None on release
        body, _, _ = build_order(deal, line_items, contact, order_suffix,
                                 status=PIPE17_RFF_STATUS, tags=tags, note=note)
        # Reportable hold flag as a custom field (held while reserving, released at RFO),
        # alongside hubspot_deal_id which build_order already set.
        cf_value = PIPE17_HOLD_CF_RELEASED if action == "confirmed" else PIPE17_HOLD_CF_HELD
        if PIPE17_HOLD_CF_NAME:
            body["customFields"] = _set_cf(body.get("customFields"), PIPE17_HOLD_CF_NAME, cf_value)

        if not existing:
            if dry_run:
                log.info("[DRY_RUN] %s: would CREATE %s (%s), tags=%s, %d items",
                         action, ext, currency, tags, len(body.get("lineItems", [])))
                return "would_create", ext
            order = p17w.create_order(body, api_key)
            hs.set_pipe17_order_id(deal_id, str(p17w.order_id_of(order) or ext))
            log.info("%s: created Pipe17 order %s for deal %s", action, ext, deal_id)
            return "created", ext

        # Dedupe: an order already exists for this deal — reconcile it in place, never
        # create a second one.
        oid = p17w.order_id_of(existing) or ext
        was_held = PIPE17_HOLD_TAG in (existing.get("tags") or [])
        # Preserve the existing order's non-managed tags (LTL, Ops/automation tags);
        # only flip our own state tags. Never rebuild the tag list from scratch.
        desired = merge_tags(existing.get("tags"), action)
        sig_existing = line_signature(existing.get("lineItems"))
        sig_body = line_signature(body.get("lineItems"))
        same_lines = sig_existing == sig_body
        same_tags = not tags_differ(existing.get("tags"), desired)
        same_status = str(existing.get("status") or "") == PIPE17_RFF_STATUS
        # Tax: the deal's b2b_deal_tax lands on the order as orderTax and rolls into
        # totalPrice (build_order already set both). Resend the price trio when tax or the
        # line set changes, so the order's total stays correct as tax is populated or
        # revised by the external workflow before the order is released.
        tax_changed = _money(existing.get("orderTax")) != _money(body.get("orderTax"))
        pricing_changed = tax_changed or not same_lines
        # Managed note: on release (confirmed) strip any managed note that's present; while
        # reserving, set the note when it differs from the one this state should carry.
        new_note, note_change = _reconcile_note(action, existing)
        if not same_lines and _DEBUG:
            log.info("[DEBUG] %s line mismatch:\n  existing=%s\n  body    =%s",
                     ext, sig_existing, sig_body)
        if not same_tags and _DEBUG:
            log.info("[DEBUG] %s tag change: existing=%s desired=%s",
                     ext, existing.get("tags"), desired)
        if same_lines and same_tags and same_status and not note_change and not tax_changed:
            return "unchanged", ext
        if dry_run:
            log.info("[DRY_RUN] %s: would UPDATE %s (lines_changed=%s tags_changed=%s status_changed=%s note_changed=%s tax_changed=%s)",
                     action, ext, not same_lines, not same_tags, not same_status, note_change, tax_changed)
            return "would_update", ext
        # Minimal PUT: only the fields we own. Leave shippingAddress / customer alone so
        # Ops edits survive. lineItems only when changed; note only when it changed.
        update_body = {"status": PIPE17_RFF_STATUS, "tags": desired}
        if not same_lines:
            update_body["lineItems"] = body.get("lineItems")
        if PIPE17_HOLD_CF_NAME:
            update_body["customFields"] = body.get("customFields")
        if note_change:
            update_body[PIPE17_HOLD_NOTE_FIELD] = new_note   # "" clears the note
        if pricing_changed:
            # Keep subtotal / tax / total coherent. orderTax defaults to 0 so a cleared
            # b2b_deal_tax zeroes the order's tax rather than leaving a stale value.
            update_body["subTotalPrice"] = body.get("subTotalPrice")
            update_body["totalPrice"] = body.get("totalPrice")
            update_body["orderTax"] = body.get("orderTax", 0)
        p17w.update_order(oid, update_body, api_key)
        hs.set_pipe17_order_id(deal_id, str(oid))
        # Sales-Ops alert on the held/awaiting -> Ready for Ops flip (an order that WAS a
        # hold and is now confirmed), so they review and release its shipping requests.
        if action == "confirmed" and was_held and HOLD_RFO_SLACK_ALERT:
            notify.slack(f":inbox_tray: Held order *{ext}* (deal {deal_id}) is now "
                         f"*Ready for Ops* — review and release its shipping requests.")
        log.info("%s: updated Pipe17 order %s for deal %s", action, ext, deal_id)
        return "updated", ext

    if action == "closed_lost":
        return _closed_lost(deal_id, currency, api_key, ext, existing, hs, p17w, dry_run)

    # action == "withdrawn": checkbox cleared in an early hold stage. Legacy narrow
    # cancel — only a still-held order (pre-release).
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


def _reconcile_note(action, existing):
    """(new_note, note_change) for the update path. On release, strip any managed note;
    while reserving, set this state's note when the order doesn't already carry it."""
    if not PIPE17_HOLD_NOTE_FIELD:
        return "", False
    existing_note = existing.get(PIPE17_HOLD_NOTE_FIELD) or ""
    if action == "confirmed":
        new_note = existing_note
        for mn in _MANAGED_NOTES:
            if mn and mn in new_note:
                new_note = new_note.replace(mn, "").strip()
        return new_note, (new_note != existing_note)
    desired_note = _note_for(action)
    if desired_note and existing_note != desired_note:
        return desired_note, True
    return existing_note, False


def _closed_lost(deal_id, currency, api_key, ext, existing, hs, p17w, dry_run):
    """Deal went closed lost: cancel its Pipe17 order(s) so inventory restocks, unless a
    shipping request is in flight (then leave it and alert Ops). Idempotent (skips orders
    already canceled). Writes the result back to the deal. Gated by CLOSED_LOST_CANCEL_ENABLED."""
    if not CLOSED_LOST_CANCEL_ENABLED:
        # Legacy behavior: cancel a still-held single order only.
        if not existing:
            return "noop", ext
        if PIPE17_HOLD_TAG not in (existing.get("tags") or []):
            return "noop_released", ext
        if dry_run:
            log.info("[DRY_RUN] closed_lost(legacy): would CANCEL held order %s (deal %s)", ext, deal_id)
            return "would_cancel", ext
        p17w.cancel_order(p17w.order_id_of(existing) or ext, api_key)
        log.info("closed_lost(legacy): cancelled held order %s for deal %s", ext, deal_id)
        return "canceled", ext

    prefix = PIPE17_ORDER_PREFIX_MAP.get(currency, "")
    orders = list(p17w.find_orders_for_deal(deal_id, prefix, api_key) or [])
    # Make sure the exact base order is included even if discovery missed it.
    base_key = p17w.order_id_of(existing) or ext if existing else None
    if existing and not any((p17w.order_id_of(o) or o.get("extOrderId")) == base_key for o in orders):
        orders.append(existing)
    if not orders:
        log.info("closed_lost: deal %s has no Pipe17 order — nothing to cancel.", deal_id)
        return "noop", ext

    to_cancel, inflight = [], []
    for o in orders:
        if str(o.get("status") or "") == PIPE17_CANCELED_STATUS:
            continue   # idempotent: already canceled
        o_ext = o.get("extOrderId") or ext
        srs = p17w.get_order_shipments(o_ext, api_key) or []
        blocking = [s for s in srs if str(s.get("status") or "") in CANCEL_INFLIGHT_BLOCK_STATUSES]
        (inflight if blocking else to_cancel).append((o, blocking))

    if not to_cancel and not inflight:
        return "noop", ext   # every order already canceled

    if dry_run:
        log.info("[DRY_RUN] closed_lost deal %s: would CANCEL %d order(s), ALERT on %d in-flight",
                 deal_id, len(to_cancel), len(inflight))
        return ("would_cancel" if to_cancel else "would_alert"), ext

    canceled_exts = []
    for o, _ in to_cancel:
        oid = p17w.order_id_of(o) or o.get("extOrderId")
        p17w.cancel_order(oid, api_key)
        canceled_exts.append(o.get("extOrderId") or oid)
        log.info("closed_lost: cancelled %s (deal %s)", o.get("extOrderId") or oid, deal_id)

    if inflight:
        detail = "; ".join(
            f"{o.get('extOrderId')} ["
            + ", ".join(f"{s.get('shipmentId') or s.get('extShipmentId') or '?'}:{s.get('status')}"
                        for s in bl)
            + "]"
            for o, bl in inflight)
        notify.slack(
            f":warning: Deal {deal_id} closed lost but has shipment(s) IN FLIGHT — "
            f"Pipe17 order(s) NOT cancelled: {detail}. Review/cancel manually in Pipe17.")
        log.warning("closed_lost: deal %s left alone — shipments in flight: %s", deal_id, detail)

    _writeback_cancel(hs, deal_id, canceled_exts, inflight)

    if inflight and canceled_exts:
        return "canceled_partial", ext
    if inflight:
        return "blocked_inflight", ext
    return "canceled", ext


def _writeback_cancel(hs, deal_id, canceled_exts, inflight):
    """Record the closed-lost cancellation outcome on the HubSpot deal (HS_CANCEL_RESULT_PROP).
    No-op when the property isn't configured. Never raises — a writeback failure (e.g. the
    property doesn't exist yet) must not fail the sweep."""
    if not HS_CANCEL_RESULT_PROP:
        return
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = []
    if canceled_exts:
        parts.append(f"Pipe17 order(s) {', '.join(canceled_exts)} canceled (deal closed lost)")
    if inflight:
        exts = ", ".join((o.get("extOrderId") or "?") for o, _ in inflight)
        parts.append(f"Pipe17 order(s) {exts} NOT canceled — shipment in flight")
    if not parts:
        return
    msg = f"{'; '.join(parts)} — {ts}"
    try:
        hs.update_deal(deal_id, {HS_CANCEL_RESULT_PROP: msg})
    except Exception:
        log.exception("closed_lost: HubSpot writeback failed for deal %s (prop %s)",
                      deal_id, HS_CANCEL_RESULT_PROP)


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
    # Standalone dry-run entrypoint so the path can be exercised WITHOUT touching
    # INVENTORY_HOLD_ENABLED (which only gates the path inside main_hubspot_to_pipe17).
    # DRY_RUN defaults to "true", so this reads live HubSpot + Pipe17 but writes nothing;
    # it logs the would-CREATE / would-UPDATE / would-CANCEL decisions per deal.
    #   python hold_sync.py --deal-id 65791268757     # one deal (recommended first test)
    #   python hold_sync.py                           # all in-window hold/committed/RFO/lost deals
    # To actually write (don't, until Ops automations exist): DRY_RUN=false python hold_sync.py --deal-id ...
    import logging as _logging
    from config import DRY_RUN
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("hold_sync standalone | mode=%s", "DRY_RUN" if DRY_RUN else "LIVE")
    run(DRY_RUN, deal_id=_arg("--deal-id"), order_suffix=_arg("--order-suffix") or "")
