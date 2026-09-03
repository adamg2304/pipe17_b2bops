# Pipe17 -> Airtable sync (Shipping Requests -> Shipments)

Replaces the skubana -> Airtable feed. Pulls Pipe17 **shipping requests** and lands
them in the Airtable **Shipments** table (base *US - Branch Project Tracker (New)*),
linked to their parent **Order** — mirroring the skubana shipment shape the existing
Ops workflows depend on.

Wired to live schemas (verified 2026-08-18 against order **#TestCA1040**, 6 splits).

## What runs where
- **Poll, not webhook (V1).** A GCP Cloud Run **Job** polls Pipe17 on a Scheduler
  cadence — same pattern as the LTL worker. Webhooks can replace it later.
- **Incremental** by `updatedSince` window (`SYNC_LOOKBACK_MINUTES`).
- **Idempotent upsert**, merging on the Shipments primary field **Shipment Number**
  (= Pipe17 `extShipmentId`). Re-runs update in place; window overlap is safe.
- **Parent linkage:** each shipping request's `extOrderId` is looked up in **Orders**
  by *Order Number*; the resulting recordId is set on the shipment's **Order Link**.

## Field map (Pipe17 shipping request -> Airtable Shipments)
| Airtable field (Shipments) | Pipe17 source | Notes |
|---|---|---|
| Shipment Number *(primary, merge key)* | `extShipmentId` | **RAW dot form preserved** (e.g. `#TestCA1040.2`) so it round-trips to Pipe17 |
| Order Link | `extOrderId` -> Orders lookup | recordId resolved in main.py |
| Origin WH *(select)* | `locationId` via `LOCATION_MAP` | Mississauga confirmed; fill the rest |
| Status *(select)* | `status` via `STATUS_MAP` | Ops-flow field, not 1:1 — **confirm with Drew** |
| Customer Name | `shippingAddress.firstName`+`lastName` | |
| Delivery Address / City / Zip Code / State | `shippingAddress.*` | State/`ON` already an option |
| Customer Email | `shippingAddress.email` | |
| Line Items | `lineItems[]` | `"7 x 11-01-00-50 - Ergonomic Chair ..."` (per-split qty) |
| Shipment Creation Date | `createdAt` | date only |

## Nomenclature (important)
Pipe17 numbers splits with a dot (`#TestCA1040.2`); skubana used a parenthesis
(`#TestCA1040(2)`). Decision: **keep Pipe17's raw dot form in Airtable** (don't
rewrite `.` -> `(`) so the number round-trips to Pipe17 for reporting. Instead the
Airtable side is made **delimiter-agnostic**: the **Parent Order (FX)** formula on
Shipments parses either `(` or `.` (applied, backward-compatible). The
**Child / Parent - Relational Database** automation just consumes that formula, so
fixing the one formula fixes both.

## Confirm before prod
- [ ] **Status mapping** (`STATUS_MAP`) — the Ops-flow labels are best-guess; Drew/Ops own this.
- [ ] **Order ownership** (`SYNC_ORDERS`, default off) — Orders are created upstream
      (HubSpot -> Airtable Order Connector) and we only link. Flip on later when Pipe17
      owns order submission.
- [ ] **`LOCATION_MAP`** — add every `locationId` -> Origin WH option (run Pipe17 list-locations).
- [ ] **Idempotency key** — we merge on the human Shipment Number, mirroring skubana. If Pipe17 ever
      reissues `extShipmentId`, add a dedicated "Pipe17 Shipment ID" field (= `shipmentId`) and merge on that.

## Deploy (GCP Cloud Run JOB, from local source)
```bash
gcloud config set project 5442231412

# secrets (first time only)
printf '%s' 'YOUR_PIPE17_KEY'   | gcloud secrets create pipe17-api-key --data-file=-
printf '%s' 'YOUR_AIRTABLE_PAT' | gcloud secrets create airtable-pat  --data-file=-

# let the job runtime SA read them
for S in pipe17-api-key airtable-pat; do
  gcloud secrets add-iam-policy-binding $S \
    --member="serviceAccount:5442231412-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done

# deploy the JOB (first run stays DRY_RUN=true — no writes)
gcloud run jobs deploy pipe17-airtable-sync \
  --source . \
  --region us-central1 \
  --tasks 1 --max-retries 1 \
  --set-secrets=PIPE17_API_KEY=pipe17-api-key:latest,AIRTABLE_API_KEY=airtable-pat:latest \
  --set-env-vars=SYNC_LOOKBACK_MINUTES=60,DRY_RUN=true

# run once + watch logs
gcloud run jobs execute pipe17-airtable-sync --region us-central1

# once validated, drop DRY_RUN and schedule (cadence <= SYNC_LOOKBACK_MINUTES)
gcloud scheduler jobs create http pipe17-airtable-sync-trigger \
  --schedule="*/30 * * * *" \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/5442231412/jobs/pipe17-airtable-sync:run" \
  --http-method=POST \
  --oauth-service-account-email=5442231412-compute@developer.gserviceaccount.com
```

## Files
`config.py` (ids, maps, merge keys) · `pipe17_client.py` · `airtable_client.py`
(upsert + order lookup) · `transform.py` · `main.py` · `Dockerfile`
