"""One-time backfill: ingest deals already sitting at the committed / quote-signed
stage(s) (HS_COMMITTED_STAGE_IDS) as Pipe17 orders, for the switch to committed-stage
ingestion.

DRY_RUN by default: it lists what it WOULD do per deal (would_create / would_update /
unchanged) and writes nothing. Pass --apply to write. Reuses hold_sync.reconcile_deal, so
the awaiting-payment order shape and the dedupe (verify/correct the one order, never a
second) are identical to the live sync.

  python backfill_committed.py                 # dry-run, all committed-stage deals
  python backfill_committed.py --deal-id 123   # dry-run, one deal
  python backfill_committed.py --apply          # LIVE, all committed-stage deals
  python backfill_committed.py --apply --exclude-deal-ids 123,456   # skip those deals
"""
import logging
import sys

import hubspot_client as hs
import pipe17_write as p17w
import hold_sync
from config import CURRENCY_API_KEY_MAP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hubspot-pipe17")


def _arg(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main():
    apply = "--apply" in sys.argv
    dry_run = not apply
    deal_id = _arg("--deal-id")
    exclude = {d.strip() for d in (_arg("--exclude-deal-ids") or "").split(",") if d.strip()}

    if deal_id:
        deals = [hs.get_deal(deal_id)]
    else:
        deals = hs.find_committed_deals()

    log.info("Committed backfill | mode=%s | excluding=%s",
             "APPLY (LIVE)" if apply else "DRY_RUN", sorted(exclude) or "none")
    counts = {}
    for deal in deals:
        did = str(deal.get("id"))
        if did in exclude:
            counts["excluded"] = counts.get("excluded", 0) + 1
            log.info("deal %s -> excluded (skipped by --exclude-deal-ids)", did)
            continue
        try:
            result, ext = hold_sync.reconcile_deal(
                deal, hs=hs, p17w=p17w, key_map=CURRENCY_API_KEY_MAP, dry_run=dry_run)
        except Exception:
            log.exception("Backfill failed for deal %s", did)
            result, ext = "failed", None
        counts[result] = counts.get(result, 0) + 1
        log.info("deal %s -> %s (%s)", did, result, ext)
    log.info("Committed backfill done (%s): %s", "APPLY" if apply else "DRY_RUN", counts)
    if counts.get("failed"):
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Committed backfill failed")
        sys.exit(1)
