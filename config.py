"""Configuration + field maps for the Pipe17 -> Airtable sync (shipments + orders)."""
import os

# --- Pipe17 (transport CONFIRMED 2026-09-03) --------------------------------
PIPE17_API_BASE = os.environ.get("PIPE17_API_BASE", "https://api-v3.pipe17.com/api/v3")
PIPE17_API_KEY = os.environ["PIPE17_API_KEY"]
PIPE17_AUTH_HEADER = os.environ.get("PIPE17_AUTH_HEADER", "X-Pipe17-Key")
PIPE17_SINCE_PARAM = os.environ.get("PIPE17_SINCE_PARAM", "updatedSince")
PIPE17_SHIPMENTS_PATH = os.environ.get("PIPE17_SHIPMENTS_PATH", "/shipments")
PIPE17_ORDERS_PATH = os.environ.get("PIPE17_ORDERS_PATH", "/orders")

SYNC_LOOKBACK_MINUTES = int(os.environ.get("SYNC_LOOKBACK_MINUTES", "60"))
PIPE17_TAG_FILTER = os.environ.get("PIPE17_TAG_FILTER", "Airtable")
SYNC_ORDERS = os.environ.get("SYNC_ORDERS", "false").lower() == "true"
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

# --- Airtable ---------------------------------------------------------------
AIRTABLE_API_KEY = os.environ["AIRTABLE_API_KEY"]
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
ORDER_SYNC_STATUSES = ["new"]
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
