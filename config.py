"""Configuration + field maps for the Pipe17 -> Airtable sync (shipments + orders)."""
import os

# --- Pipe17 (transport CONFIRMED 2026-09-03) --------------------------------
PIPE17_API_BASE = os.environ.get("PIPE17_API_BASE", "https://api-v3.pipe17.com/api/v3")
PIPE17_API_KEY = os.environ.get("PIPE17_API_KEY") or os.environ.get("PIPE17_API_KEY_US", "")
PIPE17_AUTH_HEADER = os.environ.get("PIPE17_AUTH_HEADER", "X-Pipe17-Key")
PIPE17_SINCE_PARAM = os.environ.get("PIPE17_SINCE_PARAM", "updatedSince")
PIPE17_SHIPMENTS_PATH = os.environ.get("PIPE17_SHIPMENTS_PATH", "/shipments")
PIPE17_ORDERS_PATH = os.environ.get("PIPE17_ORDERS_PATH", "/orders")

SYNC_LOOKBACK_MINUTES = int(os.environ.get("SYNC_LOOKBACK_MINUTES", "60"))
PIPE17_TAG_FILTER = os.environ.get("PIPE17_TAG_FILTER", "Airtable")
SYNC_ORDERS = os.environ.get("SYNC_ORDERS", "false").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

# --- Airtable ---------------------------------------------------------------
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appYsxq2ZGOz2z5ND")
ORDERS_TABLE = os.environ.get("ORDERS_TABLE", "tbltJfWGsMAeijlV2")
SHIPMENTS_TABLE = os.environ.get("SHIPMENTS_TABLE", "tblp2rEKFhMvwFdtw")

# --- Shipments field IDs ----------------------------------------------------
F_SHIPMENT_NUMBER = "fldAo9nWuRVj9lG7p"
F_ORDER_LINK = "fldS43vViZGpbffFJ"
F_ORIGIN_WH = "fldlOvl9BxQsoEow9"
F_STATUS = "fldn5soXXtCFKA3K1"
F_CUSTOMER_NAME = "fldNDEpstqH42sQkl"
F_DELIVERY_ADDRESS = "fldxhQ2SUEGDvOHS8"
F_CITY = "fldXNEOFLrJL6ag5d"
F_ZIP_CODE = "fldh4QCyKa6JFI1wQ"
F_STATE = "fldxyu1jGoYMS1rA5"
F_CUSTOMER_EMAIL = "fldH76ux37k5Hmhnw"
F_LINE_ITEMS = "fldrmuH8qsLeBDAtr"
F_SHIPMENT_CREATION_DATE = "fldAR0iUA9liD09QA"

FIELD_LABELS = {
    F_SHIPMENT_NUMBER: "Shipment Number", F_ORDER_LINK: "Order Link",
    F_ORIGIN_WH: "Origin WH", F_STATUS: "Status", F_CUSTOMER_NAME: "Customer Name",
    F_DELIVERY_ADDRESS: "Delivery Address", F_CITY: "City", F_ZIP_CODE: "Zip Code",
    F_STATE: "State", F_CUSTOMER_EMAIL: "Customer Email", F_LINE_ITEMS: "Line Items",
    F_SHIPMENT_CREATION_DATE: "Shipment Creation Date",
}

SHIPMENT_NUMBER_FIELD = F_SHIPMENT_NUMBER
SHIPMENT_ORDER_LINK_FIELD = F_ORDER_LINK
ORDER_NUMBER_FIELD = "Order Number"   # NAME for filterByFormula (IDs not allowed there)

LOCATION_MAP = {"fcf8381c1cffc669": "Mantoria - Mississauga (MTO)"}
STATUS_MAP = {
    "draft": None, "new": "Processing", "readyForFulfillment": "Processing",
    "readyToShip": "Ready to Book", "partialShipped": "In-Transit",
    "shipped": "In-Transit", "inTransit": "In-Transit", "delivered": "Arrived",
    "canceled": "Cancelled", "returned": "Cancelled",
}
DEFAULT_SHIPMENT_STATUS = None
NORMALIZE_SHIPMENT_NUMBER = os.environ.get("NORMALIZE_SHIPMENT_NUMBER", "false").lower() == "true"

