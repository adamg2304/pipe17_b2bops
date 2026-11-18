"""Airtable Orders Status -> HubSpot deal `operations_status`.

Replaces the Zapier zap "Catch Airtable Order Updates and Push to Hubspot" and the
Airtable automation that fed it ("Sync Order Status Update to Hubspot"). The zap moved the
deal through HubSpot Operations Pipeline stages; that pipeline is being retired, so the
order's status now lands on the deal property `operations_status` and the deal stays in
its sales pipeline.

How a run works:
  1. Read Airtable Orders whose Status changed inside the window (the Orders
     "Last Modified" field watches Status).
  2. Per order: skip the orders the old automation skipped (test numbers #77/#99,
     duplicate / replacement / claim tags, and rows already Completed-dated but not in
     Completed status). Resolve the HubSpot deal id. Work out the target values.
  3. Read those deals' current values in one batch and PATCH only what differs, so a run
     that finds nothing new writes nothing.

Status mapping: the Airtable Status is written as-is when it matches an operations_status
option (case-insensitive). Otherwise OPS_STATUS_ALIASES maps it. A status that matches
neither leaves the deal untouched and is logged, so adding the option in HubSpot is all it
takes to start syncing it. A rejected delivery date reports OPS_STATUS_DATE_REJECTED when
HubSpot has that option.

With OPS_STATUS_SYNC_EXTRAS, the delivery fields the zap wrote alongside the stage ride
along: delivery_date, the friendly scheduled date, and the LateShipment tracking link.
Only non-empty Airtable values are written.

run() takes at / hs injected so the selftest runs offline.
"""
import logging
import re

from config import (
    ORDERS_TABLE, O_ORDER_NUMBER, O_STATUS, O_ORDER_TAGS, O_HUBSPOT_DEAL_LINK,
    O_HS_DEAL_ID_LOOKUP, O_DELIVERY_DATE_APPROVED, O_SCHEDULED_DELIVERY_DATE,
    O_SCHEDULED_DELIVERY_FRIENDLY, O_LATESHIPMENT_LINK, O_COMPLETED_DATE,
    O_STATUS_CHANGED_NAME, PIPE17_ORDER_PREFIX_MAP,
    HS_OPS_STATUS_PROP, OPS_STATUS_ALIASES, OPS_STATUS_DATE_REJECTED,
    OPS_STATUS_SYNC_EXTRAS, HS_DELIVERY_DATE_PROP, HS_DELIVERY_FRIENDLY_PROP,
    HS_LATESHIPMENT_PROP,
    OPS_STATUS_SKIP_ORDER_SUBSTRINGS, OPS_STATUS_SKIP_TAG_PREFIXES, OPS_STATUS_SKIP_TAGS,
)

log = logging.getLogger("pipe17-airtable")

COMPLETED = "Completed"
REJECTED = "Rejected"

READ_FIELDS = [
    O_ORDER_NUMBER, O_STATUS, O_ORDER_TAGS, O_HUBSPOT_DEAL_LINK, O_HS_DEAL_ID_LOOKUP,
    O_DELIVERY_DATE_APPROVED, O_SCHEDULED_DELIVERY_DATE, O_SCHEDULED_DELIVERY_FRIENDLY,
    O_LATESHIPMENT_LINK, O_COMPLETED_DATE,
]

_DEAL_LINK_RE = re.compile(r"/record/0-3/(\d+)/?$")
# HubSpot deal ids are long (11 digits today). Shopify-era order numbers like #BE15603
# share the #BE prefix but are not deal ids, so a short number never counts as one.
_MIN_DEAL_ID_LEN = 9


def _text(v):
    """Airtable cell -> plain string (selects arrive as strings, lookups as lists)."""
    if isinstance(v, list):
        v = next((x for x in v if x not in (None, "")), None)
    if isinstance(v, dict):
        v = v.get("name") or v.get("value")
    return str(v).strip() if v not in (None, "") else ""


# --- pure helpers (unit-tested offline) ------------------------------------

def deal_id_of(fields):
    """HubSpot deal id for an Orders row: the Hubspot Deals lookup first, then the
    Hubspot Deal Link Track A writes, then the digits after a #BE/#CEN prefix."""
    lookup = _text(fields.get(O_HS_DEAL_ID_LOOKUP))
    if lookup.isdigit() and len(lookup) >= _MIN_DEAL_ID_LEN:
        return lookup
    m = _DEAL_LINK_RE.search(_text(fields.get(O_HUBSPOT_DEAL_LINK)))
    if m and len(m.group(1)) >= _MIN_DEAL_ID_LEN:
        return m.group(1)
    num = _text(fields.get(O_ORDER_NUMBER))
    for prefix in sorted((p for p in PIPE17_ORDER_PREFIX_MAP.values() if p), key=len, reverse=True):
        rest = num[len(prefix):] if num.startswith(prefix) else ""
        if rest.isdigit() and len(rest) >= _MIN_DEAL_ID_LEN:
            return rest
    return None


def skip_reason(fields):
    """Why the old automation would not have sent this order, or None."""
    num = _text(fields.get(O_ORDER_NUMBER))
    for s in OPS_STATUS_SKIP_ORDER_SUBSTRINGS:
        if s in num:
            return "test order"
    for tag in fields.get(O_ORDER_TAGS) or []:
        t = _text(tag).upper()
        if t in OPS_STATUS_SKIP_TAGS or any(t.startswith(p) for p in OPS_STATUS_SKIP_TAG_PREFIXES):
            return "duplicate/replacement tag"
    status = _text(fields.get(O_STATUS))
    if not status:
        return "no status"
    if fields.get(O_COMPLETED_DATE) and status != COMPLETED:
        return "completed-dated"
    return None


