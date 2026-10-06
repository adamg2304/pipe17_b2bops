"""Inventory sync: Pipe17 Available -> HubSpot product properties (GCP Cloud Run Job).

For every HubSpot product (base SKU), resolve to the current Pipe17 product per
channel, read Available, aggregate to US / CA / total, and patch the product's
inventory_us / inventory_ca / inventory_total. Read-only from Pipe17; writes only
HubSpot product properties. Honors DRY_RUN (log, no writes). Touches no deals,
orders, or the order/shipment sync.

  python main_inventory_sync.py          # all products
  python main_inventory_sync.py --sku X  # just the product(s) whose base SKU == X
"""
import logging
import sys

from config import DRY_RUN, PIPE17_INVENTORY_READ_KEY
from pipe17_catalog import get_resolver
import pipe17_inventory as inv
import hubspot_products as hsp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("inventory-sync")


def _arg(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def _changed(current, new):
    """True if the HubSpot value differs from the computed one (current is a string
    or None; new is an int or a string). Compared numerically when both are numeric."""
    if current in (None, ""):
        return True
    try:
        return float(current) != float(new)
    except (TypeError, ValueError):
        return str(current) != str(new)


def main():
    only_sku = _arg("--sku")
    read_key = PIPE17_INVENTORY_READ_KEY
    if not read_key:
        log.error("No Pipe17 API key for inventory reads (PIPE17_INVENTORY_READ_KEY).")
        sys.exit(1)

    log.info("Mode: %s%s", "DRY_RUN" if DRY_RUN else "LIVE",
             f" | single SKU {only_sku}" if only_sku else "")

    # Build the shared lookups once per run.
    resolver_us = get_resolver("USD")
    resolver_ca = get_resolver("CAD")
    country_map = inv.location_country_map(read_key)
    us_locs = sum(1 for c in country_map.values() if c == "US")
    ca_locs = sum(1 for c in country_map.values() if c == "CA")
    log.info("Locations: %d total (US=%d, CA=%d)", len(country_map), us_locs, ca_locs)
    inv_index = inv.inventory_index(read_key)
    log.info("Inventory: %d SKUs with stock rows", len(inv_index))

    updates = []
    seen = matched = unmatched = no_sku = unchanged = 0
    for product in hsp.iter_products():
        base_sku = (product.get("sku") or "").strip()
        if only_sku and base_sku != only_sku:
            continue
        seen += 1
        if not base_sku:
            no_sku += 1
            log.warning("HubSpot product %s has no %s — skipping.",
                        product.get("id"), "base SKU")
            continue

        r = inv.compute_inventory(base_sku, resolver_us, resolver_ca, inv_index, country_map)
        if not r["matched"]:
            unmatched += 1
            log.warning("Base SKU %s has no Pipe17 product match (US + CA) — not writing.",
                        base_sku)
            continue
        matched += 1

        props = hsp.inventory_properties(r["inventory_us"], r["inventory_ca"], r["inventory_total"])
        is_changed = (_changed(product.get("inventory_us"), r["inventory_us"])
                      or _changed(product.get("inventory_ca"), r["inventory_ca"])
                      or _changed(product.get("inventory_total"), r["inventory_total"]))
        if not is_changed:
            unchanged += 1
            continue

        log.info("%s -> US(%s)=%d  CA(%s)=%d  total=%d%s",
                 base_sku, r["us_sku"], r["inventory_us"], r["ca_sku"], r["inventory_ca"],
                 r["inventory_total"], "  [DRY_RUN]" if DRY_RUN else "")
        updates.append({"id": product["id"], "properties": props})

    written = 0
    if updates and not DRY_RUN:
        written = hsp.batch_update(updates)

    log.info("Done. products_seen=%d matched=%d unmatched=%d no_sku=%d unchanged=%d "
             "to_write=%d written=%d%s",
             seen, matched, unmatched, no_sku, unchanged, len(updates), written,
             " (DRY_RUN: nothing written)" if DRY_RUN else "")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Inventory sync failed")
        sys.exit(1)