# ============================================================================
# ORDERS SYNC (Pipe17 -> Airtable Orders)  — gated by SYNC_ORDERS
# ============================================================================
ORDER_SYNC_STATUSES = ["readyForFulfillment"]  # Ops-approved (Draft -> "Mark Ready For Fulfillment")
ORDER_STATUS_SEED = "No Shipments Found"

O_ORDER_NUMBER = "fldE5XFShmJ2VZOzV"
O_ORDER_DATE = "fldEyOAQnE91pEhi6"
O_STATUS = "fldrMgGTKYqowebcx"
O_CUSTOMER_NAME = "fldRksHogVvNO6YMR"
O_COMPANY_NAME = "flduqdNcA7M9PkZlj"
O_DEAL_NAME = "fldhYD7tAzZwc1Aw2"
O_DEAL_VALUE = "fldnavBgYF5c8hCzL"
O_DELIVERY_ADDRESS = "fldBYEkOH9umhsPkE"
O_SUITE_NUMBER = "fldW4QfIYHWRd9OD1"
O_CITY = "fld1us6ByWxuSOoxJ"
O_STATE = "fldBfijftTMvEFz2B"
O_ZIP_CODE = "fldlLEUuxFUsrm9Ym"
O_CUSTOMER_EMAIL = "fldLOUMtQC8Ot0pP2"
O_CUSTOMER_PHONE = "fldcx96tdKbTmOXiq"
O_ORDER_TAGS = "fldkYsJf29f50gvVY"
O_ORDER_DETAILS = "fldCln1sbb4Rx3rGS"
O_NOTES = "fldzxyuljaUM82P3W"
O_PIPE17_ORDER_ID = "fldz8upok6ZOlv9Zv"
O_ORDER_LINE_ITEMS = "fldMKgX11B2IypTzY"
ORDER_MERGE_FIELD = O_ORDER_NUMBER

# ===========================================================================
# Track B  (HubSpot deposit-paid deals -> Pipe17 draft sales orders)
# ===========================================================================
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
HUBSPOT_API_BASE = os.environ.get("HUBSPOT_API_BASE", "https://api.hubapi.com")
HS_LOOKBACK_MINUTES = int(os.environ.get("HS_LOOKBACK_MINUTES", "60"))
HS_TRIGGER_MODE = os.environ.get("HS_TRIGGER_MODE", "datetime_property")
HS_DEPOSIT_PAID_DATE_PROP = os.environ.get("HS_DEPOSIT_PAID_DATE_PROP", "deposit_paid_date")
HS_DEPOSIT_PAID_STAGE_ID = os.environ.get("HS_DEPOSIT_PAID_STAGE_ID", "")
HS_DEAL_PIPELINE_ID = os.environ.get("HS_DEAL_PIPELINE_ID", "")
HS_PIPE17_ORDER_ID_PROP = os.environ.get("HS_PIPE17_ORDER_ID_PROP", "pipe17_order_id")
HS_ORDER_NUMBER_PROP = os.environ.get("HS_ORDER_NUMBER_PROP", "order_number")
HS_CURRENCY_PROP = os.environ.get("HS_CURRENCY_PROP", "deal_currency_code")
HS_TAX_TOTAL_PROP = os.environ.get("HS_TAX_TOTAL_PROP", "committed_tax_total")
HS_SHIP_ADDR_PROPS = {
    "name":    os.environ.get("HS_SHIP_NAME_PROP", "shipping_name"),
    "address": os.environ.get("HS_SHIP_ADDR_PROP", "shipping_address"),
    "city":    os.environ.get("HS_SHIP_CITY_PROP", "shipping_city"),
    "state":   os.environ.get("HS_SHIP_STATE_PROP", "shipping_state"),
    "zip":     os.environ.get("HS_SHIP_ZIP_PROP", "shipping_zip"),
    "country": os.environ.get("HS_SHIP_COUNTRY_PROP", "shipping_country"),
    "email":   os.environ.get("HS_SHIP_EMAIL_PROP", "shipping_email"),
}
HS_LI_PROPS = {
    "sku":      os.environ.get("HS_LI_SKU_PROP", "hs_sku"),
    "name":     os.environ.get("HS_LI_NAME_PROP", "name"),
    "quantity": os.environ.get("HS_LI_QTY_PROP", "quantity"),
    "price":    os.environ.get("HS_LI_PRICE_PROP", "price"),
}
CURRENCY_CHANNEL_MAP = {  # legacy/no longer used for routing (key selects the channel)
    "USD": os.environ.get("PIPE17_CHANNEL_US", ""),
    "CAD": os.environ.get("PIPE17_CHANNEL_CA", ""),
}
PIPE17_DRAFT_STATUS = os.environ.get("PIPE17_DRAFT_STATUS", "draft")
PIPE17_AIRTABLE_TAG = os.environ.get("PIPE17_AIRTABLE_TAG", "Airtable")

