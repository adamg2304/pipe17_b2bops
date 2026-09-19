# Pipe17 <-> Airtable sync

Two Cloud Run jobs feeding the Airtable base *US - Branch Project Tracker (New)*
(`appYsxq2ZGOz2z5ND`) from Pipe17, replacing the skubana shipment feed and, for B2B,
the HubSpot -> Airtable order connector:

1. **Shipments** (live-validated): Pipe17 shipping requests -> Airtable **Shipments**.
2. **Orders** (built, dry-runnable): Pipe17 orders -> Airtable **Orders**, gated by `SYNC_ORDERS`.

GCP project `pipe17-b2bops`, region `europe-west1`, deployed as Cloud Run Job
`pipe17-airtable-sync` from local source.

## Status (2026-09-17)

- **Transport CONFIRMED.** Base `https://api-v3.pipe17.com/api/v3`, header `X-Pipe17-Key`,
  shipping requests at `/shipments`, orders at `/orders`, incremental param `updatedSince`.
- **Shipments: green dry run.** A 30-day window pulled 12 shipping requests, mapped every
  field correctly, and wrote nothing. Order-Link resolution behaves correctly (leaves the
  link blank when no parent Order exists yet, verified against the live base).
- **Orders: built, not yet dry-run in GCP.** `order_transform.py` + the `SYNC_ORDERS`
  branch map orders to the Orders table. Passes `selftest_orders.py` offline.
- **Writes are keyed by Airtable FIELD IDs, not names**, so a column rename in the UI can't
  silently break either sync. The one exception is the parent-order lookup, which uses the
  field name because Airtable's `filterByFormula` only accepts names.

## Tag gate

Only shipping requests / orders whose parent order carries the Pipe17 **`Airtable`** tag
sync. Set in Pipe17 (orderbot on the Enterprise US/CA channels). Enforced client-side in
`main.py` as well as server-side. `PIPE17_TAG_FILTER` (default `Airtable`); set it empty
to disable, which is for DRY_RUN validation only, never with writes on.

## Shipments field map (Pipe17 shipping request -> Shipments)

| Airtable field | Field ID | Pipe17 source |
|---|---|---|
| Shipment Number *(merge key)* | `fldAo9nWuRVj9lG7p` | `extShipmentId` (raw dot form kept) |
| Order Link | `fldS43vViZGpbffFJ` | `extOrderId` -> Orders lookup (by name) |
| Origin WH | `fldlOvl9BxQsoEow9` | `locationId` via `LOCATION_MAP` |
| Status | `fldn5soXXtCFKA3K1` | `status` via `STATUS_MAP` |
| Customer Name | `fldNDEpstqH42sQkl` | `shippingAddress.firstName` + `lastName` |
| Delivery Address | `fldxhQ2SUEGDvOHS8` | `shippingAddress.address1` |
| City / Zip / State | `fldXNEOFLrJL6ag5d` / `fldh4QCyKa6JFI1wQ` / `fldxyu1jGoYMS1rA5` | `shippingAddress.*` |
| Customer Email | `fldH76ux37k5Hmhnw` | `shippingAddress.email` |
| Line Items | `fldrmuH8qsLeBDAtr` | `lineItems[]` |
| Shipment Creation Date | `fldAR0iUA9liD09QA` | `createdAt` (date) |

Nomenclature: Pipe17's raw dot form (`#TestCA1040.2`) is preserved so ids round-trip to
Pipe17. The Airtable **Parent Order (FX)** formula was made delimiter-agnostic (handles
`(`, `.`, or bare), so the Child/Parent automation keeps working. `NORMALIZE_SHIPMENT_NUMBER`
stays `false`.

## Orders field map (Pipe17 order -> Orders)

Mirrors the old HubSpot/Shopify connector's target fields, sourced from Pipe17. Poll orders
in status `new` (after the man-in-the-loop review in Pipe17) carrying the `Airtable` tag,
upsert merging on Order Number.

| Airtable field | Field ID | Pipe17 source |
|---|---|---|
| Order Number *(merge key)* | `fldE5XFShmJ2VZOzV` | `extOrderId` |
| Order Date | `fldEyOAQnE91pEhi6` | `extOrderCreatedAt` (date) |
| Status | `fldrMgGTKYqowebcx` | seed `"No Shipments Found"` |
| Deal Value | `fldnavBgYF5c8hCzL` | `totalPrice` |
| Delivery Address / Suite / City / State / Zip | `fldBYEkOH9umhsPkE` / `fldW4QfIYHWRd9OD1` / `fld1us6ByWxuSOoxJ` / `fldBfijftTMvEFz2B` / `fldlLEUuxFUsrm9Ym` | `shippingAddress.*` |
| Pipe17 Order ID | `fldz8upok6ZOlv9Zv` | `orderId` (hex; replaces Shopify Order ID) |
| Order Tags | `fldkYsJf29f50gvVY` | `tags[]` minus the `Airtable` gate tag |
| Order Line Items | `fldMKgX11B2IypTzY` | `{"Product ID":[],"Quantity":[...],"SKU":[...]}`, service lines dropped |
| Customer Name / Email / Phone / Company | `fldRksHogVvNO6YMR` / `fldLOUMtQC8Ot0pP2` / `fldcx96tdKbTmOXiq` / `flduqdNcA7M9PkZlj` | Pipe17 **customer** object (see Track B dependency) |
| Deal Name | `fldhYD7tAzZwc1Aw2` | `Order# - Company - Customer Name` |