def map_status(fields, options):
    """Target operations_status for an Orders row, or None if no HubSpot option fits."""
    by_lower = {str(o).lower(): o for o in options}
    if _text(fields.get(O_DELIVERY_DATE_APPROVED)) == REJECTED:
        hit = by_lower.get(OPS_STATUS_DATE_REJECTED.lower())
        if hit:
            return hit
    status = _text(fields.get(O_STATUS))
    if not status:
        return None
    hit = by_lower.get(status.lower())
    if hit:
        return hit
    alias = OPS_STATUS_ALIASES.get(status)
    if alias:
        return by_lower.get(str(alias).lower())
    return None


def desired_props(fields, options):
    """HubSpot properties this Orders row wants on its deal (blank values omitted)."""
    out = {}
    status = map_status(fields, options)
    if status:
        out[HS_OPS_STATUS_PROP] = status
    if OPS_STATUS_SYNC_EXTRAS:
        date = _text(fields.get(O_SCHEDULED_DELIVERY_DATE))[:10]
        if date:
            out[HS_DELIVERY_DATE_PROP] = date
        friendly = _text(fields.get(O_SCHEDULED_DELIVERY_FRIENDLY))
        if friendly:
            out[HS_DELIVERY_FRIENDLY_PROP] = friendly
        link = _text(fields.get(O_LATESHIPMENT_LINK))
        if link:
            out[HS_LATESHIPMENT_PROP] = link
    return out


def _norm(prop, v):
    s = "" if v is None else str(v).strip()
    if prop == HS_DELIVERY_DATE_PROP:
        return s[:10]   # HubSpot may return a date as YYYY-MM-DD or a midnight timestamp
    return s


def changes(desired, current):
    """The subset of `desired` that differs from the deal's current values."""
    current = current or {}
    return {k: v for k, v in desired.items() if _norm(k, v) != _norm(k, current.get(k))}


def is_main_order(fields):
    """True for a parent order number (no split suffix like '-2' or ' (A)')."""
    num = _text(fields.get(O_ORDER_NUMBER))
    return bool(num) and "-" not in num and "(" not in num


# --- run --------------------------------------------------------------------

def window_formula(minutes):
    return "IS_AFTER({%s}, DATEADD(NOW(), -%d, 'minutes'))" % (O_STATUS_CHANGED_NAME, int(minutes))


def run(lookback_minutes, dry_run, *, at=None, hs=None):
    """Push recently changed Airtable order statuses to HubSpot. Returns {result: count}."""
    if at is None:
        import airtable_client as at
    if hs is None:
        import hubspot_client as hs

    rows = at.list_records(ORDERS_TABLE, window_formula(lookback_minutes), field_ids=READ_FIELDS)
    options = hs.get_property_options("deals", HS_OPS_STATUS_PROP)
    counts = {}

    def bump(k):
        counts[k] = counts.get(k, 0) + 1

    # One target per deal. Split orders share their parent's deal; when both changed in
    # the window, the parent row wins so a child can't overwrite the parent's status.
    plan = {}
    unmapped = set()
    for rec in rows:
        f = rec.get("fields") or {}
        num = _text(f.get(O_ORDER_NUMBER))
        reason = skip_reason(f)
        if reason:
            bump("skipped")
            continue
        deal_id = deal_id_of(f)
        if not deal_id:
            bump("no_deal_id")
            log.warning("Ops status: no HubSpot deal id for order %s", num)
            continue
        want = desired_props(f, options)
        if HS_OPS_STATUS_PROP not in want:
            unmapped.add(_text(f.get(O_STATUS)))
        if not want:
            bump("unmapped")
            continue
        prev = plan.get(deal_id)
        if prev and is_main_order(prev[1]) and not is_main_order(f):
            continue
        plan[deal_id] = (num, f, want)
    if unmapped:
        log.warning("Ops status: Airtable status(es) with no %s option, left unchanged on the "
                    "deal: %s", HS_OPS_STATUS_PROP, ", ".join(sorted(s for s in unmapped if s)))

    props = sorted({k for _, _, want in plan.values() for k in want})
    current = hs.batch_read_deals(list(plan), props) if plan else {}
    for deal_id, (num, _, want) in plan.items():
        if deal_id not in current:
            bump("deal_not_found")
            log.warning("Ops status: deal %s (order %s) not found in HubSpot", deal_id, num)
            continue
        diff = changes(want, current[deal_id])
        if not diff:
            bump("unchanged")
            continue
        if dry_run:
            log.info("[DRY_RUN] ops status: deal %s (order %s) <- %s", deal_id, num, diff)
            bump("would_update")
            continue
        try:
            hs.update_deal(deal_id, diff)
            bump("updated")
            log.info("Ops status: deal %s (order %s) <- %s", deal_id, num, diff)
        except Exception:
            bump("failed")
            log.exception("Ops status: update failed for deal %s (order %s)", deal_id, num)
    log.info("Ops status sync (%s, %d orders in window): %s",
             "DRY_RUN" if dry_run else "LIVE", len(rows), counts)
    return counts
