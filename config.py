"""Configuration + field maps for the Pipe17 -> Airtable sync.

Wired to the live schemas discovered on 2026-08-18:
  - Airtable base "US - Branch Project Tracker (New)"  appYsxq2ZGOz2z5ND
  - Orders     table  tbltJfWGsMAeijlV2  (primary: "Order Number")
  - Shipments  table  tblp2rEKFhMvwFdtw  (primary: "Shipment Number")
  - Pipe17 shipping-request payload confirmed against order #TestCA1040 (6 splits).

Writes are keyed by Airtable FIELD IDs (pulled from the live base 2026-09-03) so a
column rename in the Airtable UI can never silently break the sync. The ONE exception
is the parent-order lookup (ORDER_NUMBER_FIELD): Airtable's REST filterByFormula only
accepts field NAMES, never IDs, so that stays a name by necessity.

Secrets (PIPE17_API_KEY, AIRTABLE_API_KEY) come from the environment. On GCP inject
them with Cloud Run `--set-secrets` pinned to `:latest` (NOT `:1`).
"""
import os

# --- Pipe17 -----------------------------------------------------------------
PIPE17_API_BASE = os.environ.get("PIPE17_API_BASE", "https://api-v3.pipe17.com/api/v3")
PIPE17_API_KEY = os.environ["PIPE17_API_KEY"]
# Pipe17 auth header. CONFIRMED from Pipe17 docs + help center: custom header
# "X-Pipe17-Key" (NOT Authorization, NOT X-Api-Key).
PIPE17_AUTH_HEADER = os.environ.get("PIPE17_AUTH_HEADER", "X-Pipe17-Key")
# Incremental-read param name. Pipe17 examples show "since" on some resources and
# "updatedSince" on others. Confirm which /shipments accepts (see selftest notes),
# then override via env if needed. This is the last transport unknown.
PIPE17_SINCE_PARAM = os.environ.get("PIPE17_SINCE_PARAM", "updatedSince")
# Pipe17 calls shipping requests "shipments" in the REST API (response key "shipments").
PIPE17_SHIPMENTS_PATH = os.environ.get("PIPE17_SHIPMENTS_PATH", "/shipments")
PIPE17_ORDERS_PATH = os.environ.get("PIPE17_ORDERS_PATH", "/orders")

# Each run pulls records updated in the last N minutes. Keep Scheduler cadence <= this.
SYNC_LOOKBACK_MINUTES = int(os.environ.get("SYNC_LOOKBACK_MINUTES", "60"))
# Whether to also upsert the parent Order row from Pipe17. Default off: Orders are
# created upstream (HubSpot/Shopify) and we only link to them. Flip on if Pipe17
# should own the parent Order too. (Open question — confirm vs skubana behavior.)
SYNC_ORDERS = os.environ.get("SYNC_ORDERS", "false").lower() == "true"
# Dry run: fetch + map + resolve links + LOG everything, but write NOTHING to
# Airtable. The safe on-ramp for the first real run once creds exist. Default ON so
# a misconfigured deploy can never write to the live base by accident.
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

# --- Airtable ---------------------------------------------------------------
AIRTABLE_API_KEY = os.environ["AIRTABLE_API_KEY"]
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appYsxq2ZGOz2z5ND")
ORDERS_TABLE = os.environ.get("ORDERS_TABLE", "tbltJfWGsMAeijlV2")
SHIPMENTS_TABLE = os.environ.get("SHIPMENTS_TABLE", "tblp2rEKFhMvwFdtw")

# --- Airtable field IDs (immutable — survive UI renames) --------------------
# Pulled from the live base 2026-09-03. Every write and the upsert merge key use
# these IDs. Do NOT swap them back to names.
#
# Shipments (tblp2rEKFhMvwFdtw)
F_SHIPMENT_NUMBER        = "fldAo9nWuRVj9lG7p"  # singleLineText — primary / merge key
F_ORDER_LINK             = "fldS43vViZGpbffFJ"  # multipleRecordLinks -> Orders
F_ORIGIN_WH              = "fldlOvl9BxQsoEow9"  # singleSelect (gated by LOCATION_MAP)
F_STATUS                 = "fldn5soXXtCFKA3K1"  # singleSelect (gated by STATUS_MAP)
F_CUSTOMER_NAME          = "fldNDEpstqH42sQkl"  # singleLineText
F_DELIVERY_ADDRESS       = "fldxhQ2SUEGDvOHS8"  # singleLineText
F_CITY                   = "fldXNEOFLrJL6ag5d"  # singleLineText
F_ZIP_CODE               = "fldh4QCyKa6JFI1wQ"  # singleLineText
F_STATE                  = "fldxyu1jGoYMS1rA5"  # singleSelect (!) — typecast risk, see transform.py
F_CUSTOMER_EMAIL         = "fldH76ux37k5Hmhnw"  # email
F_LINE_ITEMS             = "fldrmuH8qsLeBDAtr"  # singleLineText
F_SHIPMENT_CREATION_DATE = "fldAR0iUA9liD09QA"  # date
#
# Orders (tbltJfWGsMAeijlV2)
F_ORDER_NUMBER           = "fldE5XFShmJ2VZOzV"  # singleLineText — primary

