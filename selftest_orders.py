"""Offline check that the Orders-sync changes landed correctly.

No GCP, no creds, no network. Runs order_to_airtable over the real #BE64737059217
payload and asserts the field-ID mapping, line-item filtering, gate-tag stripping,
blank contact when no customer, and populated contact when a customer is supplied.

    PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_orders.py
"""
import json

import config as c
from order_transform import order_to_airtable

# Real Pipe17 order (#BE64737059217), trimmed to what the transform reads.
ORDER = {
    "extOrderId": "#BE64737059217",
    "orderId": "6e72e9d2190bfc7f",
    "extOrderCreatedAt": "2026-09-10T04:54:30.000Z",
    "createdAt": "2026-09-10T04:54:31.387Z",
    "totalPrice": 6998.75,
    "tags": ["Airtable"],
    "shippingAddress": {
        "company": "Hannah Brooke Design - Terral Hill - Montgomery, TX",
        "address1": "895 Fish Creek Thoroughfare", "address2": "A",
        "city": "Montgomery", "stateOrProvince": "TX",
        "zipCodeOrPostalCode": "77316", "country": "US",
    },
    "lineItems": [
        {"sku": "11-03-00-50", "quantity": 25, "name": "Daily Chair", "requiresShipping": True},
        {"sku": "White Glove Delivery", "quantity": 1, "name": "White Glove Delivery",
         "requiresShipping": False},
    ],
}

passed = failed = 0
def check(label, cond):
    global passed, failed
    if cond:
        passed += 1; print(f"  PASS  {label}")
    else:
        failed += 1; print(f"  FAIL  {label}")

# ---- 1) no customer yet (pre Track B): contact blank, everything else maps ----
print("1) order_to_airtable(order, customer=None)")
r = order_to_airtable(ORDER)
check("Order Number = #BE64737059217", r.get(c.O_ORDER_NUMBER) == "#BE64737059217")
check("Order Date from extOrderCreatedAt", r.get(c.O_ORDER_DATE) == "2026-09-10")
check("Pipe17 Order ID = hex orderId", r.get(c.O_PIPE17_ORDER_ID) == "6e72e9d2190bfc7f")
check("Deal Value = totalPrice", r.get(c.O_DEAL_VALUE) == 6998.75)
check("Status seeded", r.get(c.O_STATUS) == "No Shipments Found")
check("Delivery Address", r.get(c.O_DELIVERY_ADDRESS) == "895 Fish Creek Thoroughfare")
check("Suite Number", r.get(c.O_SUITE_NUMBER) == "A")
check("City / State / Zip", (r.get(c.O_CITY), r.get(c.O_STATE), r.get(c.O_ZIP_CODE))
      == ("Montgomery", "TX", "77316"))
li = json.loads(r.get(c.O_ORDER_LINE_ITEMS))
check("Line items keep the product SKU", li["SKU"] == ["11-03-00-50"])
check("Line items keep quantity", li["Quantity"] == [25])
check("Line items drop the White Glove service line", "White Glove Delivery" not in li["SKU"])
check("Product ID empty (no Shopify id in Pipe17)", li["Product ID"] == [])
check("Gate tag 'Airtable' stripped from Order Tags", c.O_ORDER_TAGS not in r)
check("Customer Name blank without a customer", c.O_CUSTOMER_NAME not in r)
check("Customer Email blank without a customer", c.O_CUSTOMER_EMAIL not in r)
check("Company Name NOT taken from messy shippingAddress.company", c.O_COMPANY_NAME not in r)
check("Deal Name falls back to order number only", r.get(c.O_DEAL_NAME) == "#BE64737059217")

# ---- 2) with a Pipe17 customer (post Track B): contact fills, no code change ----
print("2) order_to_airtable(order, customer=<from Track B>)")
cust = {"firstName": "Hannah", "lastName": "Weinberg", "email": "hannah@example.com",
        "phone": "555-0100", "company": "Terral Hill"}
r2 = order_to_airtable(ORDER, cust)
check("Customer Name", r2.get(c.O_CUSTOMER_NAME) == "Hannah Weinberg")
check("Customer Email", r2.get(c.O_CUSTOMER_EMAIL) == "hannah@example.com")
check("Customer Phone", r2.get(c.O_CUSTOMER_PHONE) == "555-0100")
check("Company from customer (clean)", r2.get(c.O_COMPANY_NAME) == "Terral Hill")
check("Deal Name = Order - Company - Name",
      r2.get(c.O_DEAL_NAME) == "#BE64737059217 - Terral Hill - Hannah Weinberg")

# ---- 3) merge key + writable-only sanity ----
print("3) config sanity")
check("Order upsert merges on Order Number id", c.ORDER_MERGE_FIELD == "fldE5XFShmJ2VZOzV")
check("No derived fields written (Order Details/Notes left for later steps)",
      c.O_ORDER_DETAILS not in r and c.O_NOTES not in r)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
