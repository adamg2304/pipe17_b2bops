"""Offline check for the Track B transform (HubSpot deal -> Pipe17 order payload).

No network, no creds. Runs build_order over a synthetic USD deal and asserts the pieces
that matter: currency-prefixed extOrderId, draft status, Airtable tag, service-SKU
flagging, NET unit pricing (amount/qty), line-item-derived subtotal, the customer object
built from the delivery contact, and CAD prefix routing.

    PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_trackb.py
"""
import transform_order
from transform_order import build_order
from config import HS_TAX_TOTAL_PROP

# Keep this test offline: stub the SKU resolver so build_order never fetches the Pipe17
# catalog. The fake passes every SKU through unchanged (status "exact").
transform_order.get_resolver = lambda currency: type(
    "R", (), {"resolve": staticmethod(lambda s: (s, "exact")),
              "collisions": {}, "currency": (currency or "USD").upper()})()

DEAL_USD = {
    "id": "64737059217",
    "properties": {"deal_currency_code": "USD", "dealname": "Acme HQ Refresh",
                   "delivery_city": "Montgomery", "delivery_state_province": "TX",
                   "delivery_zip_postal_code": "77316", "delivery_address_1": "895 Fish Creek"},
}
LINE_ITEMS = [
    {"sku": "11-03-00-50", "name": "Daily Chair", "quantity": 25, "price": 250, "amount": 5827.5},
    {"sku": "White Glove Delivery", "name": "WGD", "quantity": 1, "price": 1171.25, "amount": 1171.25},
]
CONTACT = {"firstName": "Hannah", "lastName": "Weinberg", "email": "h@x.com", "phone": "555-0100"}

passed = failed = 0
def check(label, cond):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {label}")
    else: failed += 1; print(f"  FAIL  {label}")

print("1) USD deal -> Pipe17 order body")
body, currency, ext = build_order(DEAL_USD, LINE_ITEMS, CONTACT)
check("currency USD", currency == "USD")
check("extOrderId = #BE<dealId>", ext == "#BE64737059217")
check("status draft", body["status"] == "draft")
check("tags: Airtable + White Glove + B2B", body["tags"] == ["Airtable", "White Glove", "B2B"])
check("hubspot_deal_id custom field",
      body["customFields"] == [{"name": "hubspot_deal_id", "value": "64737059217"}])

chair = next(li for li in body["lineItems"] if li["sku"] == "11-03-00-50")
wgd = next(li for li in body["lineItems"] if li["sku"] == "White Glove Delivery")
check("net unit price = amount/qty (5827.5/25 = 233.1)", chair["itemPrice"] == 233.1)
check("chair requiresShipping True", chair["requiresShipping"] is True)
check("service SKU requiresShipping False", wgd["requiresShipping"] is False)
check("subtotal from line items (233.1*25 + 1171.25 = 6998.75)", body["subTotalPrice"] == 6998.75)
check("total = subtotal (no tax)", body["totalPrice"] == 6998.75)
check("no orderTax when tax absent", "orderTax" not in body)

# Tax from the deal's b2b_deal_tax -> orderTax, added to totalPrice.
taxed_deal = {"id": "64737059217",
              "properties": dict(DEAL_USD["properties"], **{HS_TAX_TOTAL_PROP: "550.25"})}
tbody, _, _ = build_order(taxed_deal, LINE_ITEMS, CONTACT)
check("orderTax = deal tax (550.25)", tbody.get("orderTax") == 550.25)
check("total = subtotal + tax (6998.75 + 550.25 = 7549.0)", tbody["totalPrice"] == 7549.0)
check("subtotal unaffected by tax (6998.75)", tbody["subTotalPrice"] == 6998.75)

check("customer object built from delivery contact",
      body.get("customer", {}).get("firstName") == "Hannah"
      and body["customer"].get("email") == "h@x.com"
      and body["customer"].get("company") == "Acme HQ Refresh")
