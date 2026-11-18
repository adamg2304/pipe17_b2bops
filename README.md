# pipe17_b2bops — Branch B2B order + logistics pipeline

Two GCP Cloud Run **Jobs**, one shared image, one shared `config.py`. Replaces Shopify
for B2B order entry and skubana for the shipment feed.

```
HubSpot deal (deposit paid, stage 10492961)
   │  Track B — main_hubspot_to_pipe17.py   (poll deals -> create Pipe17 DRAFT order)
   ▼
Pipe17 draft sales order  --(Ops confirms Draft->New in Pipe17)-->  shipping requests
   │                                                                      │
   │  Track A (orders) — main.py / sync_orders        Track A (shipments) — main.py / sync_shipments
   ▼                                                                      ▼
Airtable Orders  <--------------- Order Link ---------------  Airtable Shipments
```

Everything is gated by the Pipe17 **`Airtable`** tag, so only B2B orders reach Airtable.
Transport (both tracks): base `https://api-v3.pipe17.com/api/v3`, header `X-Pipe17-Key`.

## Track A — Pipe17 -> Airtable (`main.py`)
Poller Job. Runs `sync_orders` (Pipe17 orders in status `new` + tag -> Airtable Orders,
upsert on Order Number) then `sync_shipments` (shipping requests -> Airtable Shipments,
upsert on Shipment Number, Order Link resolved by Order Number). Writes are keyed by
Airtable **field IDs**. A fail-safe aborts any live run with the tag gate disabled.
Order contact (name/email/phone/company) is read from the Pipe17 **customer** object,
which Track B attaches.

### Shipments key: Pipe17 shipping request ID
Shipments rows are keyed on the Pipe17 **shipping request ID** (`shipmentId`, field
**Pipe17 Shipping Request ID**). It is the only upsert key. **Shipment Number**
(`extShipmentId`) is still written for display, but nothing matches on it: when a backorder
SR is canceled and re-routed, Pipe17 often gives the new SR the same number
(#BE63027606419 has two SRs on `#BE63027606419` and two on `#BE63027606419.2`).

Each run collects the orders behind the tagged SRs updated in the window, then syncs each
order as a whole (`shipment_sync.sync_order`): it reads ALL the order's SRs, whatever their
tag or channel, and writes one row per SR. Only changed fields are written, so a re-run on
unchanged data writes nothing. Rules:
- **Status** is forward-only. Once a row reaches Processing (sent to fulfillment) it is
  never moved to an earlier status, and a status Ops set by hand is kept.
- **Backordered**: a live SR on a backorder location (listed in `BACKORDER_LOCATION_IDS`,
  "backorder" in the location name, or the `no-fulfillment-integration` integration). No
  packing list or shipping document is made until the SR is sent to fulfillment.

**Replacement sweep.** For each canceled SR (`canceled` / `canceledRestock`) the sweep
looks up its line `uniqueId`s on the order's live SRs created after it. Line IDs survive a
cancel-and-reroute, so this finds the replacement(s): a split gives several, and a chain
(canceled twice) resolves to the newest live SR. The canceled row gets Status **Replaced**,
**Replaced By** → the replacement row(s), and each replacement gets **Replaces** ← the
canceled row(s). Notes and Shipment Deficiency Detail (`REPLACEMENT_COPY_FIELDS`) are
copied onto a replacement when its own value is empty. If a line the order still needs has
no live SR `ORPHAN_GRACE_MINUTES` (30) after the cancel, the row stays **Cancelled**, is
flagged **Orphaned**, and one Slack alert is sent. Every run also re-sweeps orders that
still have an unsettled canceled row from the last `SWEEP_RECHECK_DAYS` (14), so a late
replacement or the orphan grace is picked up even when nothing on the order changed.

Check one order without writing:
```bash
DRY_RUN=true python main.py --order '#BE63027606419'
```

**Backfill (run once, before deploying the keyed sync).** Existing rows have no shipping
request ID, and the keyed sync never adopts a row by Shipment Number, so un-backfilled rows
would get a duplicate the next time their SR changes.
```bash
python backfill_shipment_ids.py                      # dry run; writes backfill_shipment_ids.csv
python backfill_shipment_ids.py --order '#BE63027606419'
python backfill_shipment_ids.py --apply              # write the unambiguous matches
```
For each unkeyed Pipe17 row it scores the SRs with the row's Shipment Number on line
items (SKU + qty from the Line Items text; Airtable never stored line IDs), Origin WH, and
creation time (an SR created after the row cannot own it, which catches rows a later SR
overwrote). It writes only when exactly one SR passes all three. Everything else is in the
CSV with the reason, each candidate's scores and, where one SR is time-consistent, a
suggested ID to paste into **Pipe17 Shipping Request ID** by hand.

### Shipment Details + link
When a shipment is sent to fulfillment, the sync attaches its packing list to **Shipment
Details** and publishes the same PDF to the public GCS bucket (`GCS_ORDER_SLIP_BUCKET`,
under `packing-lists/`). The URL goes in **Shipment Details Link**, which the Hub reads.
A row with a file but no link gets the attached file published on its next sync. To
backfill every such Pipe17-created row at once, run the `pipe17-airtable-sync` job from the **Run job**
workflow with args `main.py,--backfill-shipment-links`. It is idempotent.

### Operations Pipeline replacement
Track A also creates the Airtable **Hubspot Deals** row for each committed deal and pushes
Airtable order **Status** to the deal's `operations_status` property (replacing two Zapier
zaps and the HubSpot Operations Pipeline). Details and go-live steps: `DEPLOY.md`.

