# Sync Repair Service — setup

Ops-triggered healing of orders / shipping requests that the scheduled sync missed or
got wrong. An Airtable **form** writes a request row → an Airtable **automation** POSTs it
to a Cloud Run **service** the instant it's submitted → the service **diagnoses why** it
was missed, **heals** it through the normal sync mapping, and writes the result back to
the row, an Airtable **comment**, and **Slack**.

Code: `repair.py` (engine), `repair_service.py` (webhook), `main_repair.py` (CLI).
With nothing wired, the service still deploys and no-ops safely.

## 1. Create the "Sync Repair Requests" table (Airtable UI)

New table in base `appYsxq2ZGOz2z5ND`. Fields:

| Field | Type | Filled by | Notes |
|---|---|---|---|
| Pipe17 ID | Single line text | ops | order (`#BE123`) or SR (`#BE123.2`) number |
| Type | Single select | ops | options: `Order`, `Shipping Request`, `Auto` (default Auto) |
| Force | Checkbox | ops | heal even if the record isn't normally synced |
| Details | Long text | ops | what looks wrong |
| Reported By | Single line text (or Created by) | ops | optional |
| Status | Single select | service | options: `New`, `Processing`, `Healed`, `Could Not Heal`, `Needs Review` (default `New`) |
| Diagnosis | Long text | service | why it was missed |
| Outcome | Long text | service | what the service did |
| Processed At | Date (incl. time) | service | |

> I can create the **form** and the **automation** for you via MCP once this table exists — just say go and share the table ID.

## 2. Form

Create a Form view (or interface form) exposing **Pipe17 ID, Type, Force, Details, Reported By**. Share the link with ops. That's their entry point.

## 3. Secret + service deploy

1. Create the shared webhook secret in Secret Manager (any strong random string):
   ```bash
   printf '%s' "$(openssl rand -hex 24)" | gcloud secrets create repair-webhook-secret --data-file=- --project=pipe17-b2bops
   # grant the deploy + runtime SAs access (same pattern as the other secrets)
   ```
2. Deploy the service: GitHub → Actions → **Deploy Cloud Run Jobs** → Run workflow → `target: repair`. It deploys `pipe17-repair` (a Cloud Run **service**, `--allow-unauthenticated`; requests are gated by the secret header).
3. Point it at the table + fields (field IDs from the table's API docs):
   ```bash
   gcloud run services update pipe17-repair --region=europe-west1 --project=pipe17-b2bops \
     --set-env-vars=PIPE17_TAG_FILTER=Airtable,\
   REPAIR_REQUESTS_TABLE=tbl...,\
   RF_PIPE17_ID=fld...,RF_TYPE=fld...,RF_FORCE=fld...,RF_DETAILS=fld...,\
   RF_STATUS=fld...,RF_DIAGNOSIS=fld...,RF_OUTCOME=fld...,RF_PROCESSED_AT=fld...
   ```
   (If your Status/Type option strings differ from the defaults, also set `REPAIR_STATUS_*` / `REPAIR_TYPE_*`.)
4. Note the service URL from the deploy output (`https://pipe17-repair-....run.app`).

## 4. Automation (instant trigger)

Airtable automation on the requests table: **When a record is created → Run a script**:

```js
let cfg = input.config();
let rec = await input.recordAsync ? null : null; // use the trigger record
let r = input.config();
// Pull the trigger record's cell values:
let table = base.getTable("Sync Repair Requests");
let recordId = input.config().recordId;           // map this input to the trigger record id
let record = await table.selectRecordAsync(recordId);
await fetch("https://pipe17-repair-XXXX.run.app/repair", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Repair-Secret": "THE_SECRET" },
  body: JSON.stringify({
    record_id: recordId,
    pipe17_id: record.getCellValueAsString("Pipe17 ID"),
    type: record.getCellValueAsString("Type") || "Auto",
    force: !!record.getCellValue("Force"),
  }),
});
```

Add the input variable `recordId` = the trigger record's Record ID, paste the service URL and the secret, test, and turn the automation on.

## 5. Try it

- CLI (no webhook): `python main_repair.py --id '#BE123'` (reads/writes live — run where the creds are).
- End to end: submit the form with a known-missing order number; within a second or two the row should flip to `Healed` / `Needs Review`, gain a Diagnosis + Outcome + a comment, and a Slack message should post.

## How it decides

- **Order not in Pipe17** → `Needs Review` (wrong number/channel).
- **Not tagged `Airtable`** or **status not `readyForFulfillment`/`sentToFulfillment`** → reports the exact reason, does **not** force it in (resubmit with **Force** to override).
- **Eligible but missing / damaged** → heals via the real `order_to_airtable` / `shipment_to_airtable` + upsert, so the row matches a normal sync.
- **SR** (`#BE123.2`) heals that one request; a bare order number with Type `Shipping Request` heals all of the order's SRs.

The requests table is also your **triage log** — the Diagnosis column shows the recurring root causes to fix in the sync over time.
