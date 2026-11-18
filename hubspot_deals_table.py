"""Airtable "Hubspot Deals" rows for deals committed to Pipe17.

Replaces the Zapier zap "Sync HubSpot Deals with Airtable Records", which fired when a
deal entered the first stage of the HubSpot Operations Pipeline. That pipeline is being
retired, so the trigger moves here: when Track A writes an order to Airtable Orders (the
order is in Pipe17 with the Airtable tag, i.e. the deal is committed), the deal gets a row
in Hubspot Deals, linked to its Orders row.

Rules:
  - One row per deal. The table is searched by Hubspot Deal ID first. An existing row is
    never rewritten (Ops attach COI samples and edit it by hand); only a missing
    Orders link is filled in. The zap created a fresh row on every stage entry, which
    left duplicates; this does not.
  - Field mapping is the zap's, with two fixes: the deal link uses the portal id (the zap
    put a contact id there), and the contact is the deal's labeled delivery contact,
    falling back to the first associated contact.
  - Every HubSpot lookup beyond the deal itself is best-effort. A missing company, owner
    or contact leaves that cell blank rather than skipping the row.

ensure_rows takes hs / at injected so the selftest runs offline.
"""
import logging

from config import (
    HS_DEALS_TABLE, HD_DEAL_ID_NAME, HS_DEALS_TABLE_PROPS,
    HD_DEAL_NAME, HD_DEAL_ID, HD_ORDER_NUMBER, HD_INVOICE_NUMBER, HD_ORDER_LINK,
    HD_DEAL_LINK, HD_COMPANY, HD_CONTACT, HD_CONTACT_EMAIL, HD_DELIVERY_TYPE,
    HD_REGULAR_HOURS, HD_STAIR_CARRY, HD_UNION_BUILDING, HD_COI_REQUIRED, HD_FLOORPLAN,
    HD_SALES_CONTACT, HD_SALES_CONTACT_EMAIL, HD_STAKEHOLDER,
    HS_APP_BASE, HS_PORTAL_ID,
)

log = logging.getLogger("pipe17-airtable")


def _name(first, last):
    return " ".join(p for p in (first, last) if p).strip()


def _is_url(v):
    return isinstance(v, str) and v.strip().lower().startswith(("http://", "https://"))


def build_fields(deal_id, props, *, order_number=None, order_record_id=None,
                 contact=None, company=None, owner=None):
    """Hubspot Deals row (field-ID keyed) from a deal's properties plus its lookups.
    Blank values are dropped so typecast never invents an empty select option."""
    contact = contact or {}
    owner = owner or {}
    f = {
        HD_DEAL_NAME: props.get("dealname"),
        HD_DEAL_ID: str(deal_id),
        HD_ORDER_NUMBER: order_number,
        HD_INVOICE_NUMBER: props.get("invoice_number"),
        HD_DEAL_LINK: f"{HS_APP_BASE}/contacts/{HS_PORTAL_ID}/record/0-3/{deal_id}",
        HD_COMPANY: (company or {}).get("name"),
        HD_CONTACT: _name(contact.get("firstname"), contact.get("lastname")),
        HD_CONTACT_EMAIL: props.get("delivery_contact_email") or contact.get("email"),
        HD_DELIVERY_TYPE: props.get("delivery_type"),
        HD_REGULAR_HOURS: props.get("regular_hour_delivery"),
        HD_STAIR_CARRY: props.get("stair_carry"),
        HD_UNION_BUILDING: props.get("union_building_"),
        HD_COI_REQUIRED: props.get("coi_required_"),
        HD_SALES_CONTACT: _name(owner.get("firstName"), owner.get("lastName")),
        HD_SALES_CONTACT_EMAIL: owner.get("email"),
        HD_STAKEHOLDER: props.get("deal_stakeholder"),
    }
    floorplan = props.get("floorplan")
    if _is_url(floorplan):
        f[HD_FLOORPLAN] = [{"url": floorplan.strip()}]
    if order_record_id:
        f[HD_ORDER_LINK] = [order_record_id]
    return {k: v for k, v in f.items() if v not in (None, "", [])}


def _safe(label, deal_id, fn):
    try:
        return fn()
    except Exception:
        log.warning("Hubspot Deals row: %s lookup failed for deal %s (left blank)",
                    label, deal_id, exc_info=True)
        return None


def _gather(deal_id, hs):
    """Deal props + delivery contact + company + owner, each lookup best-effort."""
    from config import HS_DELIVERY_CONTACT_LABEL
    props = hs.get_deal_props(deal_id, HS_DEALS_TABLE_PROPS)

    def contact():
        c = hs.get_labeled_contact(deal_id, HS_DELIVERY_CONTACT_LABEL)
        if c:
            return c.get("properties") or {}
        cid = hs.first_associated_id(deal_id, "contacts")
        return hs.get_object_props("contacts", cid, ["firstname", "lastname", "email"]) if cid else None

    def company():
        cid = hs.first_associated_id(deal_id, "companies")
        return hs.get_object_props("companies", cid, ["name"]) if cid else None

    return (props,
            _safe("contact", deal_id, contact),
            _safe("company", deal_id, company),
            _safe("owner", deal_id, lambda: hs.get_owner(props.get("hubspot_owner_id"))))


def ensure_row(deal_id, order_number, order_record_id, *, hs, at, dry_run):
    """Create the deal's Hubspot Deals row if missing, else fill a missing Orders link.
    Returns created / linked / exists / would_create / would_link."""
    existing = at.find_record(HS_DEALS_TABLE, HD_DEAL_ID_NAME, str(deal_id))
    if existing:
        links = (existing.get("fields") or {}).get(HD_ORDER_LINK) or []
        if order_record_id and order_record_id not in links:
            if dry_run:
                return "would_link"
            at.update_record(HS_DEALS_TABLE, existing["id"],
                             {HD_ORDER_LINK: links + [order_record_id]})
            return "linked"
        return "exists"

    props, contact, company, owner = _gather(deal_id, hs)
    fields = build_fields(deal_id, props, order_number=order_number,
                          order_record_id=order_record_id,
                          contact=contact, company=company, owner=owner)
    if dry_run:
        log.info("[DRY_RUN] would create Hubspot Deals row for deal %s: %s", deal_id, fields)
        return "would_create"
    at.create_record(HS_DEALS_TABLE, fields)
    return "created"


def ensure_rows(items, *, hs, at, dry_run):
    """items: [(deal_id, order_number, order_record_id)]. Per-deal failures are logged
    and never block the sync. Returns a {result: count} dict."""
    counts = {}
    for deal_id, order_number, rec_id in items:
        if not deal_id:
            counts["no_deal_id"] = counts.get("no_deal_id", 0) + 1
            continue
        try:
            result = ensure_row(deal_id, order_number, rec_id, hs=hs, at=at, dry_run=dry_run)
        except Exception:
            log.exception("Hubspot Deals row failed for deal %s (order %s)", deal_id, order_number)
            result = "failed"
        counts[result] = counts.get(result, 0) + 1
    if counts:
        log.info("Hubspot Deals table: %s", counts)
    return counts
