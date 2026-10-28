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
# Reconcile sweep window (main_reconcile.py). Wide on purpose: it re-checks every
# eligible order over this many days and heals any missing from Airtable, so a single
# missed/failed frequent run never drops an order permanently.
RECONCILE_LOOKBACK_DAYS = int(os.environ.get("RECONCILE_LOOKBACK_DAYS", "14"))
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
F_PIPE17_REQUEST_LINK = "fldEWt9Sq7Q41vydM"   # Pipe17 Shipping Request deep link

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
SHIPMENT_NUMBER_NAME = "Shipment Number"   # NAME for filterByFormula (BLK-4 change detection)

LOCATION_MAP = {"fcf8381c1cffc669": "Mantoria - Mississauga (MTO)"}
# --- Shipment status model (PROVISIONAL — per 2026-09-25 B2B sync mtg) -------
# Decision from that meeting: a shipment sitting in Pipe17 `readyForFulfillment`
# reads as OUT OF STOCK (routed but not yet allocatable to a warehouse), and
# `sentToFulfillment` reads as PROCESSING (routed + handed to the warehouse WMS).
#
# ⚠️ UNVERIFIED — confirm against the BLK-6 live automated-routing test before
# trusting this. Live data 2026-09-29 (#BE650768289544) shows EVERY routed
# shipment in `sentToFulfillment` even though the ORDER is out of stock; the OOS
# signal appears at the ORDER level (order.routingStatus == "pendingInventory" /
# eRouteOutOfStock exception), NOT on the shipping request. So on real routed
# data the readyForFulfillment->Out of Stock mapping below may never fire, and
# true OOS surfacing likely needs the order's routingStatus. Change status
# strings/mappings HERE only — transform.py and main.py read these, never
# hardcode the strings.
PIPE17_STATUS_READY = os.environ.get("PIPE17_STATUS_READY", "readyForFulfillment")
PIPE17_STATUS_SENT = os.environ.get("PIPE17_STATUS_SENT", "sentToFulfillment")
# "Processing" used to cover both reviewRequired (parked in the Ops review gate, no
# packing list yet) and sentToFulfillment (handed to the warehouse, packing list
# generated). That conflation made an SR read as "Processing" with no Shipment
# Details and no way to tell why. reviewRequired now has its own label so the status
# alone tells Ops whether a packing list is due: "In Review" = not yet, "Processing"
# = sent to the warehouse (packing list present). Env-overridable.
SHIP_STATUS_IN_REVIEW = os.environ.get("SHIP_STATUS_IN_REVIEW", "In Review")
SHIP_STATUS_PROCESSING = os.environ.get("SHIP_STATUS_PROCESSING", "Processing")
STATUS_MAP = {
    "draft": None,
    "new": SHIP_STATUS_PROCESSING,
    PIPE17_STATUS_READY: "Out of Stock",      # provisional: ready-for-fulfillment == OOS
    PIPE17_STATUS_SENT: SHIP_STATUS_PROCESSING,  # sent-to-fulfillment: handed to warehouse, packing list generated
    "reviewRequired": SHIP_STATUS_IN_REVIEW,  # parked in the Ops review gate, no packing list yet;
                                              # OOS (if any) surfaces at the ORDER level, not the SR
    "readyToShip": "Ready to Book",
    "partialShipped": "In-Transit",
    "shipped": "In-Transit",
    "inTransit": "In-Transit",
    "delivered": "Arrived",
    "canceled": "Cancelled",
    "returned": "Cancelled",
}
DEFAULT_SHIPMENT_STATUS = None
# Shipment Status is forward-only. Ops/warehouse advance the Airtable status (book ->
# ship -> deliver) ahead of Pipe17, which lags: an SR can sit at sentToFulfillment
# ("Processing") while the warehouse has already booked it to "Ready to Book". The sync
# must not drag that status back to the lagging Pipe17 value. A Status write is
# suppressed when it would move the row BACKWARD in this progression; a blank or
# unmanaged current status is never overwritten (preserves a human/Airtable-set value).
# Ranks define the ordering only — their absolute values don't matter.
SHIP_STATUS_RANK = {
    "Out of Stock": 1,
    SHIP_STATUS_IN_REVIEW: 2,
    SHIP_STATUS_PROCESSING: 3,
    "Ready to Book": 4,
    "In-Transit": 5,
    "Arrived": 6,
    "Cancelled": 7,
}
# Packing lists are generated only for shipments in these Pipe17 statuses (BLK-1/3):
# a shipment must be handed to the warehouse before its pick list is real.
PACKING_LIST_STATUSES = {PIPE17_STATUS_SENT}
NORMALIZE_SHIPMENT_NUMBER = os.environ.get("NORMALIZE_SHIPMENT_NUMBER", "false").lower() == "true"

