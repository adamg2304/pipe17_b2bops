"""Offline tests: Shipment Details Link is published with the packing list, backfilled
from the existing file, and cleared with it. No network, no creds.

Run: PIPE17_API_KEY=x AIRTABLE_API_KEY=x HUBSPOT_TOKEN=x python3 selftest_shipment_link.py
"""
import main
from config import (SHIP_PACKING_LIST_ATTACH as FILE, SHIP_DETAILS_LINK as LINK,
                    SHIPMENT_NUMBER_FIELD as NUM, F_LINE_ITEMS)

passed = failed = 0


def check(name, cond):
    global passed, failed
    passed, failed = (passed + 1, failed) if cond else (passed, failed + 1)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


calls = {}


def reset(bucket=True):
    calls.clear()
    calls.update(updates=[], attached=[], uploaded=[], downloaded=[])
    main.docs_render.render_pdf = lambda html: b"RENDERED"
    main.docs_render.packing_list_html = lambda s: ""
    main.airtable_attach.attach_pdf = lambda rid, fid, fn, pdf: calls["attached"].append(pdf)
    main.airtable_attach.download = lambda url: calls["downloaded"].append(url) or b"EXISTING"

    def upload(num, pdf):
        calls["uploaded"].append(pdf)
        return f"https://storage.googleapis.com/b/packing-lists/{num.lstrip('#')}-x.pdf" if bucket else None
    main.gcs_upload.upload_packing_list = upload
    main.at.update_record = lambda t, rid, flds: calls["updates"].append(flds)


def gen(fields, status="sentToFulfillment", prior=None):
    num = fields[NUM]
    sr = {"extShipmentId": num, "status": status}
    main._generate_packing_lists([{"id": "rec1", "fields": fields}], {num: sr}, prior or {})


def links_written():
    return [u[LINK] for u in calls["updates"] if u.get(LINK)]


ATT = [{"url": "https://v5.airtableusercontent.com/x", "filename": "PackingList_BE1.pdf"}]

# New sent shipment: file attached and the SAME bytes published as the link.
reset(); gen({NUM: "#BE1", F_LINE_ITEMS: "1 x A"})
check("new sent SR: packing list attached", calls["attached"] == [b"RENDERED"])
check("new sent SR: same PDF uploaded", calls["uploaded"] == [b"RENDERED"])
check("new sent SR: link written", links_written() == ["https://storage.googleapis.com/b/packing-lists/BE1-x.pdf"])

# Backlog: file present, link empty -> publish the existing file, no re-render/re-attach.
reset(); gen({NUM: "#BE2", F_LINE_ITEMS: "1 x A", FILE: ATT}, prior={"#BE2": "1 x A"})
check("backlog row: existing file downloaded", calls["downloaded"] == [ATT[0]["url"]])
check("backlog row: not re-attached", calls["attached"] == [])
check("backlog row: existing bytes published", calls["uploaded"] == [b"EXISTING"])
check("backlog row: link written", len(links_written()) == 1)

# Backlog row whose SR is past sent (fulfilled): still gets its link.
reset(); gen({NUM: "#BE2b", FILE: ATT}, status="fulfilled")
check("fulfilled backlog row: no link (only sent SRs are touched in-sync)", links_written() == [])

# File + link already present, contents unchanged: nothing happens.
reset(); gen({NUM: "#BE3", F_LINE_ITEMS: "1 x A", FILE: ATT, LINK: "https://old"},
             prior={"#BE3": "1 x A"})
check("complete row: no writes", not calls["updates"] and not calls["uploaded"])

# Line items changed: file and link both cleared, then both replaced.
reset(); gen({NUM: "#BE4", F_LINE_ITEMS: "2 x A", FILE: ATT, LINK: "https://old"},
             prior={"#BE4": "1 x A"})
check("re-split: file and link cleared first",
      calls["updates"][0] == {FILE: [], LINK: None})
check("re-split: new link written", len(links_written()) == 1)

# SR back in review: stale file and link both cleared.
reset(); gen({NUM: "#BE5", FILE: ATT, LINK: "https://old"}, status="reviewRequired")
check("SR back in review: file and link cleared", calls["updates"] == [{FILE: [], LINK: None}])

# No bucket configured: file still attached, no link write, no crash.
reset(bucket=False); gen({NUM: "#BE6", F_LINE_ITEMS: "1 x A"})
check("no bucket: file attached", calls["attached"] == [b"RENDERED"])
check("no bucket: no link write", links_written() == [])

# Upload failure never blocks: the attachment stands.
reset()
main.gcs_upload.upload_packing_list = lambda num, pdf: (_ for _ in ()).throw(RuntimeError("gcs down"))
gen({NUM: "#BE7", F_LINE_ITEMS: "1 x A"})
check("upload failure: file still attached, no link", calls["attached"] and not links_written())

# One-off backfill: only rows with a file and no link.
reset()
main.at.list_records = lambda t, formula, field_ids=None: [
    {"id": "r1", "fields": {NUM: "#A", FILE: ATT}},                     # needs link
    {"id": "r2", "fields": {NUM: "#B", FILE: ATT, LINK: "https://x"}},  # done
    {"id": "r3", "fields": {NUM: "#C"}},                                # no file
]
w, f = main.backfill_shipment_links(dry_run=False)
check("backfill: only the file-without-link row written", (w, f) == (1, 0) and len(links_written()) == 1)
reset()
w, f = main.backfill_shipment_links(dry_run=True)
check("backfill dry run: no writes", not calls["updates"] and not calls["uploaded"])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