check("shippingAddress company = dealname", body["shippingAddress"]["company"] == "Acme HQ Refresh")
check("shippingAddress country inferred US", body["shippingAddress"]["country"] == "US")
check("shippingAddress contact email", body["shippingAddress"]["email"] == "h@x.com")

print("1b) routing tags by shipping line (each named line -> same-name tag; B2B on all)")
CHAIR = {"sku": "11-03-00-50", "name": "Daily Chair", "quantity": 1, "price": 250, "amount": 250}
free_body, _, _ = build_order(
    DEAL_USD, [CHAIR, {"sku": "Free Shipping", "name": "Free Shipping", "quantity": 1, "price": 0, "amount": 0}], None)
check("free shipping -> Airtable + Free Shipping + B2B",
      free_body["tags"] == ["Airtable", "Free Shipping", "B2B"])
exp_body, _, _ = build_order(
    DEAL_USD, [CHAIR, {"sku": "Expedited Shipping", "name": "Expedited Shipping", "quantity": 1, "price": 0, "amount": 0}], None)
check("expedited -> Airtable + Expedited Shipping + B2B",
      exp_body["tags"] == ["Airtable", "Expedited Shipping", "B2B"])
default_body, _, _ = build_order(DEAL_USD, [CHAIR], None)
check("no shipping line -> Airtable + B2B", default_body["tags"] == ["Airtable", "B2B"])

print("2) CAD deal -> #CEN prefix + CA routing")
deal_ca = {"id": "63327307681", "properties": {"deal_currency_code": "CAD", "dealname": "Maple Co"}}
_, cur_ca, ext_ca = build_order(deal_ca, LINE_ITEMS, None)
check("currency CAD", cur_ca == "CAD")
check("extOrderId = #CEN<dealId>", ext_ca == "#CEN63327307681")

# ---------------------------------------------------------------------------
print("3) inventory hold V2 (offline; Pipe17/HubSpot stubbed)")
import hold_sync
from config import (PIPE17_HOLD_TAG, PIPE17_CONFIRM_REVIEW_TAG, PIPE17_AIRTABLE_TAG,
                    PIPE17_RFF_STATUS, HS_HOLD_STAGE_IDS, HS_READY_FOR_OPS_STAGE_ID,
                    HS_LOST_STAGE_ID, PIPE17_HOLD_NOTE, PIPE17_HOLD_NOTE_FIELD,
                    PIPE17_HOLD_CF_NAME, PIPE17_HOLD_CF_HELD, PIPE17_HOLD_CF_RELEASED)

# Capture the Ready-for-Ops Slack alert without posting.
_slack = []
hold_sync.notify = type("N", (), {"slack": staticmethod(lambda *a, **k: _slack.append(a[0] if a else ""))})()

def _cf(body, name):
    return next((c["value"] for c in (body.get("customFields") or []) if c["name"] == name), None)

HOLD_STAGE = HS_HOLD_STAGE_IDS[0]            # e.g. 1523817 Quote Sent
HOLD_LINES = [{"sku": "11-03-00-50", "name": "Chair", "quantity": 2, "price": 250, "amount": 500},
              {"sku": "White Glove Delivery", "name": "WGD", "quantity": 1, "price": 100, "amount": 100}]

def deal_with(stage, approved, did="900001"):
    return {"id": did, "properties": {
        "deal_currency_code": "USD", "dealname": "Hold Co", "dealstage": stage,
        "inventory_hold_approved": "true" if approved else "false",
        "delivery_city": "Montgomery", "delivery_state_province": "TX",
        "delivery_zip_postal_code": "77316", "delivery_address_1": "895 Fish Creek"}}

# classify
check("hold stage + checkbox -> hold", hold_sync.classify(deal_with(HOLD_STAGE, True)) == "hold")
check("hold stage, no checkbox -> withdrawn", hold_sync.classify(deal_with(HOLD_STAGE, False)) == "withdrawn")
check("Ready for Ops -> confirmed", hold_sync.classify(deal_with(HS_READY_FOR_OPS_STAGE_ID, False)) == "confirmed")
check("closed lost supersedes checkbox -> closed_lost",
      hold_sync.classify(deal_with(HS_LOST_STAGE_ID, True)) == "closed_lost")
