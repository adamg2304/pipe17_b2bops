# Inventory Hold (V2) — Track B

Lock inventory for a deal **before it's paid**, on the **same** Pipe17 order the
Ready-for-Ops flow later releases (one order per deal, `extOrderId = #BE/#CEN<dealId>`).
Entirely behind `INVENTORY_HOLD_ENABLED` (default `false`). With the flag off, Track B
behaves exactly as before.

## Why it's built this way

To lock inventory, a Pipe17 order must reach **`readyForFulfillment`** — that's what
splits shipping requests and commits inventory. A `draft` order does not hold anything.
So every order (hold or confirmed) is created at `readyForFulfillment`, and an **Ops
tag-keyed Pipe17 automation parks the shipping requests in Review Required** so nothing
ships until released. Our code only sets the status, the tags, a hold note + reportable
custom field, and a Sales-Ops Slack alert when a hold flips to Ready for Ops.

## States

| Deal condition | Pipe17 order | Tags | Note |
|---|---|---|---|
| **Hold** — hold stage + `inventory_hold_approved` checkbox | `readyForFulfillment` | `B2BInventoryHold` + routing/B2B, **no** `Airtable` | `DO NOT RELEASE` added |
| **Confirmed** — stage = Ready for Ops (`10492961`) | `readyForFulfillment` | `Airtable` + `B2BConfirmReview` + routing/B2B | none |
| **Held → Ready for Ops** | unchanged | remove `B2BInventoryHold`, add `Airtable` + `B2BConfirmReview`; re-sync lines | **cleared** |
| **Withdrawn** — closed-lost, or checkbox cleared in a hold stage | cancelled | — | — |

- Holds are **not** tagged `Airtable`, so Track A / Ops never see or action them. Only
  at Ready for Ops does the order get `Airtable` + `B2BConfirmReview`, surfacing it for
  Ops to review against HubSpot and release the SRs.
- On the **Held → Ready for Ops** flip, the `DO NOT RELEASE` note is **cleared** (a
  released order never carries it), and a **Slack alert goes to Sales Ops** ("Held order
  XYZ is now Ready for Ops — review and release its shipping requests").
- The hold is also surfaced as a **reportable custom field** on the SO
  (`inventory_hold` = `true` while held, `false` once released), in addition to the tag.
- **Cancel only touches a still-held order** (one still carrying `B2BInventoryHold`). A
  released order is never cancelled by a later loss — its 3PL shipment isn't recalled by
  a status flip.
- Updates are **signature-gated** (sorted sku+qty+price, tag diff, status) and **merge
  tags** rather than rebuilding, so freight tags like `LTL` and anything Ops added survive.
  The PUT sends a minimal body (status + tags, lineItems only when changed), so Ops-curated
  shipping address / customer / notes survive.

## Go-live sequence

The code is merged **dark** (`INVENTORY_HOLD_ENABLED=false`, pinned in the deploy
workflow). Going live:

1. **Ops builds two Pipe17 order automations** (tag-keyed), each parking that order's
   shipping requests in Review Required:
   - one on `B2BInventoryHold`
   - one on `B2BConfirmReview`

   Tag names are config (`PIPE17_HOLD_TAG`, `PIPE17_CONFIRM_REVIEW_TAG`), so they can
   match whatever the automations key on. **These must exist before the flag flips** —
   without them, holds would create orders at `readyForFulfillment` with nothing parking
   the SRs, and they'd be fulfilled.
2. **Dry-run** against live data (writes nothing): `python hold_sync.py --deal-id <id>`
   then a full `python hold_sync.py`. Confirm the per-deal decisions.
3. **First live write is a single deal:** `DRY_RUN=false python hold_sync.py --deal-id <id>`.
   Confirm the Pipe17 order carries the note (`PIPE17_HOLD_NOTE_FIELD`, default
   `orderNote` — verified against Pipe17's order schema). If it ever rejects it, set
   `PIPE17_HOLD_NOTE=""` (the tag and the `inventory_hold` custom field still mark it).
4. **Flip the flag:** set `INVENTORY_HOLD_ENABLED=true` in the Track B deploy step and
   push (durable across redeploys), or set it on `hubspot-pipe17-orders` directly.

## Release note for the team (holds that change later)

An inventory hold is set for the **original** order quantity. If, after the hold is
placed, someone **increases the quantity** or **changes the SKU** (especially to an
out-of-stock item), the hold is **not guaranteed** to still cover it. Reducing quantity
or editing before the SR is sent to the warehouse is fine (the SR stays editable).
Do **not** cancel-and-recreate a held order to change it — cancelling releases the
committed inventory. Route changes to held orders through the upstream sales process.

## Known risk (inherent, not a bug)

Committing inventory for an unpaid hold deducts from available, so holds sit ahead of
paid orders in the queue and can move ecom ship-by dates. This mirrors the old Skubana
behavior and is inherent to locking inventory.
