"""Cloud Run HTTP service behind the ops "Sync Repair Requests" form.

An Airtable automation (record-created on the requests table) POSTs here the moment a
form is submitted. The service diagnoses + heals via repair.py, then writes the result
back three ways: onto the request row (Status / Diagnosis / Outcome), as an Airtable
comment (the ops thread), and to Slack. The heavy lifting is in repair.py; this module
is just the webhook + the writeback.

Run locally:  gunicorn repair_service:app --bind :8080
Health check: GET /           -> "ok"
Webhook:      POST /repair     (X-Repair-Secret header; JSON {record_id,pipe17_id,type,force})
"""
import datetime
import logging
import os

from flask import Flask, request, jsonify

import repair
import airtable_client as at
import notify
from config import (
    REPAIR_REQUESTS_TABLE, REPAIR_WEBHOOK_SECRET,
    RF_STATUS, RF_DIAGNOSIS, RF_OUTCOME, RF_PROCESSED_AT,
    REPAIR_STATUS_PROCESSING, REPAIR_STATUS_NOT_HEALED,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipe17-airtable")

app = Flask(__name__)


@app.get("/")
def health():
    return "ok", 200


@app.post("/repair")
def handle():
    # Reject anything without the shared secret (the service is public).
    if REPAIR_WEBHOOK_SECRET and request.headers.get("X-Repair-Secret") != REPAIR_WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    pipe17_id = (data.get("pipe17_id") or "").strip()
    kind = data.get("type") or "auto"
    force = _truthy(data.get("force"))
    record_id = data.get("record_id")
    if not pipe17_id:
        return jsonify({"ok": False, "error": "missing pipe17_id"}), 400

    # Optimistic "Processing" so ops see the request was picked up.
    _set_row(record_id, {RF_STATUS: REPAIR_STATUS_PROCESSING})
    try:
        result = repair.diagnose_and_heal(pipe17_id, kind=kind, force=force)
    except Exception as e:
        log.exception("Repair failed for %s", pipe17_id)
        result = {"ok": False, "status": REPAIR_STATUS_NOT_HEALED,
                  "diagnosis": f"The repair service hit an error: {e}",
                  "outcome": "Nothing was written. Check the service logs and retry."}
    _writeback(record_id, pipe17_id, result)
    return jsonify(result), 200


def _truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes", "on", "checked") if v is not None else False


def _set_row(record_id, fields):
    clean = {k: v for k, v in (fields or {}).items() if k and v is not None}
    if not (REPAIR_REQUESTS_TABLE and record_id and clean):
        return
    try:
        at.update_record(REPAIR_REQUESTS_TABLE, record_id, clean)
    except Exception:
        log.exception("Repair-request row update failed (%s)", record_id)


def _writeback(record_id, pipe17_id, result):
    status = result.get("status")
    diag = result.get("diagnosis") or ""
    outcome = result.get("outcome") or ""
    _set_row(record_id, {
        RF_STATUS: status,
        RF_DIAGNOSIS: diag,
        RF_OUTCOME: outcome,
        RF_PROCESSED_AT: datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    body = f"Repair for {pipe17_id} → {status or 'done'}\n\n{diag}\n\n{outcome}".strip()
    if REPAIR_REQUESTS_TABLE and record_id:
        at.create_comment(REPAIR_REQUESTS_TABLE, record_id, body)
    notify.slack(f":wrench: *Sync repair* — `{pipe17_id}` → *{status or 'done'}*\n{diag}\n{outcome}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
