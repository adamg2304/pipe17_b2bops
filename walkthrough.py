"""End-to-end walkthrough for ONE deal: HubSpot -> Pipe17, step by step.

Shows every stage so you can watch the workflow live:
  1. pull the deal + line items from HubSpot
  2. build the Pipe17 order payload
  3. idempotency check (is it already in Pipe17?)
  4. create the draft order in Pipe17
  5. write the Pipe17 order id back to the HubSpot deal
  6. read the order back from Pipe17 to confirm it landed

Then confirm the order in the Pipe17 UI (Draft -> New) to fire shipping requests.

Usage:
  DRY_RUN=true  python walkthrough.py --deal-id 64737059217   # preview only, no writes
  DRY_RUN=false python walkthrough.py --deal-id 64737059217   # create it for real
"""
import json
import sys

from config import DRY_RUN, CURRENCY_API_KEY_MAP
import hubspot_client as hs
import pipe17_write as p17w
from transform_order import build_order


def _arg(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def step(n, msg):
    print(f"\n=== {n}. {msg} ===")


def main():
    deal_id = _arg("--deal-id")
    if not deal_id:
        print("Usage: python walkthrough.py --deal-id <HUBSPOT_DEAL_ID>")
        sys.exit(2)

    print(f"Mode: {'DRY_RUN (no writes)' if DRY_RUN else 'LIVE'}  |  deal {deal_id}")

    step(1, "Pull the deal + line items from HubSpot")
    deal = hs.get_deal(deal_id)
    line_items = hs.get_line_items(deal_id)
    contact = hs.delivery_contact_of(deal_id)
    props = deal.get("properties", {})
    print(f"  deal: {props.get('dealname')}  currency={props.get('deal_currency_code')}  amount={props.get('amount')}")
    print(f"  {len(line_items)} line items (in HubSpot order):")
    for li in line_items:
        print(f"    - {li.get('sku')}  x{li.get('quantity')}  net_amount={li.get('amount')}  ({li.get('name')})")
    if contact:
        print(f"  delivery contact: {contact.get('firstName','')} {contact.get('lastName','')}"
              f"  {contact.get('email','')}  {contact.get('phone','')}".rstrip())
    else:
        print("  delivery contact: (none found under that association label)")

    step(2, "Build the Pipe17 order payload")
    body, currency, ext_order_id = build_order(deal, line_items, contact)
    print(json.dumps(body, indent=2, default=str))
    api_key = CURRENCY_API_KEY_MAP.get(currency)
    if not api_key:
        print(f"  !! no Pipe17 API key for currency {currency} — stopping.")
        sys.exit(1)
    print(f"  -> will send to the {currency} channel as extOrderId={ext_order_id}")

    if DRY_RUN:
        print("\nDRY_RUN=true — stopping before any write. Set DRY_RUN=false to create it.")
        return

    step(3, "Check Pipe17 for an existing order (idempotency)")
    try:
        existing = p17w.get_order_by_ext_id(ext_order_id, api_key)
    except Exception as e:
        print(f"  !! couldn't read orders with the {currency} key: {e}")
        print(f"     That key needs Orders read+list scope (create alone isn't enough), or")
        print(f"     it isn't the {currency} connector's Integration API Key. Fix it and re-run.")
        return
    if existing:
        oid = existing.get("orderId") or existing.get("id")
        print(f"  already exists: {oid} (status={existing.get('status')}).")
        print("  To re-create: use a fresh deal, OR delete that draft in Pipe17 and clear")
        print(f"  pipe17_order_id on deal {deal_id}. Stopping so we don't touch the existing order.")
        return
    print("  none found — creating.")

    step(4, "Create the draft order in Pipe17")
    order = p17w.create_order(body, api_key)
    oid = order.get("orderId") or order.get("id") or ext_order_id
    print(f"  created: orderId={oid}  extOrderId={ext_order_id}  status={order.get('status', 'draft')}")

    step(5, "Write the Pipe17 order id back to the HubSpot deal")
    hs.set_pipe17_order_id(deal_id, str(oid))
    print(f"  set pipe17_order_id = {oid} on deal {deal_id}")

    step(6, "Read the order back from Pipe17 to confirm")
    check = p17w.get_order_by_ext_id(ext_order_id, api_key)
    if check:
        print(f"  confirmed in Pipe17: status={check.get('status')}  tags={check.get('tags')}")
    else:
        print("  !! could not read it back — check the Pipe17 UI.")

    print("\nDONE (HubSpot -> Pipe17).")
    print("Next step in the workflow: open this order in Pipe17 and click Confirm (Draft -> New).")
    print("That fires the shipping requests; once Track A is deployed, those sync to Airtable.")


if __name__ == "__main__":
    main()
