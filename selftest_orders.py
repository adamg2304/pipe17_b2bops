"""Offline check for the Track A order transform (Pipe17 order -> Airtable Orders).

No network, no creds. Contact (name/email/phone/company) is read from the order's
shippingAddress (decision 2026-09-20); the top-level customer object is a fallback only.

    PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_orders.py
"""
import json
import config as c
from order_transform import order_to_airtable

# Real #BE65076828954 shape: shippingAddress has full contact; customer object lacks
# email/phone (that's what Pipe17 returned live).
ORDER = {
    "extOrderId": "#BE65076828954", "orderId": "3d45ef58286b38f6",
    "extOrderCreatedAt": "2026-09-20T16:34:28.000Z", "totalPrice": 436308.2,
    "tags": ["Airtable"], "status": "readyForFulfillment",
    "customer": {"firstName": "Adam", "lastName": "Goldstein", "company": "Adam - Test Deal (US)"},
    "shippingAddress": {
        "firstName": "Adam", "lastName": "Goldstein", "company": "Adam - Test Deal (US)",
        "address1": "1695 Beach Street", "address2": "#204", "city": "San Francisco",
        "stateOrProvince": "CA", "zipCodeOrPostalCode": "94123", "country": "US",
        "email": "adam@trysway.co", "phone": "516-205-7781"},
    "lineItems": [
        {"sku": "11-01-00-54", "quantity": 200, "requiresShipping": True},
        {"sku": "White Glove Delivery", "quantity": 1, "requiresShipping": False}],
}

passed = failed = 0
def check(label, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {label}")
    else: failed += 1; print(f"  FAIL  {label}")

print("1) order_to_airtable(order) — contact from shippingAddress")
r = order_to_airtable(ORDER)
check("Order Number", r.get(c.O_ORDER_NUMBER) == "#BE65076828954")
check("Order Date from extOrderCreatedAt", r.get(c.O_ORDER_DATE) == "2026-09-20")
check("Pipe17 Order ID = hex orderId", r.get(c.O_PIPE17_ORDER_ID) == "3d45ef58286b38f6")
check("Deal Value = totalPrice", r.get(c.O_DEAL_VALUE) == 436308.2)
check("Status seeded", r.get(c.O_STATUS) == "No Shipments Found")
check("Delivery Address", r.get(c.O_DELIVERY_ADDRESS) == "1695 Beach Street")
check("Suite Number", r.get(c.O_SUITE_NUMBER) == "#204")
check("City / State / Zip", (r.get(c.O_CITY), r.get(c.O_STATE), r.get(c.O_ZIP_CODE))
      == ("San Francisco", "CA", "94123"))
li = json.loads(r.get(c.O_ORDER_LINE_ITEMS))
check("Line items keep the product SKU", li["SKU"] == ["11-01-00-54"])
check("Line items drop the White Glove service line", "White Glove Delivery" not in li["SKU"])
check("Product ID empty (no Shopify id in Pipe17)", li["Product ID"] == [])
check("Gate tag 'Airtable' stripped from Order Tags", c.O_ORDER_TAGS not in r)
# contact now lands from shippingAddress (the whole point of the change)
check("Customer Name from shippingAddress", r.get(c.O_CUSTOMER_NAME) == "Adam Goldstein")
check("Customer Email from shippingAddress", r.get(c.O_CUSTOMER_EMAIL) == "adam@trysway.co")
check("Customer Phone from shippingAddress", r.get(c.O_CUSTOMER_PHONE) == "516-205-7781")
check("Company from shippingAddress", r.get(c.O_COMPANY_NAME) == "Adam - Test Deal (US)")
check("Deal Name = Order - Company - Name",
      r.get(c.O_DEAL_NAME) == "#BE65076828954 - Adam - Test Deal (US) - Adam Goldstein")

print("2) order with NO shippingAddress contact -> those fields blank, order still maps")
bare = {"extOrderId": "#BE999", "orderId": "abc", "extOrderCreatedAt": "2026-09-20T00:00:00Z",
        "totalPrice": 100, "tags": ["Airtable"],
        "shippingAddress": {"address1": "1 A St", "city": "X", "stateOrProvince": "CA", "zipCodeOrPostalCode": "90001"},
        "lineItems": [{"sku": "S1", "quantity": 2, "requiresShipping": True}]}
rb = order_to_airtable(bare)
check("Order Number still maps", rb.get(c.O_ORDER_NUMBER) == "#BE999")
check("Customer Name blank when no contact anywhere", c.O_CUSTOMER_NAME not in rb)
check("Customer Email blank when no contact anywhere", c.O_CUSTOMER_EMAIL not in rb)
check("Deal Name falls back (order # only, no contact/company)", rb.get(c.O_DEAL_NAME) == "#BE999")

print("3) config sanity")
check("Order upsert merges on Order Number id", c.ORDER_MERGE_FIELD == "fldE5XFShmJ2VZOzV")
check("Track A watches readyForFulfillment", c.ORDER_SYNC_STATUSES == ["readyForFulfillment"])
check("No derived fields written (Order Details/Notes left for later steps)",
      c.O_ORDER_DETAILS not in r and c.O_NOTES not in r)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
