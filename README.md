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
| Shipment Number *(primary, merge key)* | `extShipmentId` | **normalized** `.N` -> `(N)` to match the base's skubana convention |
| Order Link | `extOrderId` -> Orders lookup | recordId resolved in main.py |
| Origin WH *(select)* | `locationId` via `LOCATION_MAP` | Mississauga confirmed; fill the rest |
| Status *(select)* | `status` via `STATUS_MAP` | Ops-flow field, not 1:1 — **confirm with Drew** |
| Customer Name | `shippingAddress.firstName`+`lastName` | |
| Delivery Address / City / Zip Code / State | `shippingAddress.*` | State/`ON` already an option |
| Customer Email | `shippingAddress.email` | |
| Line Items | `lineItems[]` | `"7 x 11-01-00-50 - Ergonomic Chair …"` (per-split qty) |
| Shipment Creation Date | `createdAt` | date only |

## Nomenclature (important)
Pipe17 numbers splits with a dot (`#TestCA1040.2`); the Airtable base parses skubana's
parenthesis form (`#TestCA1040(2)`) in the **Parent Order (FX)** formula and the
**Child / Parent - Relational Database** automation. The sync therefore normalizes
`.N` -> `(N)` (`NORMALIZE_SHIPMENT_NUMBER=true`) so all existing formulas/automations keep
working. First split stays bare (`#TestCA1040`), which the existing formula handles.

## Confirm before prod
- [ ] **Status mapping** (`STATUS_MAP`) — the Ops-flow labels are best-guess; Drew/Ops own this.
- [ ] **Order ownership** (`SYNC_ORDERS`, default off) — does Pipe17 own the parent Order row,
      or is it created upstream (HubSpot/Shopify) and we only link? Confirm vs skubana. If Pipe17
      should own it, map the order payload and enable.
- [ ] **`LOCATION_MAP`** — add every `locationId` -> Origin WH option (run Pipe17 list-locations).
- [ ] **Pipe17 transport** — `PIPE17_AUTH_HEADER` and that shipping requests are at `/shipments`.
      (Field names are already confirmed from live data.)
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