check("other stage -> noop", hold_sync.classify(deal_with("appointmentscheduled", True)) == "noop")

# desired tags
hold_tags = hold_sync.desired_tags("hold", ["White Glove", "B2B"])
conf_tags = hold_sync.desired_tags("confirmed", ["White Glove", "B2B"])
check("hold tags = routing + B2BInventoryHold, no Airtable",
      hold_tags == ["White Glove", "B2B", PIPE17_HOLD_TAG] and PIPE17_AIRTABLE_TAG not in hold_tags)
check("confirmed tags = Airtable + routing + B2BConfirmReview",
      conf_tags == [PIPE17_AIRTABLE_TAG, "White Glove", "B2B", PIPE17_CONFIRM_REVIEW_TAG])

# line signature
sigA = hold_sync.line_signature([{"sku": "A", "quantity": 2, "itemPrice": 10}])
sigA2 = hold_sync.line_signature([{"sku": "A", "quantity": 2, "itemPrice": 10}])
sigB = hold_sync.line_signature([{"sku": "A", "quantity": 3, "itemPrice": 10}])
check("signature equal when identical", sigA == sigA2)
check("signature differs on qty change", sigA != sigB)

class FakeHS:
    def __init__(self, lines): self.lines = lines; self.set_calls = []; self.deal_updates = []
    def get_line_items(self, did): return self.lines
    def delivery_contact_of(self, did): return None
    def set_pipe17_order_id(self, did, oid): self.set_calls.append((did, oid))
    def update_deal(self, did, props): self.deal_updates.append((did, props))

class FakeP17:
    def __init__(self, existing=None, shipments=None, deal_orders=None):
        self.existing = existing; self.created = []; self.updated = []; self.cancelled = []
        self._shipments = shipments if shipments is not None else []
        self._deal_orders = deal_orders   # None -> derive from `existing`
    def get_order_by_ext_id(self, ext, key): return self.existing
    def create_order(self, body, key): self.created.append(body); return {"orderId": "OID123"}
    def update_order(self, oid, body, key): self.updated.append((oid, body)); return {}
    def cancel_order(self, oid, key): self.cancelled.append(oid); return {}
    def order_id_of(self, order): return (order or {}).get("orderId") or (order or {}).get("id")
    def get_order_shipments(self, ext, key): return self._shipments
    def find_orders_for_deal(self, did, prefix, key):
        if self._deal_orders is not None: return self._deal_orders
        return [self.existing] if self.existing else []

KEYS = {"USD": "k", "CAD": "k"}
def reconcile(deal, existing=None, dry_run=False, shipments=None, deal_orders=None):
    hsf, p17 = FakeHS(HOLD_LINES), FakeP17(existing, shipments=shipments, deal_orders=deal_orders)
    res, ext = hold_sync.reconcile_deal(deal, hs=hsf, p17w=p17, key_map=KEYS,
                                        dry_run=dry_run, order_suffix="")
    return res, ext, hsf, p17

# CREATE a hold: no existing order
res, ext, hsf, p17 = reconcile(deal_with(HOLD_STAGE, True))
check("hold create -> created", res == "created")
check("hold create extOrderId #BE<id>", ext == "#BE900001")
check("hold order status = readyForFulfillment", p17.created[0]["status"] == PIPE17_RFF_STATUS)
check("hold order tags carry B2BInventoryHold, not Airtable",
      PIPE17_HOLD_TAG in p17.created[0]["tags"] and PIPE17_AIRTABLE_TAG not in p17.created[0]["tags"])
check("hold create carries DO-NOT-RELEASE note",
      not PIPE17_HOLD_NOTE or p17.created[0].get(PIPE17_HOLD_NOTE_FIELD) == PIPE17_HOLD_NOTE)
check("hold create carries reportable inventory_hold=true custom field",
      not PIPE17_HOLD_CF_NAME or _cf(p17.created[0], PIPE17_HOLD_CF_NAME) == PIPE17_HOLD_CF_HELD)
