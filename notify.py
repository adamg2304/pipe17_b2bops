"""Slack notifications for the sync jobs.

Posts to an incoming-webhook URL in SLACK_WEBHOOK_URL. When that env var is unset
(local runs, tests, or before Ops wires the webhook) it logs the message and returns
False instead of raising, so notification never breaks a sync. Keep the dependency
one-way: notify imports nothing from the sync modules.
"""
import logging
import os

import requests

log = logging.getLogger("pipe17-airtable")

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()


def slack(text, blocks=None):
    """Post a message to Slack. Returns True on a delivered message, False otherwise.
    Never raises — a notification failure must not fail the job it is reporting on."""
    if not SLACK_WEBHOOK_URL:
        log.info("[slack disabled] %s", text)
        return False
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception:
        log.exception("Slack notification failed (continuing)")
        return False
