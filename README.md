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
