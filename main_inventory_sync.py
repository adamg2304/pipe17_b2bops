"""Inventory sync: Pipe17 Available -> HubSpot (GCP Cloud Run Job).

For every HubSpot product (base SKU), sum Pipe17 Available across the whole version
family (bare base + every -V variant), split by warehouse country, and patch the
product's inventory_us / inventory_ca / inventory_total. Read-only from Pipe17;
writes only HubSpot product properties. Honors DRY_RUN (log, no writes). Touches no
orders or the order/shipment sync.

When INVENTORY_LINE_ITEM_SYNC is on, the SAME per-SKU numbers (from the SAME Pipe17
snapshot, via one memoized computation) are also stamped onto every line item of an
active deal, because HubSpot reporting runs on line items, not products. All three
numbers go on every line item; this job never chooses US vs CA — the HubSpot
workflows do, per the deal's region. Product and line item cannot drift: same value,
same run. The flag is off by default, so with it off this behaves exactly as the
product-only sync always has.

  python main_inventory_sync.py          # all products (+ line items if flag on)
  python main_inventory_sync.py --sku X  # just the product(s) whose base SKU == X
"""
import logging
import sys

from config import DRY_RUN, PIPE17_INVENTORY_READ_KEY, INVENTORY_LINE_ITEM_SYNC
import pipe17_inventory as inv
import hubspot_products as hsp
import hubspot_lineitems as hsli

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
    or None; new is an int). Compared numerically when both are numeric."""
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
    country_map = inv.location_country_map(read_key)
    us_locs = sum(1 for c in country_map.values() if c == "US")
    ca_locs = sum(1 for c in country_map.values() if c == "CA")
    log.info("Locations: %d total (US=%d, CA=%d)", len(country_map), us_locs, ca_locs)
    inv_by_base = inv.inventory_by_base(read_key)
    log.info("Inventory: %d base SKUs with stock rows (versions rolled up)", len(inv_by_base))

    # One memoized computation feeds BOTH the product write and the line-item fan-out,
    # so the same SKU can never resolve to two different numbers in one run.
    _memo = {}

    def compute(base):
        if base not in _memo:
            _memo[base] = inv.compute_inventory(base, inv_by_base, country_map)
        return _memo[base]

    _sync_products(compute, only_sku)
    if INVENTORY_LINE_ITEM_SYNC and not only_sku:
        _sync_line_items(compute)
    elif INVENTORY_LINE_ITEM_SYNC and only_sku:
        log.info("Line-item sync skipped: --sku is a product-only probe.")


def _sync_products(compute, only_sku):
    updates = []
    seen = matched = unmatched = no_sku = unchanged = 0
    for product in hsp.iter_products():
        base_sku = (product.get("sku") or "").strip()
        if only_sku and base_sku != only_sku:
            continue
        seen += 1
        if not base_sku:
            no_sku += 1
            log.warning("HubSpot product %s has no base SKU — skipping.", product.get("id"))
            continue

        r = compute(base_sku)
        if not r["matched"]:
            unmatched += 1
            log.warning("Base SKU %s has no Pipe17 inventory (no product in the family) "
                        "— not writing.", base_sku)
            continue
        matched += 1

        props = hsp.inventory_properties(r["inventory_us"], r["inventory_ca"], r["inventory_total"])
        is_changed = (_changed(product.get("inventory_us"), r["inventory_us"])
                      or _changed(product.get("inventory_ca"), r["inventory_ca"])
                      or _changed(product.get("inventory_total"), r["inventory_total"]))
        if not is_changed:
            unchanged += 1
            continue

        log.info("product %s -> US=%d  CA=%d  total=%d%s",
                 base_sku, r["inventory_us"], r["inventory_ca"], r["inventory_total"],
                 "  [DRY_RUN]" if DRY_RUN else "")
        updates.append({"id": product["id"], "properties": props})

    written = 0
    if updates and not DRY_RUN:
        written = hsp.batch_update(updates)

    log.info("Products done. seen=%d matched=%d unmatched=%d no_sku=%d unchanged=%d "
             "to_write=%d written=%d%s",
             seen, matched, unmatched, no_sku, unchanged, len(updates), written,
             " (DRY_RUN: nothing written)" if DRY_RUN else "")


def _sync_line_items(compute):
    """Stamp all three inventory numbers onto every line item of an active deal.
    Same memoized compute as the product pass; the line item's SKU is normalized to
    its base-SKU family (base_stem) so a versioned line-item SKU rolls up the same
    way the product does. Unchanged line items are skipped; no-SKU and
    no-inventory-record line items are left untouched."""
    updates = []
    seen = matched = unmatched = no_sku = unchanged = 0
    for li in hsli.iter_active_deal_line_items():
        seen += 1
        sku = (li.get("sku") or "").strip()
        if not sku:
            no_sku += 1
            continue
        base = inv.base_stem(sku)
        r = compute(base)
        if not r["matched"]:
            unmatched += 1
            continue
        matched += 1

        new_us, new_ca, new_total = r["inventory_us"], r["inventory_ca"], r["inventory_total"]
        is_changed = (_changed(li.get("inventory_us"), new_us)
                      or _changed(li.get("inventory_ca"), new_ca)
                      or _changed(li.get("inventory_total"), new_total))
        if not is_changed:
            unchanged += 1
            continue

        updates.append({"id": li["id"],
                        "properties": hsli.inventory_properties(new_us, new_ca, new_total)})

    written = 0
    if updates and not DRY_RUN:
        written = hsli.batch_update(updates)

    log.info("Line items done. seen=%d matched=%d unmatched=%d no_sku=%d unchanged=%d "
             "to_write=%d written=%d%s",
             seen, matched, unmatched, no_sku, unchanged, len(updates), written,
             " (DRY_RUN: nothing written)" if DRY_RUN else "")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Inventory sync failed")
        sys.exit(1)