# --- Branch-specific overrides (last definition wins) ---
HS_TRIGGER_MODE = "stage"
HS_DEPOSIT_PAID_STAGE_ID = os.environ.get("HS_DEPOSIT_PAID_STAGE_ID", "10492961")
HS_SHIP_ADDR_PROPS = {
    "address":  "delivery_address_1",
    "address2": "delivery_address_2",
    "city":     "delivery_city",
    "state":    "delivery_state_province",
    "zip":      "delivery_zip_postal_code",
}

# two-key routing (the API key selects the US/CA channel), order-create knobs
PIPE17_API_KEY_US = os.environ.get("PIPE17_API_KEY_US", os.environ.get("PIPE17_API_KEY", ""))
PIPE17_API_KEY_CA = os.environ.get("PIPE17_API_KEY_CA", os.environ.get("PIPE17_API_KEY", ""))
CURRENCY_API_KEY_MAP = {"USD": PIPE17_API_KEY_US, "CAD": PIPE17_API_KEY_CA}
PIPE17_ORDER_SOURCE = os.environ.get("PIPE17_ORDER_SOURCE", "hubspot")

SERVICE_SKUS = {
    "White Glove Delivery", "Free Shipping",
    "Union Labor Surcharge", "Stair Carry Surcharge", "Expedited Shipping",
    "After Hours Surcharge", "Freight Shipping",
}
PIPE17_ORDER_PREFIX_MAP = {
    "USD": os.environ.get("PIPE17_PREFIX_US", "#BE"),
    "CAD": os.environ.get("PIPE17_PREFIX_CA", "#CEN"),
}
HS_DELIVERY_CONTACT_LABEL = os.environ.get("HS_DELIVERY_CONTACT_LABEL", "End User - Delivery Contact")

# --- Deal write-back on Ops approval ----------------------------------------
# When Track A first sees an order in readyForFulfillment (Ops clicked "Mark Ready
# For Fulfillment"), move the HubSpot deal to the Ordered-with-Warehouse stage and
# record the Pipe17 order number on the deal. Deal id comes from the Pipe17 order's
# hubspot_deal_id custom field (set by Track B at draft creation).
HS_ORDERED_STAGE_ID = os.environ.get("HS_ORDERED_STAGE_ID", "5428967")            # "Product Ordered with Warehouse"
HS_ORDER_DETAILS_PROP = os.environ.get("HS_ORDER_DETAILS_PROP", "order_details")  # deal prop for the Pipe17 order number
HS_DEAL_ID_CUSTOM_FIELD = os.environ.get("HS_DEAL_ID_CUSTOM_FIELD", "hubspot_deal_id")
WRITEBACK_DEAL_ON_APPROVAL = os.environ.get("WRITEBACK_DEAL_ON_APPROVAL", "true").lower() == "true"

# --- Document generation (order slip / packing list) -----------------------
GENERATE_ORDER_SLIP = os.environ.get("GENERATE_ORDER_SLIP", "true").lower() == "true"
GENERATE_PACKING_LIST = os.environ.get("GENERATE_PACKING_LIST", "false").lower() == "true"  # on once shipment leg proven
O_ORDER_ATTACHMENTS = "fldo34yjW8UPZKNJ5"        # Orders multipleAttachments — order slip PDF
SHIP_PACKING_LIST_ATTACH = "fldyEzJwoGg8Lpjem"   # Shipments multipleAttachments — packing list PDF