check("hold create keeps hubspot_deal_id custom field",
      _cf(p17.created[0], "hubspot_deal_id") == "900001")
check("hold create writes pipe17_order_id back", hsf.set_calls == [("900001", "OID123")])
res, *_ = reconcile(deal_with(HOLD_STAGE, True), dry_run=True)
check("hold create DRY_RUN -> would_create (no write)", res == "would_create")

# CONFIRMED create: no hold note, and inventory_hold=false
res, _, _, p17 = reconcile(deal_with(HS_READY_FOR_OPS_STAGE_ID, False))
check("confirmed create -> no hold note",
      PIPE17_HOLD_NOTE_FIELD not in p17.created[0] or not PIPE17_HOLD_NOTE)
check("confirmed create -> inventory_hold=false custom field",
      not PIPE17_HOLD_CF_NAME or _cf(p17.created[0], PIPE17_HOLD_CF_NAME) == PIPE17_HOLD_CF_RELEASED)

# UNCHANGED: existing order already matches the desired hold shape (already at RFF)
EXISTING_HOLD = {"orderId": "O1",
                 "status": PIPE17_RFF_STATUS,
                 "lineItems": [{"sku": "11-03-00-50", "quantity": 2, "itemPrice": 250},
                               {"sku": "White Glove Delivery", "quantity": 1, "itemPrice": 100}],
                 "tags": ["White Glove", "B2B", PIPE17_HOLD_TAG],
                 PIPE17_HOLD_NOTE_FIELD: PIPE17_HOLD_NOTE}
res, _, _, p17 = reconcile(deal_with(HOLD_STAGE, True), existing=EXISTING_HOLD)
check("hold unchanged when lines+tags+note match", res == "unchanged" and not p17.updated)

# UPDATE on tax change: deal gains b2b_deal_tax, existing order has none -> refresh pricing.
taxed_hold = deal_with(HOLD_STAGE, True)
taxed_hold["properties"][HS_TAX_TOTAL_PROP] = "500"
res, _, _, p17 = reconcile(taxed_hold, existing=EXISTING_HOLD)
check("hold tax change -> updated", res == "updated")
upd = p17.updated[0][1] if p17.updated else {}
check("tax update carries orderTax (500)", upd.get("orderTax") == 500)
check("tax update carries totalPrice = subtotal + tax (600 + 500 = 1100)", upd.get("totalPrice") == 1100)
check("tax update carries subTotalPrice (600)", upd.get("subTotalPrice") == 600)
# UNCHANGED when the order already carries the same tax.
res, _, _, p17 = reconcile(taxed_hold, existing=dict(EXISTING_HOLD, orderTax=500))
check("hold unchanged when tax already matches", res == "unchanged" and not p17.updated)

# UPDATE: deal line items changed (qty differs)
CHANGED = dict(EXISTING_HOLD, lineItems=[{"sku": "11-03-00-50", "quantity": 1, "itemPrice": 250}])
res, _, _, p17 = reconcile(deal_with(HOLD_STAGE, True), existing=CHANGED)
check("hold update on signature change -> updated", res == "updated" and len(p17.updated) == 1)

# CONFIRMED transition: held order moves to Ready for Ops -> tag swap, note cleared,
# inventory_hold->false, and a Sales-Ops Slack alert fires.
_slack.clear()
res, _, _, p17 = reconcile(deal_with(HS_READY_FOR_OPS_STAGE_ID, False), existing=EXISTING_HOLD)
check("held -> Ready for Ops = tag swap update", res == "updated")
_, upd_body = p17.updated[0]
check("confirmed swap: Airtable + B2BConfirmReview in, B2BInventoryHold out",
      PIPE17_AIRTABLE_TAG in upd_body["tags"] and PIPE17_CONFIRM_REVIEW_TAG in upd_body["tags"]
      and PIPE17_HOLD_TAG not in upd_body["tags"])
check("confirmed clears the DO-NOT-RELEASE note",
      not PIPE17_HOLD_NOTE or upd_body.get(PIPE17_HOLD_NOTE_FIELD) == "")
