"""Offline self-test for build_draft_order. No network. Run: python3 selftest_order.py"""
import json
import sys
from order_payload import build_draft_order

# --- sample USD enterprise deal, no Pipe17 order yet (expect CREATE) ----------
usd_deal = {
    "id": "884412",
    "currency": "USD",
    "enterprise": True,
    "white_glove": True,
    "shipping_address": {"first_name": "Sam", "last_name": "Lee", "address1": "1 Market St",
                         "city": "San Francisco", "state": "CA", "zip": "94105",
                         "country": "US", "email": "sam@acme.com"},
}
usd_lines = [
    {"id": 1, "hs_sku": "11-01-00-50", "quantity": 4, "price": 320.0, "name": "Ergonomic Chair"},
    {"id": 2, "hs_sku": "", "quantity": 1, "price": 90.0, "name": "Mystery (no SKU)"},  # skip
    {"id": 3, "hs_sku": "11-02-00-11", "quantity": 2, "price": 545.0, "discount": 50, "name": "Standing Desk"},
]

# --- sample CAD deal that already has a Pipe17 order (expect UPDATE/PUT) -------
cad_deal = {
    "id": "884499",
    "currency": "CAD",
    "pipe17_order_id": "ord_ca_778",
    "shipping_address": {"country": "CA", "city": "Toronto", "state": "ON"},
}
cad_lines = [{"id": 9, "hs_sku": "11-06-00-55", "quantity": 5, "price": 120.0, "name": "Footrest"}]

fail = []

m, p, payload, skipped = build_draft_order(usd_deal, usd_lines)
print("USD deal ->", m, p)
print(json.dumps(payload, indent=2))
print("skipped SKUs:", skipped, "\n")
if m != "POST" or p != "/orders": fail.append("USD deal should CREATE")
if payload["status"] != "draft": fail.append("status must be draft")
if payload["currency"] != "USD": fail.append("USD currency wrong")
if payload["shippingAddress"]["country"] != "US": fail.append("US routing country wrong")
if len(payload["lineItems"]) != 2: fail.append("blank-SKU line should be skipped")
if skipped != [2]: fail.append("skipped list should flag line id 2")
if "Enterprise" not in payload["tags"] or "White Glove" not in payload["tags"]: fail.append("tags missing")
# subtotal = 4*320 + (2*545 - 50) = 1280 + 1040 = 2320
if payload["subTotalPrice"] != 2320.0: fail.append(f"subtotal wrong: {payload['subTotalPrice']}")

m2, p2, payload2, _ = build_draft_order(cad_deal, cad_lines)
print("CAD deal ->", m2, p2)
if m2 != "PUT" or p2 != "/orders/ord_ca_778": fail.append("CAD deal with existing order should UPDATE")
if payload2["currency"] != "CAD": fail.append("CAD currency wrong")
if payload2["shippingAddress"]["country"] != "CA": fail.append("CA routing country wrong")

print()
if fail:
    print("FAIL:"); [print("  -", x) for x in fail]; sys.exit(1)
print("PASS: create-vs-update routing, USD/CAD orderbot country, blank-SKU skip+flag, "
      "Enterprise/White Glove tags, subtotal math all correct.")
