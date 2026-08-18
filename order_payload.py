"""Build a Pipe17 draft sales-order payload from a HubSpot deal + its line items.

Pure functions, no network. This is the core of Flow 1 (HubSpot deal -> Pipe17 SO):
create the SO in `draft` at quote acceptance, keep it in sync as line items change.
The webhook receiver / transport wraps these; this module owns the mapping so it can
be unit-tested with zero infra.

Order schema confirmed against Pipe17 v3 (POST/PUT /orders is async; poll the job).
Auth + base identical to the shipment feed: X-Pipe17-Key, https://api-v3.pipe17.com/api/v3
"""

# HubSpot deal-currency value -> (Pipe17 currency, ship-to country for orderbot routing)
CURRENCY_ROUTING = {
    "USD": ("USD", "US"),
    "CAD": ("CAD", "CA"),
}


def decide_method(deal: dict) -> str:
    """POST (create) if no Pipe17 order yet on the deal, else PUT (replace/keep-in-sync).
    Reads the deal's pipe17_order_id property (the create-vs-update discriminator)."""
    return "PUT" if deal.get("pipe17_order_id") else "POST"


def _line_items(hs_line_items):
    """Map HubSpot line items -> Pipe17 lineItems. Skip blank/None SKUs; return
    (mapped, skipped) so the caller can flag skips rather than silently drop them."""
    mapped, skipped = [], []
    for li in hs_line_items or []:
        sku = (li.get("hs_sku") or "").strip()
        if not sku:
            skipped.append(li.get("id") or li.get("name") or "<unknown>")
            continue
        qty = li.get("quantity") or 0
        price = li.get("price") or 0
        mapped.append({
            "uniqueId": str(li.get("id")),          # HubSpot line-item id
            "sku": sku,
            "quantity": qty,
            "itemPrice": price,
            "itemDiscount": li.get("discount", 0),
            "requiresShipping": True,
            "taxable": bool(li.get("taxable", False)),
        })
    return mapped, skipped


def build_draft_order(deal: dict, hs_line_items: list):
    """Return (method, path, payload, skipped_skus).
    method/path tell the transport whether to POST /orders or PUT /orders/{id}."""
    deal_id = deal["id"]
    hs_currency = (deal.get("currency") or "USD").upper()
    currency, ship_country = CURRENCY_ROUTING.get(hs_currency, ("USD", "US"))

    line_items, skipped = _line_items(hs_line_items)
    subtotal = round(sum(li["itemPrice"] * li["quantity"] - li["itemDiscount"]
                         for li in line_items), 2)

    tags = [f"deal-{deal_id}"]
    if deal.get("enterprise"):
        tags.append("Enterprise")
    if deal.get("white_glove"):
        tags.append("White Glove")

    addr = deal.get("shipping_address") or {}
    payload = {
        "extOrderId": f"hubspot-deal-{deal_id}",
        "orderSource": "HubSpot",
        "status": "draft",                 # PENDING §1: confirm draft reserves inventory
        "currency": currency,
        "tags": tags,
        "lineItems": line_items,
        "subTotalPrice": subtotal,
        "totalPrice": subtotal,            # + shipping/surcharge/tax when wired (Phase 2)
        "shippingAddress": {
            "country": addr.get("country", ship_country),
            "firstName": addr.get("first_name"),
            "lastName": addr.get("last_name"),
            "address1": addr.get("address1"),
            "city": addr.get("city"),
            "stateOrProvince": addr.get("state"),
            "zipCodeOrPostalCode": addr.get("zip"),
            "email": addr.get("email"),
        },
        "orderNote": f"Auto-created from HubSpot deal {deal_id} at quote acceptance",
    }

    method = decide_method(deal)
    existing = deal.get("pipe17_order_id")
    path = f"/orders/{existing}" if method == "PUT" else "/orders"
    return method, path, payload, skipped