check("confirmed sets inventory_hold=false custom field",
      not PIPE17_HOLD_CF_NAME or _cf(upd_body, PIPE17_HOLD_CF_NAME) == PIPE17_HOLD_CF_RELEASED)
check("held -> Ready for Ops fires a Sales-Ops Slack alert",
      len(_slack) == 1 and "Ready for Ops" in _slack[0])

# A deal already at Ready for Ops (never held) does NOT alert
_slack.clear()
NEVER_HELD = {"orderId": "O9", "status": PIPE17_RFF_STATUS, "lineItems": EXISTING_HOLD["lineItems"],
              "tags": [PIPE17_AIRTABLE_TAG, "White Glove", "B2B", PIPE17_CONFIRM_REVIEW_TAG]}
res, _, _, p17 = reconcile(deal_with(HS_READY_FOR_OPS_STAGE_ID, False), existing=dict(NEVER_HELD, lineItems=[{"sku": "X", "quantity": 1, "itemPrice": 5}]))
check("confirmed non-hold update does not alert", not _slack)

# TAG PRESERVATION (regression): a non-managed freight tag (LTL) and any Ops-added tag
# must survive a confirmed update — only our managed tags get flipped.
EXISTING_WITH_LTL = {"orderId": "O3", "status": "draft",
                     "lineItems": EXISTING_HOLD["lineItems"],
                     "tags": [PIPE17_AIRTABLE_TAG, "Free Shipping", "B2B", "LTL"]}
res, _, _, p17 = reconcile(deal_with(HS_READY_FOR_OPS_STAGE_ID, False), existing=EXISTING_WITH_LTL)
_, upd_body = p17.updated[0]
check("confirmed update preserves LTL + routing, adds ConfirmReview",
      "LTL" in upd_body["tags"] and "Free Shipping" in upd_body["tags"]
      and PIPE17_CONFIRM_REVIEW_TAG in upd_body["tags"])
check("confirmed update flips draft -> readyForFulfillment",
      upd_body["status"] == PIPE17_RFF_STATUS)

# CANCEL: hold withdrawn (checkbox cleared) and order still held
res, _, _, p17 = reconcile(deal_with(HOLD_STAGE, False), existing=EXISTING_HOLD)
check("withdrawn + still held -> canceled", res == "canceled" and p17.cancelled == ["O1"])
res, *_ = reconcile(deal_with(HS_LOST_STAGE_ID, True), existing=EXISTING_HOLD)
check("closed lost -> canceled (checkbox ignored)", res == "canceled")

# NOT cancelled once released (order no longer carries the hold tag)
RELEASED = {"orderId": "O2", "lineItems": EXISTING_HOLD["lineItems"],
            "tags": [PIPE17_AIRTABLE_TAG, "B2B", PIPE17_CONFIRM_REVIEW_TAG]}
res, _, _, p17 = reconcile(deal_with(HS_LOST_STAGE_ID, False), existing=RELEASED)
check("lost after release -> left alone (not cancelled)", res == "noop_released" and not p17.cancelled)

# withdrawn with no order at all -> noop
res, *_ = reconcile(deal_with(HOLD_STAGE, False), existing=None)
check("withdrawn, no order -> noop", res == "noop")

# ---------------------------------------------------------------------------
print("3b) committed / quote-signed ingestion (awaiting_payment)")
from config import (HS_COMMITTED_STAGE_IDS, PIPE17_AWAITING_PAYMENT_TAG,
                    PIPE17_AWAITING_PAYMENT_NOTE)
COMMITTED = HS_COMMITTED_STAGE_IDS[0]            # 1492994 Committed Deal (90%)
COMMITTED2 = HS_COMMITTED_STAGE_IDS[-1]          # 1492995 Deposit Invoice Sent (95%)

# classify: committed ingests with AND without the checkbox; 95% too.
check("committed stage + checkbox -> awaiting_payment",
      hold_sync.classify(deal_with(COMMITTED, True)) == "awaiting_payment")
