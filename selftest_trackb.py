"""Offline check for the Track B transform (HubSpot deal -> Pipe17 order payload).

No network, no creds. Runs build_order over a synthetic USD deal and asserts the pieces
that matter: currency-prefixed extOrderId, draft status, Airtable tag, service-SKU
flagging, NET unit pricing (amount/qty), line-item-derived subtotal, the customer object
built from the delivery contact, and CAD prefix routing.

    PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_trackb.py
"""
from transform_order import build_order

DEAL_USD = {
    "id": "64737059217",
    "properties": {"deal_currency_code": "USD", "dealname": "Acme HQ Refresh",
                   "delivery_city": "Montgomery", "delivery_state_province": "TX",
                   "delivery_zip_postal_code": "77316", "delivery_address_1": "895 Fish Creek"},
}
LINE_ITEMS = [
    {"sku": "11-03-00-50", "name": "Daily Chair", "quantity": 25, "price": 250, "amount": 5827.5},
    {"sku": "White Glove Delivery", "name": "WGD", "quantity": 1, "price": 1171.25, "amount": 1171.25},
]
CONTACT = {"firstName": "Hannah", "lastName": "Weinberg", "email": "h@x.com", "phone": "555-0100"}

passed = failed = 0
def check(label, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {label}")
    else: failed += 1; print(f"  FAIL  {label}")

print("1) USD deal -> Pipe17 order body")
body, currency, ext = build_order(DEAL_USD, LINE_ITEMS, CONTACT)
check("currency USD", currency == "USD")
check("extOrderId = #BE<dealId>", ext == "#BE64737059217")
check("status draft", body["status"] == "draft")
check("Airtable tag", body["tags"] == ["Airtable"])
check("hubspot_deal_id custom field",
      body["customFields"] == [{"name": "hubspot_deal_id", "value": "64737059217"}])

chair = next(li for li in body["lineItems"] if li["sku"] == "11-03-00-50")
wgd = next(li for li in body["lineItems"] if li["sku"] == "White Glove Delivery")
check("net unit price = amount/qty (5827.5/25 = 233.1)", chair["itemPrice"] == 233.1)
check("chair requiresShipping True", chair["requiresShipping"] is True)
check("service SKU requiresShipping False", wgd["requiresShipping"] is False)
check("subtotal from line items (233.1*25 + 1171.25 = 6998.75)", body["subTotalPrice"] == 6998.75)
check("total = subtotal (no tax)", body["totalPrice"] == 6998.75)
check("no orderTax when tax absent", "orderTax" not in body)

check("customer object built from delivery contact",
      body.get("customer", {}).get("firstName") == "Hannah"
      and body["customer"].get("email") == "h@x.com"
      and body["customer"].get("company") == "Acme HQ Refresh")
check("shippingAddress company = dealname", body["shippingAddress"]["company"] == "Acme HQ Refresh")
check("shippingAddress country inferred US", body["shippingAddress"]["country"] == "US")
check("shippingAddress contact email", body["shippingAddress"]["email"] == "h@x.com")

print("2) CAD deal -> #CEN prefix + CA routing")
deal_ca = {"id": "63327307681", "properties": {"deal_currency_code": "CAD", "dealname": "Maple Co"}}
_, cur_ca, ext_ca = build_order(deal_ca, LINE_ITEMS, None)
check("currency CAD", cur_ca == "CAD")
check("extOrderId = #CEN<dealId>", ext_ca == "#CEN63327307681")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
