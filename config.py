"""Configuration + field maps for the Pipe17 -> Airtable sync.

Wired to the live schemas discovered on 2026-08-18:
  - Airtable base "US - Branch Project Tracker (New)"  appYsxq2ZGOz2z5ND
  - Orders     table  tbltJfWGsMAeijlV2  (primary: "Order Number")
  - Shipments  table  tblp2rEKFhMvwFdtw  (primary: "Shipment Number")
  - Pipe17 shipping-request payload confirmed against order #TestCA1040 (6 splits).

Secrets (PIPE17_API_KEY, AIRTABLE_API_KEY) come from the environment. On GCP inject
them with Cloud Run `--set-secrets` pinned to `:latest` (NOT `:1`).
"""
import os

# --- Pipe17 -----------------------------------------------------------------
PIPE17_API_BASE = os.environ.get("PIPE17_API_BASE", "https://api.pipe17.com/v1")
PIPE17_API_KEY = os.environ["PIPE17_API_KEY"]
# Pipe17 auth header. VERIFY against your API creds — commonly "X-Api-Key".
PIPE17_AUTH_HEADER = os.environ.get("PIPE17_AUTH_HEADER", "X-Api-Key")
# Pipe17 calls shipping requests "shipments" in the REST API (response key "shipments").
PIPE17_SHIPMENTS_PATH = os.environ.get("PIPE17_SHIPMENTS_PATH", "/shipments")
PIPE17_ORDERS_PATH = os.environ.get("PIPE17_ORDERS_PATH", "/orders")

# Each run pulls records updated in the last N minutes. Keep Scheduler cadence <= this.
SYNC_LOOKBACK_MINUTES = int(os.environ.get("SYNC_LOOKBACK_MINUTES", "60"))
# Whether to also upsert the parent Order row from Pipe17. Default off: Orders are
# created upstream (HubSpot/Shopify) and we only link to them. Flip on if Pipe17
# should own the parent Order too. (Open question — confirm vs skubana behavior.)
SYNC_ORDERS = os.environ.get("SYNC_ORDERS", "false").lower() == "true"

# --- Airtable ---------------------------------------------------------------
AIRTABLE_API_KEY = os.environ["AIRTABLE_API_KEY"]
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appYsxq2ZGOz2z5ND")
ORDERS_TABLE = os.environ.get("ORDERS_TABLE", "tbltJfWGsMAeijlV2")
SHIPMENTS_TABLE = os.environ.get("SHIPMENTS_TABLE", "tblp2rEKFhMvwFdtw")

# Primary fields used as upsert merge keys.
ORDER_NUMBER_FIELD = "Order Number"        # Orders primary
SHIPMENT_NUMBER_FIELD = "Shipment Number"  # Shipments primary
# Link field on Shipments -> Orders.
SHIPMENT_ORDER_LINK_FIELD = "Order Link"   # fldS43vViZGpbffFJ (multipleRecordLinks)

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
# Pipe17 splits use a dot (#TestCA1040.2); the Airtable base parses skubana's
# parenthesis form (#TestCA1040(2)). Keep True so existing formulas/automations
# ("Parent Order (FX)", "Child / Parent - Relational Database") keep working.
NORMALIZE_SHIPMENT_NUMBER = os.environ.get("NORMALIZE_SHIPMENT_NUMBER", "true").lower() == "true"