check("committed stage, NO checkbox -> awaiting_payment (auto)",
      hold_sync.classify(deal_with(COMMITTED, False)) == "awaiting_payment")
check("deposit-invoice-sent (95%) -> awaiting_payment",
      hold_sync.classify(deal_with(COMMITTED2, False)) == "awaiting_payment")

# CREATE: order shape = RFF, B2BInventoryHold + Awaiting Payment (no Airtable),
# payment-pending note, inventory_hold=true.
res, ext, hsf, p17 = reconcile(deal_with(COMMITTED, False))
check("committed create -> created", res == "created")
ap_body = p17.created[0]
check("awaiting_payment status = readyForFulfillment", ap_body["status"] == PIPE17_RFF_STATUS)
check("awaiting_payment tags: B2BInventoryHold + Awaiting Payment, no Airtable",
      PIPE17_HOLD_TAG in ap_body["tags"] and PIPE17_AWAITING_PAYMENT_TAG in ap_body["tags"]
      and PIPE17_AIRTABLE_TAG not in ap_body["tags"])
check("awaiting_payment carries payment-pending note",
      not PIPE17_AWAITING_PAYMENT_NOTE
      or ap_body.get(PIPE17_HOLD_NOTE_FIELD) == PIPE17_AWAITING_PAYMENT_NOTE)
check("awaiting_payment inventory_hold=true",
      not PIPE17_HOLD_CF_NAME or _cf(ap_body, PIPE17_HOLD_CF_NAME) == PIPE17_HOLD_CF_HELD)

# DEDUPE: an existing awaiting_payment order already matching -> unchanged, NO 2nd create.
EXISTING_AP = {"orderId": "AP1", "status": PIPE17_RFF_STATUS,
               "lineItems": EXISTING_HOLD["lineItems"],
               "tags": ["White Glove", "B2B", PIPE17_HOLD_TAG, PIPE17_AWAITING_PAYMENT_TAG],
               PIPE17_HOLD_NOTE_FIELD: PIPE17_AWAITING_PAYMENT_NOTE}
res, _, _, p17 = reconcile(deal_with(COMMITTED, False), existing=EXISTING_AP)
check("committed dedupe: matching order -> unchanged, no second create",
      res == "unchanged" and not p17.created and not p17.updated)

# An existing plain HOLD order converts to awaiting_payment in place at committed (no dup).
res, _, _, p17 = reconcile(deal_with(COMMITTED, True), existing=EXISTING_HOLD)
check("hold order -> committed converts in place (updated, no create)",
      res == "updated" and not p17.created and len(p17.updated) == 1)
_, conv = p17.updated[0]
check("converted order gains Awaiting Payment + keeps B2BInventoryHold",
      PIPE17_AWAITING_PAYMENT_TAG in conv["tags"] and PIPE17_HOLD_TAG in conv["tags"])
check("converted order note swapped to payment-pending",
      not PIPE17_AWAITING_PAYMENT_NOTE
      or conv.get(PIPE17_HOLD_NOTE_FIELD) == PIPE17_AWAITING_PAYMENT_NOTE)

# RELEASE: awaiting_payment -> Ready for Ops clears note, drops HOLD + Awaiting Payment,
# adds Airtable + ConfirmReview.
res, _, _, p17 = reconcile(deal_with(HS_READY_FOR_OPS_STAGE_ID, False), existing=EXISTING_AP)
check("awaiting_payment -> RFO = updated", res == "updated")
_, rel = p17.updated[0]
check("RFO release: Airtable + ConfirmReview in; HOLD + Awaiting Payment out",
      PIPE17_AIRTABLE_TAG in rel["tags"] and PIPE17_CONFIRM_REVIEW_TAG in rel["tags"]
      and PIPE17_HOLD_TAG not in rel["tags"] and PIPE17_AWAITING_PAYMENT_TAG not in rel["tags"])
check("RFO release clears the payment-pending note",
      not PIPE17_AWAITING_PAYMENT_NOTE or rel.get(PIPE17_HOLD_NOTE_FIELD) == "")

