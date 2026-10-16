"""Offline tests for the reconcile sweep + Slack notify. No network, no creds.

Run: HUBSPOT_TOKEN=x PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_reconcile.py
"""
import main as sync
import main_reconcile as rec
import notify
from config import O_ORDER_NUMBER, ORDER_SYNC_STATUSES

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


# --- notify: safe no-op without a webhook ----------------------------------
notify.SLACK_WEBHOOK_URL = ""
check("slack() returns False when no webhook configured", notify.slack("hi") is False)

# --- drift message formatting ----------------------------------------------
msg = rec._format_healed(["#BE1", "#BE2"], 14, dry_run=False)
check("healed message names the count", "2 order(s)" in msg and "14d" in msg)
check("healed message is live verb", "healed" in msg and "would heal" not in msg)
check("healed message lists the orders", "#BE1" in msg and "#BE2" in msg)

dry = rec._format_healed(["#BE1"], 30, dry_run=True)
check("dry-run message uses 'would heal'", "would heal" in dry)

many = rec._format_healed([f"#BE{i}" for i in range(40)], 14, dry_run=False)
check("long list truncates at 25 with (+N more)", "+15 more" in many)


# --- sync_orders returns the drift it healed -------------------------------
class FakeP17:
    def __init__(self, orders):
        self._orders = orders

    def iter_orders(self, since, tag=None, statuses=None):
        check("sync_orders queries the sync statuses", statuses == ORDER_SYNC_STATUSES)
        return iter(self._orders)

    def get_customer(self, cid):
        return {}


class FakeAT:
    def __init__(self, existing_numbers):
        self.existing = set(existing_numbers)

    def find_record_id(self, table, field, value):
        return "rec_exists" if value in self.existing else None

    def upsert(self, table, records, merge_fields):
        return (1, 1, [])


ORDERS = [
    {"extOrderId": "#BE_NEW", "tags": ["Airtable"], "customerId": "c1"},
    {"extOrderId": "#BE_OLD", "tags": ["Airtable"], "customerId": "c2"},
    {"extOrderId": "#BE_UNTAGGED", "tags": [], "customerId": "c3"},
]

# Swap the module-level dependencies sync_orders closes over.
sync.p17 = FakeP17(ORDERS)
sync.at = FakeAT(existing_numbers={"#BE_OLD"})
sync.order_to_airtable = lambda o, cust, is_new=False: {O_ORDER_NUMBER: o["extOrderId"]}
sync.DRY_RUN = False
sync.GENERATE_ORDER_SLIP = False
sync.WRITEBACK_DEAL_ON_APPROVAL = False

healed = sync.sync_orders("2026-01-01T00:00:00Z", "Airtable")
check("heals only the order missing from Airtable", healed == ["#BE_NEW"])
check("does not re-heal an order already present", "#BE_OLD" not in healed)
check("untagged order is skipped entirely", "#BE_UNTAGGED" not in healed)


# --- iter_orders queries one status per request (the readyForFulfillment bug) ---
import pipe17_client as pc

status_calls = []


def _fake_get(path, params=None, max_retries=4):
    st = (params or {}).get("status")
    status_calls.append(st)
    if st == "readyForFulfillment":
        rows = [{"orderId": "rff1"}, {"orderId": "shared"}]
    elif st == "sentToFulfillment":
        rows = [{"orderId": "stf1"}, {"orderId": "shared"}]
    else:
        rows = []
    return {"orders": rows, "pagination": {"last": True}}


pc._get = _fake_get
got = list(pc.iter_orders("2026-01-01T00:00:00Z", tag="Airtable",
                          statuses=["readyForFulfillment", "sentToFulfillment"]))
ids = [o["orderId"] for o in got]
check("iter_orders issues one request per status", status_calls == ["readyForFulfillment", "sentToFulfillment"])
check("iter_orders never sends a status list (the repeated-param bug)",
      all(not isinstance(s, list) for s in status_calls))
check("iter_orders yields orders from both statuses", {"rff1", "stf1"} <= set(ids))
check("iter_orders dedups an order seen under two statuses", ids.count("shared") == 1)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