## Track B — HubSpot -> Pipe17 (`main_hubspot_to_pipe17.py`)
Poller Job. Finds deposit-paid deals (stage `10492961`) without a `pipe17_order_id`,
builds a Pipe17 **draft** order, and creates it with the currency-appropriate key (USD ->
US key, CAD -> CA key; the key selects the channel, there is no channelId). Idempotent:
skips already-synced deals, re-checks Pipe17 by `extOrderId`, writes the Pipe17 id back.
- `extOrderId` = `#BE<dealId>` (USD) / `#CEN<dealId>` (CAD) — this is Track A's Order Number.
- Pricing is **net**, from line items (`itemPrice` = line net / qty); subtotal is summed
  from line items, not the deal Amount. Service SKUs flagged `requiresShipping: false`.
- Builds the Pipe17 `customer` from the deal's "End User - Delivery Contact".
- Tax deferred (Shopify via Alex): set `HS_TAX_TOTAL_PROP` when the value lands.

## Files
Shared: `config.py`, `pipe17_client.py` (Track A read), `pipe17_write.py` (Track B write),
`Dockerfile`, `requirements.txt`.
Track A: `main.py`, `transform.py`, `order_transform.py`, `airtable_client.py`,
`reconcile_orders.py`, `selftest.py`, `selftest_orders.py`, `fixtures/testca1040.json`.
Track B: `main_hubspot_to_pipe17.py`, `hubspot_client.py`, `transform_order.py`,
`walkthrough.py`, `selftest_trackb.py`, `TRACKB_CONFIG_REFERENCE.md`.

## Tests (offline; no creds, no network)
```bash
PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest.py          # shipments transform
PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_orders.py   # orders transform (24)
PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_trackb.py   # HubSpot->Pipe17 transform (17)
```
Live parallel-run diff for Track A orders (needs creds, writes nothing):
```bash
PIPE17_API_KEY=$(gcloud secrets versions access latest --secret=pipe17-api-key) \
AIRTABLE_API_KEY=$(gcloud secrets versions access latest --secret=airtable-pat) \
LOOKBACK_MIN=43200 python3 reconcile_orders.py
```
Track B single-deal walkthrough (needs creds):
```bash
source .env.sh
DRY_RUN=true  python3 walkthrough.py --deal-id <HUBSPOT_DEAL_ID>   # preview
DRY_RUN=false python3 walkthrough.py --deal-id <HUBSPOT_DEAL_ID>   # create
```

## Deploy — two Jobs, one image (europe-west1, project pipe17-b2bops)

Secrets (create once): `pipe17-api-key` + `airtable-pat` (Track A), plus
`pipe17-api-key-us`, `pipe17-api-key-ca`, `hubspot-token` (Track B).

Track A:
```bash
gcloud run jobs deploy pipe17-airtable-sync --source . --region europe-west1 \
  --tasks 1 --max-retries 1 \
  --set-secrets=PIPE17_API_KEY=pipe17-api-key:latest,AIRTABLE_API_KEY=airtable-pat:latest \
  --set-env-vars=SYNC_ORDERS=true,PIPE17_TAG_FILTER=Airtable,DRY_RUN=true,SYNC_LOOKBACK_MINUTES=60
```

Track B (same source, override the entrypoint):
```bash
gcloud run jobs deploy hubspot-pipe17-orders --source . --region europe-west1 \
  --tasks 1 --max-retries 1 \
  --command python --args main_hubspot_to_pipe17.py \
  --set-secrets=PIPE17_API_KEY_US=pipe17-api-key-us:latest,PIPE17_API_KEY_CA=pipe17-api-key-ca:latest,HUBSPOT_TOKEN=hubspot-token:latest \
  --set-env-vars=DRY_RUN=true,HS_LOOKBACK_MINUTES=60,HS_DEPOSIT_PAID_STAGE_ID=10492961
```

Both default `DRY_RUN=true`. Validate the logs, then redeploy with `DRY_RUN=false` and add
Cloud Scheduler triggers (cadence <= the lookback). Rotate the credentials that were shared
in plaintext during the build.