# CANADA mapping: a CAD deal at the committed stage -> #CEN order.
cad_committed = {"id": "63327307681", "properties": {
    "deal_currency_code": "CAD", "dealname": "Maple Co", "dealstage": COMMITTED}}
res, ext_ca2, _, _ = reconcile(cad_committed)
check("CAD committed -> #CEN extOrderId", res == "created" and ext_ca2 == "#CEN63327307681")

# ---------------------------------------------------------------------------
print("3c) closed-lost cancellation sweep (CLOSED_LOST_CANCEL_ENABLED)")
hold_sync.CLOSED_LOST_CANCEL_ENABLED = True
hold_sync.HS_CANCEL_RESULT_PROP = "pipe17_order_status"
try:
    lost = deal_with(HS_LOST_STAGE_ID, False)
    # Nothing in flight -> cancel + write the result back to the deal.
    res, _, hsf, p17 = reconcile(lost, existing=EXISTING_HOLD, shipments=[{"status": "reviewRequired"}])
    check("closed lost, nothing shipped -> canceled", res == "canceled" and p17.cancelled == ["O1"])
    check("closed lost cancel writes result back to the deal",
          bool(hsf.deal_updates) and hsf.deal_updates[0][1].get("pipe17_order_status"))

    # A shipment in flight -> do NOT cancel; alert Ops; still write back.
    _slack.clear()
    res, _, hsf, p17 = reconcile(lost, existing=EXISTING_HOLD,
                                 shipments=[{"status": "sentToFulfillment", "shipmentId": "S1"}])
    check("closed lost + in-flight SR -> NOT canceled", res == "blocked_inflight" and not p17.cancelled)
    check("in-flight closed lost raises a Slack alert",
          len(_slack) == 1 and "IN FLIGHT" in _slack[0])
    check("in-flight closed lost writes result back to the deal", bool(hsf.deal_updates))

    # Idempotent: an already-canceled order is not canceled again.
    CANCELED = dict(EXISTING_HOLD, orderId="OC", status=hold_sync.PIPE17_CANCELED_STATUS)
    res, _, _, p17 = reconcile(lost, existing=CANCELED, deal_orders=[CANCELED], shipments=[])
    check("closed lost, order already canceled -> noop (idempotent)",
          res == "noop" and not p17.cancelled)

    # Dry-run does not cancel.
    res, _, _, p17 = reconcile(lost, existing=EXISTING_HOLD, shipments=[], dry_run=True)
    check("closed lost dry-run -> would_cancel, no cancel call",
          res == "would_cancel" and not p17.cancelled)
finally:
    hold_sync.CLOSED_LOST_CANCEL_ENABLED = False
    hold_sync.HS_CANCEL_RESULT_PROP = ""

# ---------------------------------------------------------------------------
print("4) SO retail pricing + order-level discount (gated SO_RETAIL_PRICING)")
transform_order.SO_RETAIL_PRICING = True
transform_order.PIPE17_ORDER_DISCOUNT_FIELD = "discount"
rbody, _, _ = build_order(DEAL_USD, LINE_ITEMS, CONTACT)
rchair = next(li for li in rbody["lineItems"] if li["sku"] == "11-03-00-50")
check("retail mode itemPrice = list unit price (250, not net 233.1)", rchair["itemPrice"] == 250)
check("retail subtotal = sum(price*qty) (250*25 + 1171.25 = 7421.25)", rbody["subTotalPrice"] == 7421.25)
check("order discount = retail - net (7421.25 - 6998.75 = 422.5)", rbody["discount"] == 422.5)
check("retail total unchanged = net + tax (6998.75)", rbody["totalPrice"] == 6998.75)
# A line with no discount (amount == price*qty) adds no discount field.
nodisc, _, _ = build_order(DEAL_USD, [CHAIR], None)
check("no discount -> no discount field on order", "discount" not in nodisc)
transform_order.SO_RETAIL_PRICING = False   # reset the module flag after the gated test

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
