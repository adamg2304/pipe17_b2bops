"""Offline tests for the Airtable Orders Status -> HubSpot operations_status push, and the
Hubspot Deals table rows. No network, no creds.

Run: HUBSPOT_TOKEN=x PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3 selftest_ops_status.py
"""
import ops_status_sync as ops
import hubspot_deals_table as hdt
from config import (
    O_ORDER_NUMBER, O_STATUS, O_ORDER_TAGS, O_HUBSPOT_DEAL_LINK, O_HS_DEAL_ID_LOOKUP,
    O_DELIVERY_DATE_APPROVED, O_SCHEDULED_DELIVERY_DATE, O_SCHEDULED_DELIVERY_FRIENDLY,
    O_LATESHIPMENT_LINK, O_COMPLETED_DATE,
    HD_DEAL_ID, HD_DEAL_NAME, HD_ORDER_LINK, HD_DEAL_LINK, HD_FLOORPLAN, HD_COMPANY,
    HD_CONTACT, HD_CONTACT_EMAIL, HD_SALES_CONTACT, HD_SALES_CONTACT_EMAIL, HD_ORDER_NUMBER,
    HD_STAIR_CARRY, HD_INVOICE_NUMBER,
)

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


# Live operations_status options (as read 2026-11-18).
OPTIONS = ["No Shipments Found", "In Review", "Order On Hold", "Order Processing",
           "Order Partially Shipped", "Order Shipped", "Order Partially Arrived",
           "Order Arrived", "Order Scheduled", "Order Partially Delivered", "Out Of Stock",
           "Order Canceled", "Completed"]

print("1) deal id resolution")
check("lookup wins", ops.deal_id_of({O_HS_DEAL_ID_LOOKUP: ["64576257613"],
                                      O_HUBSPOT_DEAL_LINK: "x/record/0-3/62115548017"}) == "64576257613")
check("falls back to the deal link",
      ops.deal_id_of({O_HUBSPOT_DEAL_LINK: "https://app.hubspot.com/contacts/5361087/record/0-3/62115548017"}) == "62115548017")
check("broken link (…/0-3/15378_REPL) is not a deal id",
      ops.deal_id_of({O_HUBSPOT_DEAL_LINK: "https://app.hubspot.com/contacts/5361087/record/0-3/15378_REPL"}) is None)
check("falls back to #BE prefix", ops.deal_id_of({O_ORDER_NUMBER: "#BE67797175072"}) == "67797175072")
check("falls back to #CEN prefix", ops.deal_id_of({O_ORDER_NUMBER: "#CEN66138513506"}) == "66138513506")
check("Shopify-era #BE15603 is not a deal id", ops.deal_id_of({O_ORDER_NUMBER: "#BE15603"}) is None)
check("split suffix is not a deal id", ops.deal_id_of({O_ORDER_NUMBER: "#BE67797175072 - Panels"}) is None)
check("Shopify number is not a deal id", ops.deal_id_of({O_ORDER_NUMBER: "#12345"}) is None)

print("2) skip rules (mirror the old Airtable automation)")
base = {O_ORDER_NUMBER: "#BE1", O_STATUS: "Order Shipped"}
check("normal order not skipped", ops.skip_reason(base) is None)
check("#99 test order skipped", ops.skip_reason(dict(base, **{O_ORDER_NUMBER: "#99001"})))
check("#77 test order skipped", ops.skip_reason(dict(base, **{O_ORDER_NUMBER: "#77001"})))
check("DUPLICATE-* tag skipped", ops.skip_reason(dict(base, **{O_ORDER_TAGS: ["DUPLICATE-LIT"]})))
check("Replacement Order tag skipped", ops.skip_reason(dict(base, **{O_ORDER_TAGS: ["Replacement Order"]})))
check("CS-CANCEL tag skipped", ops.skip_reason(dict(base, **{O_ORDER_TAGS: ["CS-CANCEL"]})))
check("ordinary tag not skipped", ops.skip_reason(dict(base, **{O_ORDER_TAGS: ["White Glove"]})) is None)
check("completed-dated non-Completed skipped",
      ops.skip_reason(dict(base, **{O_COMPLETED_DATE: "2026-11-01"})))
check("Completed with a date is sent",
      ops.skip_reason(dict(base, **{O_STATUS: "Completed", O_COMPLETED_DATE: "2026-11-01"})) is None)
check("blank status skipped", ops.skip_reason({O_ORDER_NUMBER: "#BE1"}))

print("3) status mapping")
check("exact option passes through", ops.map_status({O_STATUS: "Order Shipped"}, OPTIONS) == "Order Shipped")
check("case-insensitive match", ops.map_status({O_STATUS: "out of stock"}, OPTIONS) == "Out Of Stock")
check("alias Canceled -> Order Canceled", ops.map_status({O_STATUS: "Canceled"}, OPTIONS) == "Order Canceled")
check("unknown status -> None (deal untouched)", ops.map_status({O_STATUS: "QA"}, OPTIONS) is None)
check("rejected date without HubSpot option keeps the order status",
      ops.map_status({O_STATUS: "Order Arrived", O_DELIVERY_DATE_APPROVED: "Rejected"}, OPTIONS) == "Order Arrived")
