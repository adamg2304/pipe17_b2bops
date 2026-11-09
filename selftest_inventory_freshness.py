"""Offline tests for the inventory freshness stamp + stale-sync watchdog.

No network, no creds. Covers:
  - HubSpot datetime parsing (ISO and epoch-ms forms)
  - stale / fresh / never-stamped messages
  - the product write path: unchanged products get a stamp-only write, changed
    products get numbers + stamp, unmatched products get nothing
  - the reconcile watchdog never fails the reconcile when the check errors

    python3 selftest_inventory_freshness.py
"""
import datetime as dt

import inventory_freshness as fr
import hubspot_products as hsp
import main_inventory_sync as mis
import main_reconcile as rec
import notify

notify.SLACK_WEBHOOK_URL = ""
passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1; print(f"  PASS  {label}")
    else:
        failed += 1; print(f"  FAIL  {label}")


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 9, 19, 0, tzinfo=UTC)

print("1) parse HubSpot datetimes")
check("ISO with Z", fr.parse_hs_datetime("2026-11-09T18:55:53.804Z")
      == dt.datetime(2026, 11, 9, 18, 55, 53, 804000, tzinfo=UTC))
check("epoch ms", fr.parse_hs_datetime("1793300153804")
      == dt.datetime.fromtimestamp(1793300153.804, tz=UTC))
check("empty -> None", fr.parse_hs_datetime("") is None and fr.parse_hs_datetime(None) is None)
check("garbage -> None", fr.parse_hs_datetime("not a date") is None)

print("2) freshness verdicts")
stale, line = fr.freshness_message(NOW - dt.timedelta(minutes=50), NOW, 3)
check("50 min old -> fresh", stale is False and "fresh" in line)
stale, line = fr.freshness_message(NOW - dt.timedelta(days=21), NOW, 3)
check("3 weeks old -> stale", stale is True and "STALE" in line and "504.0h" in line)
stale, line = fr.freshness_message(None, NOW, 3)
check("never stamped -> stale", stale is True and "NO sync stamp" in line)
stale, _ = fr.freshness_message(NOW - dt.timedelta(hours=3, minutes=1), NOW, 3)
check("just over the limit -> stale", stale is True)

print("3) stamp payload")
check("epoch ms as string", hsp.synced_at_properties(1793300153804)
      == {"inventory_last_synced_at": "1793300153804"})

print("4) product pass stamps every matched product")
PRODUCTS = [
    {"id": "1", "sku": "13-11-00-00", "inventory_us": "-26", "inventory_ca": "347", "inventory_total": "321"},
    {"id": "2", "sku": "11-01-00-50", "inventory_us": "393", "inventory_ca": "345", "inventory_total": "738"},
    {"id": "3", "sku": "99-99-99-99", "inventory_us": None, "inventory_ca": None, "inventory_total": None},
    {"id": "4", "sku": "", "inventory_us": None, "inventory_ca": None, "inventory_total": None},
]
RESULTS = {
    "13-11-00-00": {"inventory_us": -26, "inventory_ca": 347, "inventory_total": 321, "matched": True},
    "11-01-00-50": {"inventory_us": 1, "inventory_ca": 428, "inventory_total": 429, "matched": True},
    "99-99-99-99": {"inventory_us": 0, "inventory_ca": 0, "inventory_total": 0, "matched": False},
}
captured = []
mis.hsp.iter_products = lambda: iter(PRODUCTS)
mis.hsp.batch_update = lambda ups: captured.extend(ups) or len(ups)
mis._sync_products(lambda b: RESULTS[b], None, False, 1793300153804)
by_id = {u["id"]: u["properties"] for u in captured}
check("unchanged product -> stamp-only write",
      by_id.get("1") == {"inventory_last_synced_at": "1793300153804"})
check("changed product -> numbers + stamp",
      by_id.get("2") == {"inventory_us": 1, "inventory_ca": 428, "inventory_total": 429,
                         "inventory_last_synced_at": "1793300153804"})
check("unmatched / no-SKU products untouched", "3" not in by_id and "4" not in by_id)

captured.clear()
mis._sync_products(lambda b: RESULTS[b], None, True, 1793300153804)
check("DRY_RUN writes nothing", captured == [])

print("5) reconcile watchdog never raises")
sent = []
rec.notify.slack = lambda text, blocks=None: sent.append(text) or False
def _boom():
    raise RuntimeError("HubSpot 503")
rec.inventory_freshness.check = _boom
try:
    rec._check_inventory_freshness()
    check("check error is swallowed", True)
except Exception:
    check("check error is swallowed", False)
check("check error is reported to Slack", any("Could not check" in s for s in sent))
rec.inventory_freshness.check = lambda: (True, ":rotating_light: STALE test")
rec._check_inventory_freshness()
check("stale line is posted", sent[-1] == ":rotating_light: STALE test")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
