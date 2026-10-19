"""Offline tests for order-slip attach + public-URL population. No network, no creds.

Run: HUBSPOT_TOKEN=x PIPE17_API_KEY=x AIRTABLE_API_KEY=x python3.12 selftest_orderslip.py
"""
import main
from config import O_ORDER_NUMBER, O_ORDER_ATTACHMENTS, O_ORDER_DETAILS

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


# Stub rendering, attach, upload, and Airtable writes.
main.docs_render.order_slip_html = lambda o: "<html></html>"
main.docs_render.render_pdf = lambda html: b"%PDF-fake"

attached = []
main.airtable_attach.attach_pdf = lambda rid, fid, fn, pdf: attached.append((rid, fid))

uploaded = []
def _fake_upload(num, pdf):
    uploaded.append(num)
    return "https://storage.googleapis.com/pipe17-order-slips-public/order-slips/%s-abc.pdf" % str(num).lstrip("#")
main.gcs_upload.upload_order_slip = _fake_upload

updates = []
class _At:
    def update_record(self, table, rid, fields):
        updates.append((rid, fields))
main.at = _At()


def run(recs, raw):
    attached.clear(); uploaded.clear(); updates.clear()
    main._generate_order_slips(recs, raw)


# Case 1: no attachment, no URL -> attach + upload + write URL
run([{"id": "r1", "fields": {O_ORDER_NUMBER: "#A"}}], {"#A": {"extOrderId": "#A"}})
check("new order: attached", attached == [("r1", O_ORDER_ATTACHMENTS)])
check("new order: URL uploaded + written to Order Details",
      len(updates) == 1 and updates[0][0] == "r1"
      and updates[0][1].get(O_ORDER_DETAILS, "").startswith("https://storage.googleapis.com/"))

# Case 2: attachment already present, URL missing -> only the URL (backlog heal)
run([{"id": "r2", "fields": {O_ORDER_NUMBER: "#B", O_ORDER_ATTACHMENTS: [{"id": "att"}]}}],
    {"#B": {"extOrderId": "#B"}})
check("backlog order: not re-attached", attached == [])
check("backlog order: URL still written", len(updates) == 1 and O_ORDER_DETAILS in updates[0][1])

# Case 3: both present -> nothing happens (no render, no writes)
run([{"id": "r3", "fields": {O_ORDER_NUMBER: "#C", O_ORDER_ATTACHMENTS: [{"id": "a"}],
                             O_ORDER_DETAILS: "https://existing"}}],
    {"#C": {"extOrderId": "#C"}})
check("complete order: nothing written", attached == [] and updates == [])

# Case 4: no bucket configured (upload returns None) -> attach only, no URL write, no error
main.gcs_upload.upload_order_slip = lambda num, pdf: None
run([{"id": "r4", "fields": {O_ORDER_NUMBER: "#D"}}], {"#D": {"extOrderId": "#D"}})
check("no bucket: attaches but writes no URL (safe no-op)",
      attached == [("r4", O_ORDER_ATTACHMENTS)] and updates == [])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
