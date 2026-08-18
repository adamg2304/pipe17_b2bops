# Pipe17 Integration — EOD Next Steps

Two tracks. Both are now correct and **proven offline** (no GCP, no live writes). The
only thing gated on GCP billing is the actual deploy.

- **Track A — Pipe17 → Airtable shipment feed** (`/pipe17`): fixes applied, selftest green.
- **Track B — HubSpot → Pipe17 sales orders** (`/hubspot_pipe17`): payload builder scaffolded, selftest green.

---

## Track A — what changed in your uploaded code

Three bugs would have failed on first run, plus one reversed decision:

1. **Base URL** was `https://api.pipe17.com/v1` → fixed to **`https://api-v3.pipe17.com/api/v3`**.
2. **Auth header** was `X-Api-Key` → fixed to **`X-Pipe17-Key`** (confirmed from Pipe17 docs).
3. **Normalization** defaulted **on** (`.N`→`(N)`), contradicting the keep-raw decision → default **off**; Shipment Number now writes raw dot form and the Airtable formula parses both delimiters.
4. Added **`DRY_RUN`** (default **true**, writes nothing until you flip it) and **`PIPE17_SINCE_PARAM`** so the last transport unknown is a config flip, not a code change.

Proven offline:
```bash
cd pipe17 && PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest.py
# PASS: raw form preserved, all 6 splits -> #TestCA1040, unmapped WH/status left blank
```

### The 2 curls that close the last unknowns (need your key, ~5 min, read-only)
```bash
# 1) confirm transport + the since-param name + pagination shape
curl -s -H "Accept: application/json" -H "X-Pipe17-Key: YOUR_KEY" \
  "https://api-v3.pipe17.com/api/v3/shipments?updatedSince=2026-08-01T00:00:00Z&count=1" | head -c 3000
# if that 400s on updatedSince, retry with ?since=2026-08-01 . Set PIPE17_SINCE_PARAM to match.
# 403 -> your key lacks the shipments scope; recreate with methods.shipments="rl", locations="rl".

# 2) grab location IDs to finish LOCATION_MAP (only Mississauga is filled today)
curl -s -H "Accept: application/json" -H "X-Pipe17-Key: YOUR_KEY" \
  "https://api-v3.pipe17.com/api/v3/locations" | head -c 4000
```
Then fill `LOCATION_MAP` in `pipe17/config.py` with every `locationId` -> exact Origin WH option name.

### Still owed by others (Track A)
- **Status map** (`STATUS_MAP`): Ops-flow labels are best-guess. Confirm with Drew.
- **`SYNC_ORDERS`** (default off): does Pipe17 own the parent Order row, or is it created upstream and we only link? Confirm vs skubana.
- One risk worth knowing: `upsert(typecast=True)` will **create** a new select option if a mapped value does not exactly match an existing one. Transform already leaves unmapped WH/status blank, so this only bites on a typo in `LOCATION_MAP`/`STATUS_MAP`. Keep those values exact.

### Do NOT write to live Airtable to test
Even one record trips your shipment-creation automations during the skubana parallel run.
The parse is already proven by the selftest. If you must validate the Child/Parent
automation, do it on a **duplicated base** and discard.

---

## Track B — HubSpot → Pipe17 sales orders (starting point)

The core is the **draft-order payload builder** (`hubspot_pipe17/order_payload.py`), built
and tested offline:
```bash
cd hubspot_pipe17 && python3 selftest_order.py
# PASS: create-vs-update routing, USD/CAD orderbot country, blank-SKU skip+flag,
#       Enterprise/White Glove tags, subtotal math
```
It already handles: `POST /orders` (create) vs `PUT /orders/{id}` (keep-in-sync) via the
deal's `pipe17_order_id`; USD→US / CAD→CA routing country for orderbots; `deal-{id}` +
Enterprise/White Glove tags; blank-SKU skip with a flagged list; subtotal math.

### The one thing to resolve before Track B goes past scaffolding
**Does a Pipe17 `draft` order reserve inventory (FIFO, cancel-to-free)?** The whole
allocation-at-acceptance model depends on it, and the status reference suggests only
`new` consumes. Confirm with Pipe17/Lancome. If draft does not hold, the create-status
in `order_payload.py` changes (`draft` → `new` or a separate hold). It is a one-line
change here, but a big design fork. Do not build the webhook layer until this is answered.

### Build sequence for Track B (after the draft question is answered)
1. **HubSpot workflow webhook** on deal entering the quote-accepted stage → your endpoint. (Trigger stage id TBD; current Shopify sync used "Ready for Ops" `10492961`, Pipe17 moves earlier.)
2. **Webhook receiver** (Cloud Run service): verify `X-HubSpot-Signature-v3`, read deal + associated line items, call `build_draft_order`, POST/PUT to Pipe17, poll the async job, write `pipe17_order_id` back to the deal.
3. **Keep-in-sync webhook** on line-item changes → rebuild + `PUT`.
4. Idempotency via `extOrderId = hubspot-deal-{id}` + the `deal-{id}` tag (mirrors the Shopify sync).
5. Reuse the Track A transport client (same base + `X-Pipe17-Key`); orders need `orders: "crudl"` scope on the key.

Full detail is in the build spec (`pipe17-hubspot-integration-spec-v2.md`).

---

## Blocked on GCP billing (only this)
Deploy of both Cloud Run Jobs/services waits on a billing account linked to the GCP
project. That is an account action for whoever holds Branch's GCP org billing. Everything
above (fixes, curls, LOCATION_MAP, DRY_RUN validation, Track B payload logic) proceeds
without it. When billing lands: source-deploy → run in `DRY_RUN` → eyeball → flip live.
```