check("rejected date uses the option once HubSpot has it",
      ops.map_status({O_STATUS: "Order Arrived", O_DELIVERY_DATE_APPROVED: "Rejected"},
                     OPTIONS + ["Delivery Date Rejected"]) == "Delivery Date Rejected")
check("new HubSpot option starts syncing without code change",
      ops.map_status({O_STATUS: "Order Partially Scheduled"}, OPTIONS + ["Order Partially Scheduled"])
      == "Order Partially Scheduled")

print("4) desired props + diff")
row = {O_STATUS: "Order Scheduled", O_SCHEDULED_DELIVERY_DATE: "2026-12-01",
       O_SCHEDULED_DELIVERY_FRIENDLY: "12/1/2026 - 9am-12pm", O_LATESHIPMENT_LINK: "https://ls/x"}
want = ops.desired_props(row, OPTIONS)
check("status + delivery extras", want == {
    "operations_status": "Order Scheduled", "delivery_date": "2026-12-01",
    "scheduled_delivery_date_and_time__friendly_": "12/1/2026 - 9am-12pm",
    "lateshipment_tracking_link": "https://ls/x"})
check("blank extras omitted", ops.desired_props({O_STATUS: "Order Shipped"}, OPTIONS)
      == {"operations_status": "Order Shipped"})
check("no diff when deal already matches", ops.changes(want, dict(want)) == {})
check("date compares on the day only",
      ops.changes({"delivery_date": "2026-12-01"}, {"delivery_date": "2026-12-01T00:00:00Z"}) == {})
check("only changed props returned",
      ops.changes(want, dict(want, operations_status="Order Arrived")) == {"operations_status": "Order Scheduled"})


print("5) run(): batch read, diff, patch")


class FakeAT:
    def __init__(self, rows):
        self.rows = rows
        self.formula = None

    def list_records(self, table, formula, field_ids=None):
        self.formula = formula
        return self.rows


class FakeHS:
    def __init__(self, deals):
        self.deals = deals
        self.updates = []

    def get_property_options(self, obj, prop):
        return OPTIONS

    def batch_read_deals(self, ids, props):
        return {i: dict(self.deals[i]) for i in ids if i in self.deals}

    def update_deal(self, deal_id, props):
        self.updates.append((deal_id, props))


rows = [
    {"id": "r1", "fields": {O_ORDER_NUMBER: "#BE100000000100", O_STATUS: "Order Shipped"}},           # changes
    {"id": "r2", "fields": {O_ORDER_NUMBER: "#BE100000000200", O_STATUS: "Order Arrived"}},           # unchanged
    {"id": "r3", "fields": {O_ORDER_NUMBER: "#99300", O_STATUS: "Order Shipped"}},           # skipped
    {"id": "r4", "fields": {O_ORDER_NUMBER: "#BE100000000400", O_STATUS: "QA"}},                      # unmapped
    {"id": "r5", "fields": {O_ORDER_NUMBER: "#BE100000000500", O_STATUS: "Order Shipped"}},           # deal missing
    {"id": "r6", "fields": {O_ORDER_NUMBER: "#BE100000000600", O_STATUS: "Order Scheduled"}},         # parent
    {"id": "r7", "fields": {O_ORDER_NUMBER: "#BE100000000600-2", O_STATUS: "Order Processing",
                            O_HS_DEAL_ID_LOOKUP: ["100000000600"]}},                                 # child, same deal
]
hs = FakeHS({"100000000100": {"operations_status": "Order Processing"},
             "100000000200": {"operations_status": "Order Arrived"},
             "100000000400": {}, "100000000600": {"operations_status": "Order Arrived"}})
fat = FakeAT(rows)
counts = ops.run(90, dry_run=False, at=fat, hs=hs)
check("window formula uses Last Modified", "{Last Modified}" in fat.formula and "-90" in fat.formula)
check("changed deal patched with only the diff", ("100000000100", {"operations_status": "Order Shipped"}) in hs.updates)
check("unchanged deal not patched", all(d != "100000000200" for d, _ in hs.updates))
check("test order skipped", counts.get("skipped") == 1)
check("unmapped status counted, not written", counts.get("unmapped") == 1 and all(d != "100000000400" for d, _ in hs.updates))
check("missing deal counted", counts.get("deal_not_found") == 1)
check("parent order wins over its split child",
      ("100000000600", {"operations_status": "Order Scheduled"}) in hs.updates)
check("exactly two writes", len(hs.updates) == 2)