# ============================================================================
# ORDERS SYNC (Pipe17 -> Airtable Orders)  — gated by SYNC_ORDERS
# ============================================================================
# Ops-approved and beyond: readyForFulfillment is where Ops marks an order ready, but an
# order can advance to sentToFulfillment (routed/sent to the warehouse) between sync runs.
# Since the Pipe17 query filters on current status, we watch both so a fast-routing order
# isn't missed. Status on the Airtable row is create-only, so widening never re-stamps it.
ORDER_SYNC_STATUSES = ["readyForFulfillment", "sentToFulfillment"]
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
O_HUBSPOT_DEAL_LINK = "fldpzcSrzXgjXkSYH"   # HubSpot deal deep link
O_PIPE17_ORDER_LINK = "fldl2GDoFDZZ4C73Z"   # Pipe17 order deep link
ORDER_MERGE_FIELD = O_ORDER_NUMBER

# --- Inventory State + OOS exception note (sync-owned; Status stays base-computed) ---
# Inventory State is a singleSelect; its options are confirmed exact strings — the sync
# only ever writes one of these, and never typecasts a new option into existence.
O_INVENTORY_STATE = os.environ.get("O_INVENTORY_STATE", "fldICzs3CaqWVf26r")
O_EXCEPTION_FLAG = os.environ.get("O_EXCEPTION_FLAG", "fldkvCt6rHrbT6Yne")  # multilineText
INV_FULLY_IN_STOCK = "Fully In Stock"
INV_PARTIALLY_OOS = "Partially Out of Stock"
INV_AWAITING = "Awaiting Inventory"
INVENTORY_STATE_OPTIONS = (INV_FULLY_IN_STOCK, INV_PARTIALLY_OOS, INV_AWAITING)
# Signal inputs (names Pipe17 uses on the order object).
OOS_EXCEPTION = os.environ.get("OOS_EXCEPTION", "eRouteOutOfStock")
ROUTING_PENDING_INVENTORY = os.environ.get("ROUTING_PENDING_INVENTORY", "pendingInventory")
ROUTING_EXCLUDED = os.environ.get("ROUTING_EXCLUDED", "excluded")
# An order in one of these statuses is not actively awaiting inventory (active guard):
# don't flag it, and clear any prior flag. Makes the sticky eRouteOutOfStock safe.
INACTIVE_ORDER_STATUSES = tuple(
    s for s in os.environ.get("INACTIVE_ORDER_STATUSES", "archived,canceled").split(",") if s)
# Recovery pass scopes OUT done orders so its Airtable query stays bounded. The base's
# done value is "Completed" (the task said "Complete"; the field's actual option is
# "Completed").
RECOVERY_DONE_STATUSES = tuple(
    s for s in os.environ.get("RECOVERY_DONE_STATUSES", "Completed").split(",") if s)
# Field NAMES (filterByFormula uses names, not ids) for the recovery/backfill queries.
INVENTORY_STATE_NAME = os.environ.get("INVENTORY_STATE_NAME", "Inventory State")
STATUS_FIELD_NAME = os.environ.get("STATUS_FIELD_NAME", "Status")
PIPE17_ORDER_ID_NAME = os.environ.get("PIPE17_ORDER_ID_NAME", "Pipe17 Order ID")

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

# Catalog read key. The B2B order-channel keys above are NOT authorized for the
# product catalog (/products -> 403), so SKU resolution reads it with a dedicated
# catalog-scoped key when set. If empty, the resolver fails open (sends base SKUs
# through unresolved) rather than taking the order flow down.
PIPE17_CATALOG_API_KEY = os.environ.get("PIPE17_CATALOG_API_KEY", "")

# Channel integrationId per currency — used to read the per-channel Channel SKU alias
# (published[].sku) when translating HubSpot base SKUs to Pipe17 product SKUs.
PIPE17_INTEGRATION_US = os.environ.get("PIPE17_INTEGRATION_US", "a64e620e190eb6aa")  # B2B Orders USA
PIPE17_INTEGRATION_CA = os.environ.get("PIPE17_INTEGRATION_CA", "34a21b0e3a4c8cdc")  # B2B-CAN Orders
CURRENCY_CHANNEL_INTEGRATION = {"USD": PIPE17_INTEGRATION_US, "CAD": PIPE17_INTEGRATION_CA}

