"""Track B: HubSpot deposit-paid deals -> Pipe17 draft sales orders (GCP Cloud Run Job).

Routes each order to US or CA by deal currency, using the matching Pipe17 API key (the
key selects the channel). Idempotent: skips deals that already carry pipe17_order_id, and
rechecks Pipe17 by extOrderId before creating.

Usage:
  python main_hubspot_to_pipe17.py                 # all deposit-paid deals in the window
  python main_hubspot_to_pipe17.py --deal-id 123   # just one deal (great for first live test)
"""
import datetime as dt
import json
import logging
import sys

from config import DRY_RUN, HS_LOOKBACK_MINUTES, CURRENCY_API_KEY_MAP
import hubspot_client as hs
import pipe17_write as p17w
from transform_order import build_order

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hubspot-pipe17")


def _arg(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main():
    deal_id_arg = _arg("--deal-id")

    if deal_id_arg:
        log.info("Mode: %s | single deal %s", "DRY_RUN" if DRY_RUN else "LIVE", deal_id_arg)
        deals = [hs.get_deal(deal_id_arg)]
    else:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=HS_LOOKBACK_MINUTES)
        since_ms = int(since.timestamp() * 1000)
        log.info("Mode: %s | deposit-paid deals since %s",
                 "DRY_RUN" if DRY_RUN else "LIVE", since.isoformat())
        deals = hs.find_deposit_paid_deals(since_ms)

    created = skipped = failed = 0
    for deal in deals:
        deal_id = deal.get("id")
        line_items = hs.get_line_items(deal_id)
        contact = hs.delivery_contact_of(deal_id)
        body, currency, ext_order_id = build_order(deal, line_items, contact)

        api_key = CURRENCY_API_KEY_MAP.get(currency)
        if not api_key:
            log.warning("Deal %s: no Pipe17 API key for currency %s — skipping.", deal_id, currency)
            continue

        if DRY_RUN:
            log.info("[DRY_RUN] %s order for deal %s via %s key (...%s), extOrderId=%s, %d items:\n%s",
                     currency, deal_id, currency, api_key[-4:] if len(api_key) >= 4 else "----",
                     ext_order_id, len(body.get("lineItems", [])),
                     json.dumps(body, indent=2, default=str))
            continue

        try:
            existing = p17w.get_order_by_ext_id(ext_order_id, api_key)
        except Exception:
            log.exception("Deal %s: couldn't read Pipe17 orders with the %s key "
                          "(needs Orders read/list scope) — skipping to avoid a duplicate.",
                          deal_id, currency)
            failed += 1
            continue
        if existing:
            oid = existing.get("orderId") or existing.get("id")
            hs.set_pipe17_order_id(deal_id, str(oid))
            skipped += 1
            log.info("Deal %s already in Pipe17 (%s / extOrderId %s) — backfilled.",
                     deal_id, oid, ext_order_id)
            continue

        try:
            order = p17w.create_order(body, api_key)
            oid = order.get("orderId") or order.get("id") or ext_order_id
            hs.set_pipe17_order_id(deal_id, str(oid))
            created += 1
            log.info("Created Pipe17 draft SO %s (extOrderId %s) for deal %s", oid, ext_order_id, deal_id)
        except Exception:
            failed += 1
            log.exception("Create failed for deal %s", deal_id)

    log.info("Done. created=%d skipped(existing)=%d failed=%d", created, skipped, failed)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Track B sync failed")
        sys.exit(1)
