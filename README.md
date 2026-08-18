# Pipe17 -> Airtable sync (Shipping Requests -> Shipments)

Replaces the skubana -> Airtable feed. Pulls Pipe17 **shipping requests** and lands
them in the Airtable **Shipments** table (base *US - Branch Project Tracker (New)*),
linked to their parent **Order** — mirroring the skubana shipment shape the existing
Ops workflows depend on.

Wired to live schemas (verified 2026-08-18 against order **#TestCA1040**, 6 splits).

## What runs where
- **Poll, not webhook (V1).** A GCP Cloud Run Job polls Pipe17 on a Scheduler cadence
  — same pattern as the LTL worker. Webhooks can replace it later.
- **Incremental** by `updatedSince` window (`SYNC_LOOKBACK_MINUTES`).
- **Idempotent upsert**, merging on the Shipments primary field **Shipment Number**
  (= Pipe17 `extShipmentId`). Re-runs update in place; window overlap is safe.
- **Parent linkage:** each shipping request's `extOrderId` is looked up in **Orders**
  by *Order Number*; the resulting recordId is set on the shipment's **Order Link**.

## Field map (Pipe17 shipping request -> Airtable Shipments)
| Airtable field (Shipments) | Pipe17 source | Notes |
|---|---|---|
| Shipment Number *(primary, merge key)* | `extShipmentId` | **raw dot form kept** (`#TestCA1040.2`); Airtable "Parent Order (FX)" is delimiter-agnostic |
| Order Link | `extOrderId` -> Orders lookup | recordId resolved in main.py |
| Origin WH *(select)* | `locationId` via `LOCATION_MAP` | Mississauga confirmed; fill the rest |
| Status *(select)* | `status` via `STATUS_MAP` | Ops-flow field, not 1:1 — **confirm with Drew** |
| Customer Name | `shippingAddress.firstName`+`lastName` | |
| Delivery Address / City / Zip Code / State | `shippingAddress.*` | State/`ON` already an option |
| Customer Email | `shippingAddress.email` | |
| Line Items | `lineItems[]` | `"7 x 11-01-00-50 - Ergonomic Chair …"` (per-split qty) |
| Shipment Creation Date | `createdAt` | date only |

## Nomenclature (decision: keep raw)
Pipe17 numbers splits with a dot (`#TestCA1040.2`). We keep that raw so ids round-trip
back to Pipe17 cleanly (no `.N` -> `(N)` transformation). Instead the Airtable
**Parent Order (FX)** formula was made delimiter-agnostic (handles `(`, `.`, or bare),
so the **Child / Parent - Relational Database** automation keeps working unchanged.
`NORMALIZE_SHIPMENT_NUMBER` defaults **false** and stays off.

Delimiter-agnostic Parent Order (FX):
```
IF({Shipment Number}="","",LEFT({Shipment Number},MIN(IF(FIND("(",{Shipment Number})=0,LEN({Shipment Number})+1,FIND("(",{Shipment Number})),IF(FIND(".",{Shipment Number})=0,LEN({Shipment Number})+1,FIND(".",{Shipment Number})))-1))
```

## Test offline (no GCP, no creds)
```bash
PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest.py
```
Runs the real transform over `fixtures/testca1040.json` (6 splits) and asserts the raw
form is preserved, all splits parse to `#TestCA1040`, and unmapped WH / unknown status
are left blank (no junk select options). `DRY_RUN` defaults **true**: a real run logs the
mapped payload and writes nothing until you set `DRY_RUN=false`.

## Confirm before prod
- [ ] **Status mapping** (`STATUS_MAP`) — the Ops-flow labels are best-guess; Drew/Ops own this.
- [ ] **Order ownership** (`SYNC_ORDERS`, default off) — does Pipe17 own the parent Order row,
      or is it created upstream (HubSpot/Shopify) and we only link? Confirm vs skubana. If Pipe17
      should own it, map the order payload and enable.
- [ ] **`LOCATION_MAP`** — add every `locationId` -> Origin WH option (run Pipe17 list-locations).
- [x] **Pipe17 transport** — CONFIRMED: base `https://api-v3.pipe17.com/api/v3`, header
      `X-Pipe17-Key`, shipping requests at `/shipments`. Last unknown: the incremental
      param name (`PIPE17_SINCE_PARAM`, default `updatedSince`) and pagination shape.
      Confirm both with the curl in NEXT_STEPS, then override env if needed.
- [ ] **Idempotency key** — we merge on the human Shipment Number, mirroring skubana. If Pipe17 ever
      reissues `extShipmentId`, add a dedicated "Pipe17 Shipment ID" field (= `shipmentId`) and merge on that.

## Deploy (GCP Cloud Run Job)
```bash
gcloud run jobs create pipe17-airtable-sync \
  --source . --region us-central1 \
  --set-secrets=PIPE17_API_KEY=pipe17-api-key:latest,AIRTABLE_API_KEY=airtable-pat:latest \
  --set-env-vars=SYNC_LOOKBACK_MINUTES=60
# schedule (cadence <= SYNC_LOOKBACK_MINUTES)
gcloud scheduler jobs create http pipe17-airtable-sync-trigger \
  --schedule="*/30 * * * *" \
  --uri="https://<region>-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/<project>/jobs/pipe17-airtable-sync:run" \
  --oauth-service-account-email=<sa>@<project>.iam.gserviceaccount.com
```

## Files
`config.py` (ids, maps, merge keys) · `pipe17_client.py` · `airtable_client.py`
(upsert + order lookup) · `transform.py` · `main.py`
