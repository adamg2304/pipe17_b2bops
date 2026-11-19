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
  3. A deal can have several orders: splits ("#BE15501 - Small Filing Cabinets"),
     suffixed orders ("CEN1687.1") and the parent. For every deal touched, all of its
     orders are read (not just the ones that changed) and the deal gets the
     LEAST-FINISHED status among them (OPS_STATUS_PROGRESS). Cancelled orders don't count
     unless every order is cancelled. So a deal reads Completed only when all of its
     orders are, and the result never depends on which order happened to change.
  4. Read those deals' current values in one batch and PATCH only what differs, so a run
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
from collections import defaultdict

from config import (
    ORDERS_TABLE, O_ORDER_NUMBER, O_STATUS, O_ORDER_TAGS, O_HUBSPOT_DEAL_LINK,
    O_HS_DEAL_ID_LOOKUP, O_DELIVERY_DATE_APPROVED, O_SCHEDULED_DELIVERY_DATE,
    O_SCHEDULED_DELIVERY_FRIENDLY, O_LATESHIPMENT_LINK, O_COMPLETED_DATE,
    O_STATUS_CHANGED_NAME, PIPE17_ORDER_PREFIX_MAP,
    HS_OPS_STATUS_PROP, OPS_STATUS_ALIASES, OPS_STATUS_DATE_REJECTED,
    OPS_STATUS_SYNC_EXTRAS, HS_DELIVERY_DATE_PROP, HS_DELIVERY_FRIENDLY_PROP,
    HS_LATESHIPMENT_PROP, HS_ORDER_DETAILS_PROP as HS_ORDER_NUMBER_PROP,
    OPS_STATUS_SKIP_ORDER_SUBSTRINGS, OPS_STATUS_SKIP_TAG_PREFIXES, OPS_STATUS_SKIP_TAGS,
    OPS_STATUS_PROGRESS, OPS_STATUS_IGNORE, ORDER_NUMBER_FIELD,
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


def main_order_number(fields):
    """The parent order number, as the Orders "Main Order Number" formula computes it:
    everything before the first "(" or "-", trimmed ("#BE15399 - Privacy Panels" ->
    "#BE15399")."""
    num = _text(fields.get(O_ORDER_NUMBER))
    cut = min([i for i in (num.find("("), num.find("-")) if i >= 0] or [len(num)])
    return num[:cut].strip()


def order_number_candidates(fields):
    """Values to look up on the deal's Shopify Order Number (order_details): the main
    order number, plus a '#'-prefixed variant when Airtable dropped the '#'."""
    main = main_order_number(fields)
    if not main:
        return []
    return [main] if main.startswith("#") else [main, "#" + main]


_FAMILY_CUT = re.compile(r"[(\-.\s]")
_RANK = {v.lower(): i for i, v in enumerate(OPS_STATUS_PROGRESS)}
_IGNORE = {v.lower() for v in OPS_STATUS_IGNORE}


def family_key(fields):
    """Key shared by a parent order and its splits / suffixed follow-ons: the number
    without '#', cut at the first "(", "-", "." or space, upper-cased.
    "#BE15501 - Small Filing Cabinets", "#BE15501" -> "BE15501"; "CEN1687.1",
    "CEN1687", "#CEN1687" -> "CEN1687". "_REPL" / "_S" orders keep their suffix: they
    are separate deals."""
    num = _text(fields.get(O_ORDER_NUMBER)).upper().lstrip("#").strip()
    return _FAMILY_CUT.split(num, 1)[0].strip() if num else ""


def pick_deal_status(orders, options):
    """(chosen order fields, desired props) for one deal from all of its orders, or
    (None, {}). The deal takes the least-finished mapped status; cancelled orders count
    only when nothing else is left. Ties go to the parent order. Delivery extras come
    from the chosen order, falling back to the parent and then the other orders."""
    mapped = [(map_status(f, options), f) for f in orders]
    live = [(st, f) for st, f in mapped if st and st.lower() not in _IGNORE]
    ranked = [(st, f) for st, f in live if st.lower() in _RANK]
    pool = ranked or live or [(st, f) for st, f in mapped if st]
    if not pool:
        chosen = None
    else:
        def key(item):
            st, f = item
            return (_RANK.get(st.lower(), len(_RANK)), 0 if is_main_order(f) else 1)
        chosen = min(pool, key=key)[1]
    order = ([chosen] if chosen else []) + sorted(
        (f for f in orders if f is not chosen), key=lambda f: 0 if is_main_order(f) else 1)
    want = {}
    for f in order:
        for k, v in desired_props(f, options).items():
            if k == HS_OPS_STATUS_PROP and f is not chosen:
                continue
            want.setdefault(k, v)
    return chosen, want


def _family_formula(keys):
    clauses = ['FIND("%s", UPPER({%s}))' % (k, ORDER_NUMBER_FIELD) for k in keys]
    return "OR(%s)" % ",".join(clauses)


def fetch_families(at, keys, chunk=40):
    """All Orders rows whose family_key is in `keys` (FIND narrows server-side, the exact
    family match happens here)."""
    keys = sorted(k for k in keys if k and re.fullmatch(r"[A-Z0-9_]+", k))
    out = []
    for i in range(0, len(keys), chunk):
        part = keys[i:i + chunk]
        want = set(part)
        for rec in at.list_records(ORDERS_TABLE, _family_formula(part), field_ids=READ_FIELDS):
            if family_key(rec.get("fields") or {}) in want:
                out.append(rec)
    return out


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

    # Resolve each order to a deal. Rows with no deal id on the Airtable side (older
    # Shopify-era orders, splits, replacements) fall back to the old zap's lookup: the
    # deal whose Shopify Order Number (order_details) equals the main order number.
    resolved, missing = [], []
    for rec in rows:
        f = rec.get("fields") or {}
        if skip_reason(f):
            bump("skipped")
            continue
        deal_id = deal_id_of(f)
        (resolved if deal_id else missing).append((deal_id, rec.get("id"), f))
    if missing:
        wanted = {c for _, _, f in missing for c in order_number_candidates(f)}
        try:
            by_number = hs.find_deal_ids_by_property(HS_ORDER_NUMBER_PROP, wanted) if wanted else {}
        except Exception:
            log.exception("Ops status: deal lookup by %s failed", HS_ORDER_NUMBER_PROP)
            by_number = {}
        unresolved = []
        for _, rid, f in missing:
            deal_id = next((by_number[c] for c in order_number_candidates(f) if c in by_number), None)
            if deal_id:
                bump("found_by_order_number")
                resolved.append((deal_id, rid, f))
            else:
                unresolved.append(_text(f.get(O_ORDER_NUMBER)))
        if unresolved:
            counts["no_deal_id"] = len(unresolved)
            sample = ", ".join(unresolved[:15]) + (f" (+{len(unresolved) - 15} more)" if len(unresolved) > 15 else "")
            log.warning("Ops status: %d order(s) with no HubSpot deal found, skipped: %s",
                        len(unresolved), sample)

    # Every touched deal, with ALL of its orders: the changed ones plus their family
    # (parent / splits / suffixed follow-ons) read fresh from Airtable. A family row that
    # carries its own deal id for a different deal is left out, as are rows the old
    # automation skipped.
    by_deal = defaultdict(dict)
    deals_by_family = defaultdict(set)
    for deal_id, rid, f in resolved:
        by_deal[deal_id][rid] = f
        deals_by_family[family_key(f)].add(deal_id)
    deals_by_family.pop("", None)
    try:
        family_rows = fetch_families(at, deals_by_family) if deals_by_family else []
    except Exception:
        log.exception("Ops status: reading sibling orders failed; using changed orders only")
        family_rows = []
    for rec in family_rows:
        f = rec.get("fields") or {}
        if skip_reason(f):
            continue
        own = deal_id_of(f)
        for deal_id in deals_by_family.get(family_key(f), ()):
            if own and own != deal_id:
                continue
            by_deal[deal_id].setdefault(rec.get("id"), f)

    plan = {}
    unmapped = set()
    for deal_id, orders in by_deal.items():
        orders = list(orders.values())
        unmapped.update(_text(f.get(O_STATUS)) for f in orders if not map_status(f, options))
        chosen, want = pick_deal_status(orders, options)
        if not want:
            bump("unmapped")
            continue
        num = _text((chosen or orders[0]).get(O_ORDER_NUMBER))
        if len(orders) > 1:
            num += f" (+{len(orders) - 1} related)"
        plan[deal_id] = (num, chosen, want)
    if unmapped:
        log.warning("Ops status: Airtable status(es) with no %s option, ignored: %s",
                    HS_OPS_STATUS_PROP, ", ".join(sorted(s for s in unmapped if s)))

    props = sorted({k for _, _, want in plan.values() for k in want})
    current = hs.batch_read_deals(list(plan), props) if plan else {}
    not_found = []
    for deal_id, (num, _, want) in plan.items():
        if deal_id not in current:
            bump("deal_not_found")
            not_found.append(f"{num} ({deal_id})")
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
    if not_found:
        log.warning("Ops status: %d deal id(s) not found in HubSpot: %s",
                    len(not_found), ", ".join(not_found[:15]))
    log.info("Ops status sync (%s, %d orders in window): %s",
             "DRY_RUN" if dry_run else "LIVE", len(rows), counts)
    return counts