hs2 = FakeHS({"100000000100": {"operations_status": "Order Processing"}})
dry = ops.run(90, dry_run=True, at=FakeAT(rows[:1]), hs=hs2)
check("dry run writes nothing", hs2.updates == [] and dry.get("would_update") == 1)


print("6) Hubspot Deals row fields")
props = {"dealname": "Acme HQ", "invoice_number": "INV-1", "delivery_contact_email": "d@x.com",
         "delivery_type": "White Glove", "regular_hour_delivery": "Yes (Monday-Friday, 9am-5pm)",
         "stair_carry": "false", "union_building_": "", "coi_required_": None,
         "deal_stakeholder": "Tenant", "floorplan": "https://files/plan.pdf"}
f = hdt.build_fields("555", props, order_number="#BE555", order_record_id="recORD",
                     contact={"firstname": "Ann", "lastname": "Lee", "email": "a@x.com"},
                     company={"name": "Acme"}, owner={"firstName": "Tom", "lastName": "C", "email": "t@b.com"})
check("deal id + name", f[HD_DEAL_ID] == "555" and f[HD_DEAL_NAME] == "Acme HQ")
check("deal link uses the portal id", f[HD_DEAL_LINK].endswith("/contacts/5361087/record/0-3/555"))
check("order number + order link", f[HD_ORDER_NUMBER] == "#BE555" and f[HD_ORDER_LINK] == ["recORD"])
check("company / contact / owner", f[HD_COMPANY] == "Acme" and f[HD_CONTACT] == "Ann Lee"
      and f[HD_SALES_CONTACT] == "Tom C" and f[HD_SALES_CONTACT_EMAIL] == "t@b.com")
check("delivery contact email preferred", f[HD_CONTACT_EMAIL] == "d@x.com")
check("floorplan URL becomes an attachment", f[HD_FLOORPLAN] == [{"url": "https://files/plan.pdf"}])
check("select values passed through", f[HD_STAIR_CARRY] == "false" and f[HD_INVOICE_NUMBER] == "INV-1")
check("blank props dropped", all(v not in (None, "") for v in f.values()))
check("non-URL floorplan dropped", HD_FLOORPLAN not in hdt.build_fields("1", {"floorplan": "see email"}))


print("7) Hubspot Deals row: create once, link, never duplicate")


class FakeDealsAT:
    def __init__(self, existing=None):
        self.existing = existing
        self.created, self.updated = [], []

    def find_record(self, table, field, value):
        return self.existing

    def create_record(self, table, fields):
        self.created.append(fields)
        return {"id": "recNEW"}

    def update_record(self, table, rid, fields):
        self.updated.append((rid, fields))


class FakeDealsHS:
    def get_deal_props(self, deal_id, props):
        return {"dealname": "Acme HQ", "hubspot_owner_id": "9"}

    def get_labeled_contact(self, deal_id, label):
        return None

    def first_associated_id(self, deal_id, to):
        if to == "companies":
            raise RuntimeError("403 missing scope")
        return "c1"

    def get_object_props(self, typ, oid, props):
        return {"firstname": "Ann", "lastname": "Lee"}

    def get_owner(self, oid):
        return {"firstName": "Tom", "lastName": "C"}


a = FakeDealsAT()
r = hdt.ensure_row("555", "#BE555", "recORD", hs=FakeDealsHS(), at=a, dry_run=False)
check("missing row is created", r == "created" and len(a.created) == 1)
check("contact falls back to first associated contact", a.created[0].get(HD_CONTACT) == "Ann Lee")
check("failed company lookup leaves the cell blank, row still created", HD_COMPANY not in a.created[0])

a = FakeDealsAT(existing={"id": "recOLD", "fields": {HD_ORDER_LINK: []}})
r = hdt.ensure_row("555", "#BE555", "recORD", hs=FakeDealsHS(), at=a, dry_run=False)
check("existing row gets the missing order link", r == "linked" and a.updated == [("recOLD", {HD_ORDER_LINK: ["recORD"]})])
check("existing row is never re-created", a.created == [])

a = FakeDealsAT(existing={"id": "recOLD", "fields": {HD_ORDER_LINK: ["recORD"]}})
check("already linked row left alone",
      hdt.ensure_row("555", "#BE555", "recORD", hs=FakeDealsHS(), at=a, dry_run=False) == "exists"
      and not a.updated and not a.created)

a = FakeDealsAT()
check("dry run creates nothing",
      hdt.ensure_row("555", "#BE555", None, hs=FakeDealsHS(), at=a, dry_run=True) == "would_create"
      and a.created == [])

c = hdt.ensure_rows([(None, "#X", None), ("555", "#BE555", "recORD")], hs=FakeDealsHS(),
                    at=FakeDealsAT(), dry_run=False)
check("ensure_rows counts missing deal ids and creates", c == {"no_deal_id": 1, "created": 1})

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