# Human labels for the field IDs above — handy for readable DRY_RUN logs / debugging.
FIELD_LABELS = {
    F_SHIPMENT_NUMBER: "Shipment Number",
    F_ORDER_LINK: "Order Link",
    F_ORIGIN_WH: "Origin WH",
    F_STATUS: "Status",
    F_CUSTOMER_NAME: "Customer Name",
    F_DELIVERY_ADDRESS: "Delivery Address",
    F_CITY: "City",
    F_ZIP_CODE: "Zip Code",
    F_STATE: "State",
    F_CUSTOMER_EMAIL: "Customer Email",
    F_LINE_ITEMS: "Line Items",
    F_SHIPMENT_CREATION_DATE: "Shipment Creation Date",
}

# Merge key + link field consumed by main.py (now FIELD IDs, not names).
SHIPMENT_NUMBER_FIELD = F_SHIPMENT_NUMBER
SHIPMENT_ORDER_LINK_FIELD = F_ORDER_LINK

# Parent-order lookup field — NAME, not ID, DELIBERATELY.
# Airtable's REST filterByFormula (used by find_record_id) only accepts field NAMES,
# never field IDs — confirmed in Airtable's own API docs. This is the single place a
# name is unavoidable. It's the Orders primary field (least likely to be renamed);
# add an Airtable field description on "Order Number" noting this sync depends on it.
ORDER_NUMBER_FIELD = "Order Number"

# --- locationId -> Shipments "Origin WH" single-select option ---------------
# Confirmed: Mississauga. Fill the rest from Pipe17 list-locations (values must match
# the Origin WH option names exactly, or they'll be skipped rather than mis-created).
LOCATION_MAP = {
    "fcf8381c1cffc669": "Mantoria - Mississauga (MTO)",
    # "<locationId>": "Inland Star - Harrisburg (HBG)",
    # "<locationId>": "Inland Star - Fresno (FRN)",
    # "<locationId>": "Inland Star - Visalia (VIS)",
    # "<locationId>": "Mantoria - Surrey (MBC)",
    # "<locationId>": "Branch NC (BNC)",
    # ... (see Origin WH options in the base)
}

# --- Pipe17 shipment status -> Shipments "Status" single-select -------------
# Pipe17 statuses: draft, new, readyToShip, readyForFulfillment, partialShipped,
# shipped, inTransit, delivered, canceled, returned.
# Airtable Status is an Ops-flow field, NOT 1:1. These are best-guess -> CONFIRM with
# Drew/Ops. Anything unmapped is left blank (we do NOT auto-create new Status options).
STATUS_MAP = {
    "draft": None,
    "new": "Processing",
    "readyForFulfillment": "Processing",
    "readyToShip": "Ready to Book",
    "partialShipped": "In-Transit",
    "shipped": "In-Transit",
    "inTransit": "In-Transit",
    "delivered": "Arrived",
    "canceled": "Cancelled",
    "returned": "Cancelled",
}
# Fallback when a Pipe17 status isn't in STATUS_MAP (None = leave Status blank).
DEFAULT_SHIPMENT_STATUS = None

# --- Shipment number nomenclature -------------------------------------------
# DECISION (reversed): do NOT normalize. Keep Pipe17's raw dot form (#TestCA1040.2)
# in Airtable so shipment ids round-trip back to Pipe17 cleanly. Instead the Airtable
# "Parent Order (FX)" formula was made delimiter-agnostic (handles "(" or "." or bare),
# so existing formulas/automations keep working without a data transformation.
# Left as a flag for optionality, but default OFF.
NORMALIZE_SHIPMENT_NUMBER = os.environ.get("NORMALIZE_SHIPMENT_NUMBER", "false").lower() == "true"