SERVICE_SKUS = {
    "White Glove Delivery", "Free Shipping",
    "Union Labor Surcharge", "Stair Carry Surcharge", "Expedited Shipping",
    "After Hours Surcharge", "Freight Shipping",
}

# --- Order-level routing tags (from HubSpot shipping service lines) ----------
# Surfaced on the Pipe17 order so Ops routing rules can act on them. Each named
# shipping line adds a same-named tag; every order also gets the B2B tag. Tag
# strings are env-overridable so they can match the Pipe17 routing rules exactly.
ORDER_ROUTING_TAGS = os.environ.get("ORDER_ROUTING_TAGS", "true").lower() == "true"
ORDER_B2B_TAG = os.environ.get("ORDER_B2B_TAG", "B2B")   # added to every order
# HubSpot shipping-line SKU/name -> tag written on the Pipe17 order.
SHIPPING_TAG_MAP = {
    "White Glove Delivery": os.environ.get("SHIP_TAG_WHITE_GLOVE", "White Glove"),
    "Free Shipping":        os.environ.get("SHIP_TAG_FREE_SHIPPING", "Free Shipping"),
    "Expedited Shipping":   os.environ.get("SHIP_TAG_EXPEDITED", "Expedited Shipping"),
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

# --- Cross-system deep links written onto Airtable rows ---------------------
# Orders get a HubSpot deal link + Pipe17 order link; shipments get a Pipe17
# shipping-request link, so Ops can jump straight from Airtable to either system.
# The Pipe17 links use the app's search URL (proven to resolve); the org segment
# and URL templates are env-overridable so a path tweak needs no code change.
WRITE_CROSSLINKS = os.environ.get("WRITE_CROSSLINKS", "true").lower() == "true"
HS_APP_BASE = os.environ.get("HS_APP_BASE", "https://app.hubspot.com")
HS_PORTAL_ID = os.environ.get("HS_PORTAL_ID", "5361087")
PIPE17_APP_BASE = os.environ.get("PIPE17_APP_BASE_URL", "https://app.pipe17.com")
PIPE17_APP_ORG = os.environ.get("PIPE17_APP_ORG", "d6490d20e53e3811")
PIPE17_ORDER_URL_TMPL = os.environ.get(
    "PIPE17_ORDER_URL_TMPL", "{base}/{org}/orders/orders?search={num}")
PIPE17_SHIPMENT_URL_TMPL = os.environ.get(
    "PIPE17_SHIPMENT_URL_TMPL", "{base}/{org}/orders/shipments?search={num}")

# --- Document generation (order slip / packing list) -----------------------
GENERATE_ORDER_SLIP = os.environ.get("GENERATE_ORDER_SLIP", "true").lower() == "true"
GENERATE_PACKING_LIST = os.environ.get("GENERATE_PACKING_LIST", "false").lower() == "true"  # on once shipment leg proven
O_ORDER_ATTACHMENTS = "fldo34yjW8UPZKNJ5"        # Orders multipleAttachments — order slip PDF
SHIP_PACKING_LIST_ATTACH = "fldyEzJwoGg8Lpjem"   # Shipments multipleAttachments — packing list PDF

# Public hosting for the order-slip PDF: the sync uploads it to a public GCS bucket and
# writes the stable public URL into the Order Details field (O_ORDER_DETAILS). When the
# bucket env is unset the upload no-ops (the attachment still happens), so the sync runs
# safely before the bucket exists. The object path carries a random suffix so URLs are
# not enumerable (order slips carry customer + pricing data).
GCS_ORDER_SLIP_BUCKET = os.environ.get("GCS_ORDER_SLIP_BUCKET", "")
GCS_ORDER_SLIP_PREFIX = os.environ.get("GCS_ORDER_SLIP_PREFIX", "order-slips/")

# ===========================================================================
# Inventory sync  (Pipe17 Available -> HubSpot product properties)
# ===========================================================================
# Read-only from Pipe17 (Available per SKU per location), writes only HubSpot
# product-library number properties. Separate Cloud Run Job; touches no deals,
# orders, or the order/shipment sync.
PIPE17_INVENTORY_PATH = os.environ.get("PIPE17_INVENTORY_PATH", "/inventory")
PIPE17_LOCATIONS_PATH = os.environ.get("PIPE17_LOCATIONS_PATH", "/locations")
# The country buckets we surface to Sales. Every Pipe17 location is classified by
# its address.country, read live from /locations, so a new warehouse needs no
# code change. Availability sums across a base SKU's whole version family (bare +
# -V variants); the split is purely by warehouse country. inventory_total =
# inventory_us + inventory_ca (MX/BR excluded).
INVENTORY_COUNTRIES = ("US", "CA")
# One key reads org-wide inventory + locations (inventory is tenant-wide, not
# channel-scoped).
PIPE17_INVENTORY_READ_KEY = (os.environ.get("PIPE17_INVENTORY_READ_KEY")
                             or PIPE17_API_KEY_US or PIPE17_API_KEY)

# HubSpot product object + properties written back. inventory_us / inventory_ca /
# inventory_total are all number properties.
HS_PRODUCT_OBJECT = os.environ.get("HS_PRODUCT_OBJECT", "products")
HS_PRODUCT_SKU_PROP = os.environ.get("HS_PRODUCT_SKU_PROP", "hs_sku")
HS_INVENTORY_US_PROP = os.environ.get("HS_INVENTORY_US_PROP", "inventory_us")
HS_INVENTORY_CA_PROP = os.environ.get("HS_INVENTORY_CA_PROP", "inventory_ca")
HS_INVENTORY_TOTAL_PROP = os.environ.get("HS_INVENTORY_TOTAL_PROP", "inventory_total")

# --- Line-item inventory fan-out (same run, gated OFF) ----------------------
# HubSpot can't report on products, only on line items of deals, so the same
# inventory numbers are also stamped onto every line item of an active deal. This
# is a fan-out of the SAME per-SKU computation the product write uses, in the SAME
# run off one Pipe17 snapshot — product and line item can't drift. Our job writes
# ALL THREE numbers (us/ca/total) onto every active-deal line item unconditionally;
# it does NOT choose US vs CA. Bojana's HubSpot workflows read whichever applies to
# the deal's region and derive the inventory-health status. Build-only until the
# line-item properties exist in HubSpot and Ops is ready; with the flag off the
# inventory job behaves exactly as today (products only).
INVENTORY_LINE_ITEM_SYNC = os.environ.get("INVENTORY_LINE_ITEM_SYNC", "false").lower() == "true"
HS_LINE_ITEM_OBJECT = os.environ.get("HS_LINE_ITEM_OBJECT", "line_items")
# Line items created from a product carry its SKU on hs_sku (same prop Track B reads).
HS_LINE_ITEM_SKU_PROP = os.environ.get("HS_LINE_ITEM_SKU_PROP", HS_LI_PROPS["sku"])
# Line-item number properties. Default to the same internal names as the product
# props; override if HubSpot creates them under different names on the line_item
# object. CONFIRM the exact internal names before enabling the flag.
HS_LI_INVENTORY_US_PROP = os.environ.get("HS_LI_INVENTORY_US_PROP", HS_INVENTORY_US_PROP)
HS_LI_INVENTORY_CA_PROP = os.environ.get("HS_LI_INVENTORY_CA_PROP", HS_INVENTORY_CA_PROP)
HS_LI_INVENTORY_TOTAL_PROP = os.environ.get("HS_LI_INVENTORY_TOTAL_PROP", HS_INVENTORY_TOTAL_PROP)
# "Active deal" = these Sales Pipeline stages only (confirmed with Adam):
#   Qualified (qualifiedtobuy), Quote Sent (1523817), Buyer Engaged (11586560),
#   Committed Deal (1492994), Deposit Invoice Sent (1492995).
# Stage IDs are globally unique in HubSpot, so a dealstage IN filter needs no
# pipeline filter. Every other stage — including closed/won and anything past
# deposit — is excluded, so those deals' line items are never rewritten.
HS_ACTIVE_DEAL_STAGE_PROP = os.environ.get("HS_ACTIVE_DEAL_STAGE_PROP", "dealstage")
HS_ACTIVE_DEAL_STAGES = tuple(s for s in os.environ.get(
    "HS_ACTIVE_DEAL_STAGES",
    "qualifiedtobuy,1523817,11586560,1492994,1492995").split(",") if s)

# ===========================================================================
# Sync repair service  (ops-triggered heal of missed / damaged orders & SRs)
# ===========================================================================
# An Airtable form writes a "Sync Repair Requests" row; an Airtable automation POSTs
# it to this Cloud Run HTTP service (repair_service.py). The service diagnoses why the
# order/SR was missed, heals it through the normal sync mapping, and writes the result
# back onto the row + an Airtable comment + Slack. All field IDs are env-overridable so
# the table can be created in the Airtable UI and wired without code changes. See
# REPAIR_SETUP.md. With REPAIR_REQUESTS_TABLE unset the writeback no-ops safely.
REPAIR_REQUESTS_TABLE = os.environ.get("REPAIR_REQUESTS_TABLE", "")   # tbl... of the requests table
# Shared secret the Airtable automation sends as the X-Repair-Secret header; the service
# rejects any request without it. Keep it in Secret Manager, never in the repo.
REPAIR_WEBHOOK_SECRET = os.environ.get("REPAIR_WEBHOOK_SECRET", "")
# Form / request fields (ops fill the first few; the service owns the rest).
RF_PIPE17_ID = os.environ.get("RF_PIPE17_ID", "")        # singleLineText — order/SR number
RF_TYPE = os.environ.get("RF_TYPE", "")                  # singleSelect  — Order / Shipping Request / Auto
RF_FORCE = os.environ.get("RF_FORCE", "")                # checkbox      — heal even if not normally synced
RF_DETAILS = os.environ.get("RF_DETAILS", "")            # multilineText — what's wrong (ops notes)
RF_STATUS = os.environ.get("RF_STATUS", "")              # singleSelect  — service-owned
RF_DIAGNOSIS = os.environ.get("RF_DIAGNOSIS", "")        # multilineText — service writes why
RF_OUTCOME = os.environ.get("RF_OUTCOME", "")            # multilineText — service writes what it did
RF_PROCESSED_AT = os.environ.get("RF_PROCESSED_AT", "")  # dateTime      — service writes
# Status option strings the service writes (must exist on the RF_STATUS single-select).
REPAIR_STATUS_PROCESSING = os.environ.get("REPAIR_STATUS_PROCESSING", "Processing")
REPAIR_STATUS_HEALED = os.environ.get("REPAIR_STATUS_HEALED", "Healed")
REPAIR_STATUS_NOT_HEALED = os.environ.get("REPAIR_STATUS_NOT_HEALED", "Could Not Heal")
REPAIR_STATUS_REVIEW = os.environ.get("REPAIR_STATUS_REVIEW", "Needs Review")
# Request Type option strings (what ops pick on the form).
REPAIR_TYPE_ORDER = os.environ.get("REPAIR_TYPE_ORDER", "Order")
REPAIR_TYPE_SR = os.environ.get("REPAIR_TYPE_SR", "Shipping Request")
REPAIR_TYPE_AUTO = os.environ.get("REPAIR_TYPE_AUTO", "Auto")

# ===========================================================================
# Inventory hold (V2) — Track B second trigger: lock inventory for a deal before
# it's paid, on the SAME Pipe17 order the Ready-for-Ops flow later releases.
# Build-only / dry-run testable. Gated OFF by default; when disabled, Track B
# behaves exactly as before (this whole path is skipped).
# ===========================================================================
INVENTORY_HOLD_ENABLED = os.environ.get("INVENTORY_HOLD_ENABLED", "false").lower() == "true"

# HubSpot deal signals.
HS_INVENTORY_HOLD_PROP = os.environ.get("HS_INVENTORY_HOLD_PROP", "inventory_hold_approved")  # bool
HS_DEAL_STAGE_PROP = os.environ.get("HS_DEAL_STAGE_PROP", "dealstage")
# Post-quote, pre-Ready-for-Ops stages where a hold can be placed (Quote Sent,
# Buyer Engaged, Committed Deal, Deposit Invoice Sent). Env override is comma-sep.
# Same active sales stages the line-item inventory fan-out uses (minus qualifiedtobuy).
HS_HOLD_STAGE_IDS = tuple(s for s in os.environ.get(
    "HS_HOLD_STAGE_IDS", "1523817,11586560,1492994,1492995").split(",") if s)
HS_READY_FOR_OPS_STAGE_ID = os.environ.get("HS_READY_FOR_OPS_STAGE_ID", HS_DEPOSIT_PAID_STAGE_ID)  # 10492961
HS_LOST_STAGE_ID = os.environ.get("HS_LOST_STAGE_ID", "closedlost")  # stage supersedes the checkbox

# Every order (hold or confirmed) is created at Ready-for-Fulfillment so inventory
# commits and shipping requests are generated; an Ops tag-automation parks the SRs
# in Review Required until released. Distinguished by tag:
#   held  -> B2BInventoryHold, NOT Airtable (Track A ignores unpaid holds)
#   confirmed/released -> B2BConfirmReview + Airtable (Ops reviews, then releases)
# The two review tags each need a matching Pipe17 order-automation (Ops/Harry own
# those). Names are env-overridable so they match whatever the automations key on.
PIPE17_RFF_STATUS = os.environ.get("PIPE17_RFF_STATUS", "readyForFulfillment")
PIPE17_HOLD_TAG = os.environ.get("PIPE17_HOLD_TAG", "B2BInventoryHold")
PIPE17_CONFIRM_REVIEW_TAG = os.environ.get("PIPE17_CONFIRM_REVIEW_TAG", "B2BConfirmReview")

# Visible "do not release" note written onto a held Pipe17 order, on top of the tag
# (Drew/Adam 10/27: a caps note so anyone opening the order sees it is not an active
# order). Only set on HOLD creates; the confirmed/released transition leaves notes
# alone so Ops edits survive. PIPE17_HOLD_NOTE_FIELD is the order field it writes to —
# confirm Pipe17 accepts it before go-live (the first live hold should be a single
# --deal-id test). Set PIPE17_HOLD_NOTE or PIPE17_HOLD_NOTE_FIELD to "" to skip it.
PIPE17_HOLD_NOTE = os.environ.get(
    "PIPE17_HOLD_NOTE", "INVENTORY HOLD — DO NOT RELEASE until deal reaches Ready for Ops")
PIPE17_HOLD_NOTE_FIELD = os.environ.get("PIPE17_HOLD_NOTE_FIELD", "notes")
# The note is CLEARED on the Ready-for-Ops flip (held -> confirmed), when the order
# also picks up the Airtable tag — so a released order never carries "DO NOT RELEASE".

# Reportable hold flag as a custom field on the SO, in addition to the tag (Gina:
# surface the hold as a custom field so it's reportable in Pipe17). Set at create and
# on the released transition. Empty name disables it.
PIPE17_HOLD_CF_NAME = os.environ.get("PIPE17_HOLD_CF_NAME", "inventory_hold")
PIPE17_HOLD_CF_HELD = os.environ.get("PIPE17_HOLD_CF_HELD", "true")
PIPE17_HOLD_CF_RELEASED = os.environ.get("PIPE17_HOLD_CF_RELEASED", "false")

# Slack alert to Sales Ops when a held order flips to Ready for Ops (held -> confirmed),
# so they know to review and release its shipping requests.
HOLD_RFO_SLACK_ALERT = os.environ.get("HOLD_RFO_SLACK_ALERT", "true").lower() == "true"

# REST path for order update (Track B write path). Template takes {id}.
# Pipe17 v3 updates an order with PUT /api/v3/orders/{orderId} (the method is in
# pipe17_write). Cancellation uses the SAME PUT, setting status=PIPE17_CANCELED_STATUS
# — Pipe17 v3 has no dedicated cancel endpoint; status is a settable field on PUT.
PIPE17_ORDER_UPDATE_PATH = os.environ.get("PIPE17_ORDER_UPDATE_PATH", "/orders/{id}")
PIPE17_CANCELED_STATUS = os.environ.get("PIPE17_CANCELED_STATUS", "canceled")

# ===========================================================================
# SO finance fields (Finance / Drew 10/27): carry FULL RETAIL unit prices at the
# line-item level plus a single order-level discount "on the bottom", instead of
# net unit prices. Retail unit = HubSpot line `price`; discount = (price*qty) - net
# `amount`, summed across lines. The order total owed is unchanged (net + tax), so
# reconciliation is unaffected; only subTotalPrice becomes retail and the discount is
# surfaced. Gated OFF by default — when off, Track B sends net itemPrice exactly as
# before. Dry-run and verify one order (confirm PIPE17_ORDER_DISCOUNT_FIELD is the
# field Pipe17 actually reads) before enabling.
SO_RETAIL_PRICING = os.environ.get("SO_RETAIL_PRICING", "false").lower() == "true"
PIPE17_ORDER_DISCOUNT_FIELD = os.environ.get("PIPE17_ORDER_DISCOUNT_FIELD", "discount")