Not written by the sync (filled by later steps): **Order Details** (`fldCln1sbb4Rx3rGS`) and
**Order Attachments** get the generated order-slip PDF; **Notes** (`fldzxyuljaUM82P3W`) is
composed downstream (QBO invoice, etc.).

**Track B dependency (contact):** the Pipe17 order's `shippingAddress` carries only a
concatenated company string, no person name, email, or phone. The clean customer data must
come from the Pipe17 **customer** object, which the HubSpot -> Pipe17 flow (Track B) needs to
create or look up by email at order creation. `main.py` already fetches it via
`get_customer(order.customerId)`; until Track B populates it, contact fields come through
blank and everything else maps.

## Doc generation (planned)

- **Order slip** (order-level): built from the order's line items (parent products), based on
  the White Glove HTML template.
- **Packing list** (shipment-level): built from the shipping request's line items, which are
  the actual child/box SKUs Pipe17 fans an order into, not the finished goods.

Both render HTML -> PDF, upload to Google Drive, and write the Drive link/file back into the
Airtable record (order slip on the Order, packing list on the Shipment), the same pattern the
current order docs use.

## Tests

Offline, no GCP or creds (secrets are read at import, so pass dummy values):

```bash
# shipments transform over the 6-split #TestCA1040 fixture
PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest.py

# orders transform over the real #BE64737059217 payload (24 assertions)
PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_orders.py

# whole import graph loads + transport is correct
PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 -c \
  "import main; import config as c; print('OK', c.PIPE17_AUTH_HEADER, c.PIPE17_API_BASE)"
```

Live dry runs in Cloud Shell (writes nothing; `DRY_RUN=true`):

```bash
# shipments, tag gate off + 30-day window to see records map
gcloud run jobs update pipe17-airtable-sync --region europe-west1 \
  --update-env-vars=SYNC_ORDERS=false,PIPE17_TAG_FILTER=,SYNC_LOOKBACK_MINUTES=43200,DRY_RUN=true
gcloud run jobs execute pipe17-airtable-sync --region europe-west1 --wait

# orders, gate on
gcloud run jobs update pipe17-airtable-sync --region europe-west1 \
  --update-env-vars=SYNC_ORDERS=true,PIPE17_TAG_FILTER=Airtable,SYNC_LOOKBACK_MINUTES=43200,DRY_RUN=true
gcloud run jobs execute pipe17-airtable-sync --region europe-west1 --wait

# read the log
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="pipe17-airtable-sync"' \
  --project pipe17-b2bops --limit 50 --freshness=15m --format='value(textPayload)' --order=asc
```

## Deploy

```bash
cd ~/pipe17_b2bops
gcloud run jobs deploy pipe17-airtable-sync --source . --region europe-west1 \
  --tasks 1 --max-retries 1 \
  --set-secrets=PIPE17_API_KEY=pipe17-api-key:latest,AIRTABLE_API_KEY=airtable-pat:latest \
  --set-env-vars=SYNC_LOOKBACK_MINUTES=60,DRY_RUN=true
# once validated, drop DRY_RUN and add the Scheduler trigger (cadence <= lookback)
```

## Confirm before prod

- [ ] Shipments: apply the `Airtable` tag to real B2B orders, then a first live write on one
      tagged order with `DRY_RUN=false`, and verify landing + Order Link + that automations behaved.
- [ ] Orders: change **Pipe17 Order ID** confirmed text (done); dry-run with `SYNC_ORDERS=true`.
- [ ] **Track B customer** create/lookup by email, so orders carry contact.
- [ ] `LOCATION_MAP` filled from Pipe17 `list-locations`.
- [ ] `STATUS_MAP` signed off by Drew/Ops.
- [ ] Doc generators (order slip, packing list) + Drive.
- [ ] Parallel run vs skubana through September; cutover target 9/30.

## Files

`config.py` (ids, maps, merge keys, both syncs) · `pipe17_client.py` (paginated reads,
`iter_shipping_requests`, `iter_orders`, `get_customer`) · `airtable_client.py` (upsert +
order lookup) · `transform.py` (shipment map) · `order_transform.py` (order map) ·
`main.py` (job entrypoint, `sync_orders` + shipment loop) · `Dockerfile` · `requirements.txt` ·
`selftest.py` · `selftest_orders.py`
