# Tax Calculation — Design Spec (Branch B2B, HubSpot / Shopify / Pipe17 / QBO)

Status: DESIGN, not built. Owner: Adam. Captured 2026-09-20.

The current code (`transform_order.py`) only forwards a frozen tax value if a deal property (`HS_TAX_TOTAL_PROP`) is set, and otherwise omits tax. This spec replaces that with live, per-line, post-discount tax calculation. It is its own project, sequenced after Tracks A/B are live.

## 1\. Goal

Tax on a B2B deal is calculated live from Shopify's tax engine, kept current as the deal's line items change, stored as a HubSpot line item, and routed correctly onto the quote and the QBO estimate.

## 2\. Model

- **Tax is a HubSpot line item** on the deal, not a deal-level field. Created either by the rep manually, or automatically on a trigger (deal creation / stage / webhook).  
- **Recalculation is event-driven (webhook), not polled.** Whenever a line item on the deal changes, recompute tax and write it back to the tax line item. Polling would be laggy and wasteful; this is the one place a webhook is the right tool.  
- **Amounts are post-discount.** HubSpot already exposes the post-discount line amount, so the calc uses that directly (no discount math needed on our side).  
- **Per product/service, dynamic.** Different products and services are taxed differently, so the Shopify call must pass enough per-line detail (SKU / tax category) for Shopify to apply the correct rule per line. Requires a SKU \-\> tax-category mapping (data work; must exist before the code can be truly dynamic).

## 3\. Flow

```
line item added / changed on deal
        │  (HubSpot webhook: line item or deal property change)
        ▼
recalc worker (Cloud Run Service)
   1. load deal + all NON-TAX line items
   2. build per-line request (post-discount amount + tax category by SKU)
   3. call Shopify tax API -> tax amount (per line / total)
   4. upsert the single "tax" line item on the deal with the new amount
        │
        ▼
at quote time: tax routes to the quote's tax slot + QBO estimate
   (Adam handles this routing in Zapier for now)
```

## 4\. Critical guardrails

- **Feedback-loop protection (must-have).** The recalc WRITES a tax line item, which is itself a line-item change, which would re-fire the recalc \-\> infinite loop. Prevent by: excluding the tax line item from the trigger (ignore changes to the tax SKU), and/or debouncing (coalesce rapid successive changes), and/or writing only when the tax amount actually changed (no-op writes suppressed).  
- **Idempotency / one tax line.** Always upsert THE tax line item (find-or-create by a stable tax SKU), never append a new one per recalc.  
- **Ordering / races.** Rapid edits can fire overlapping recalcs; debounce or use the latest deal state at compute time so the last write wins with correct data.

## 5\. Interaction with Track B (order creation)

Track B (`transform_order.py`) computes `subTotalPrice` by summing line items. Once a tax line item exists on the deal, Track B MUST treat it specially:

- **Exclude** the tax line from `subTotalPrice` (do not sum it as a product line).  
- **Map** the tax line's amount to `orderTax`, and set `totalPrice = subTotalPrice + orderTax`.  
- Identify the tax line by its stable tax SKU (same SKU the recalc worker upserts).

This is a small, well-defined change to `transform_order.py` when the tax line goes live — add the tax SKU to a set (like `SERVICE_SKUS`) that is pulled out of the product lines and routed to `orderTax`.

## 6\. Routing at quote / estimate

- On the HubSpot quote: tax shows in the quote's tax slot, not as a product line, even though it is structurally a line item in HubSpot.  
- On the QBO estimate: tax maps to the estimate's tax field.  
- Both handled in Zapier for now (Adam). Revisit if/when it moves into code.

## 7\. Dependencies / open items

- [ ] Shopify tax API access \+ scopes (pending from Alex).  
- [ ] SKU \-\> tax-category mapping (which SKUs/services are taxable, at what rule).  
- [ ] Stable "tax" SKU/identifier for the HubSpot tax line item.  
- [ ] HubSpot webhook (line-item / deal property change) \-\> recalc Service endpoint.  
- [ ] Decide manual-vs-auto creation of the initial tax line item.  
- [ ] `transform_order.py` update: exclude tax SKU from subtotal, route to `orderTax`.

## 8\. Build sequence (when picked up)

1. Land the SKU \-\> tax-category mapping (data).  
2. Stand up the recalc worker as a webhook Service, with loop protection, writing the tax line item.  
3. Wire the HubSpot webhook to it.  
4. Update `transform_order.py` to route the tax line to `orderTax`.  
5. Confirm quote \+ QBO routing (Zapier).

