"""Mock BigPMS — the property management system of record.

This service simulates the partner platform you're integrating with. It is
DELIBERATELY imperfect, the way real partner APIs are:

  * GET /api/v1/ledger-events sometimes returns 500 or 429 (with Retry-After)
  * pages occasionally contain a duplicate event
  * webhooks are sometimes delivered twice
  * webhooks are occasionally sent with an INVALID signature (reject those!)
  * events delivered by webhook ALSO appear in the ledger-events feed

BigPMS only sends webhooks to callbacks registered via the subscription API:

  POST /api/v1/webhook-subscriptions   {"callback_url": "...", "secret": "..."}

Do not modify this service. Treat it like a partner you don't control.
Watch its logs: docker-compose logs -f mock-bigpms
"""
import hashlib
import hmac
import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s BigPMS %(levelname)s %(message)s")
log = logging.getLogger("bigpms")

WEBHOOK_INTERVAL_SECONDS = int(os.environ.get("WEBHOOK_INTERVAL_SECONDS", "15"))

RENTS = {
    "res-101": 150000, "res-102": 145000, "res-103": 160000, "res-104": 172500,
    "res-105": 150000, "res-106": 155000, "res-107": 140000, "res-108": 165000,
}

# BigPMS's authoritative payment history. seq is a monotonically increasing cursor.
# evt-1001..evt-1012 are already in LeaseFlow's local ledger; evt-1013..evt-1020
# are not (your sync job should backfill them).
PAYMENTS = [
    {"seq": 1,  "event_id": "evt-1001", "resident_id": "res-101", "amount_cents": 150000, "method": "ach",  "occurred_at": "2026-04-03T14:00:00Z"},
    {"seq": 2,  "event_id": "evt-1002", "resident_id": "res-102", "amount_cents": 145000, "method": "card", "occurred_at": "2026-04-04T09:30:00Z"},
    {"seq": 3,  "event_id": "evt-1003", "resident_id": "res-103", "amount_cents": 160000, "method": "ach",  "occurred_at": "2026-04-02T16:15:00Z"},
    {"seq": 4,  "event_id": "evt-1004", "resident_id": "res-104", "amount_cents": 172500, "method": "ach",  "occurred_at": "2026-04-05T11:00:00Z"},
    {"seq": 5,  "event_id": "evt-1005", "resident_id": "res-105", "amount_cents": 150000, "method": "card", "occurred_at": "2026-04-03T10:45:00Z"},
    {"seq": 6,  "event_id": "evt-1006", "resident_id": "res-106", "amount_cents": 155000, "method": "ach",  "occurred_at": "2026-04-06T13:20:00Z"},
    {"seq": 7,  "event_id": "evt-1007", "resident_id": "res-107", "amount_cents": 140000, "method": "ach",  "occurred_at": "2026-04-04T08:00:00Z"},
    {"seq": 8,  "event_id": "evt-1008", "resident_id": "res-108", "amount_cents": 165000, "method": "card", "occurred_at": "2026-04-05T17:40:00Z"},
    {"seq": 9,  "event_id": "evt-1009", "resident_id": "res-101", "amount_cents": 150000, "method": "ach",  "occurred_at": "2026-05-03T14:10:00Z"},
    {"seq": 10, "event_id": "evt-1010", "resident_id": "res-102", "amount_cents": 145000, "method": "card", "occurred_at": "2026-05-04T09:00:00Z"},
    {"seq": 11, "event_id": "evt-1011", "resident_id": "res-103", "amount_cents": 160000, "method": "ach",  "occurred_at": "2026-05-03T15:30:00Z"},
    {"seq": 12, "event_id": "evt-1012", "resident_id": "res-104", "amount_cents": 172500, "method": "ach",  "occurred_at": "2026-05-05T12:00:00Z"},
    {"seq": 13, "event_id": "evt-1013", "resident_id": "res-105", "amount_cents": 150000, "method": "card", "occurred_at": "2026-05-04T10:20:00Z"},
    {"seq": 14, "event_id": "evt-1014", "resident_id": "res-106", "amount_cents": 155000, "method": "ach",  "occurred_at": "2026-05-06T13:00:00Z"},
    {"seq": 15, "event_id": "evt-1015", "resident_id": "res-107", "amount_cents": 140000, "method": "ach",  "occurred_at": "2026-05-05T08:30:00Z"},
    {"seq": 16, "event_id": "evt-1016", "resident_id": "res-108", "amount_cents": 165000, "method": "card", "occurred_at": "2026-05-06T18:00:00Z"},
    {"seq": 17, "event_id": "evt-1017", "resident_id": "res-101", "amount_cents": 150000, "method": "ach",  "occurred_at": "2026-06-03T14:05:00Z"},
    {"seq": 18, "event_id": "evt-1018", "resident_id": "res-102", "amount_cents": 145000, "method": "card", "occurred_at": "2026-06-04T09:15:00Z"},
    {"seq": 19, "event_id": "evt-1019", "resident_id": "res-103", "amount_cents": 160000, "method": "ach",  "occurred_at": "2026-06-03T16:00:00Z"},
    {"seq": 20, "event_id": "evt-1020", "resident_id": "res-104", "amount_cents": 172500, "method": "ach",  "occurred_at": "2026-06-05T11:30:00Z"},
]

_lock = threading.Lock()
_next_seq = 21
_next_evt = 2001
_subscriptions = {}  # callback_url -> {"secret": ..., "subscription_id": ...}
_next_sub = 1

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Webhook subscription API
# ---------------------------------------------------------------------------

@app.post("/api/v1/webhook-subscriptions")
def create_subscription():
    global _next_sub
    body = request.get_json(silent=True) or {}
    callback_url = body.get("callback_url")
    secret = body.get("secret")
    if not callback_url or not secret:
        return jsonify({"error": "callback_url and secret are required"}), 400

    with _lock:
        existing = _subscriptions.get(callback_url)
        if existing:
            existing["secret"] = secret
            log.info("subscription updated: %s", callback_url)
            return jsonify({"subscription_id": existing["subscription_id"],
                            "callback_url": callback_url,
                            "events": ["payment.received"]}), 200
        sub = {"subscription_id": f"sub-{_next_sub:04d}", "secret": secret}
        _next_sub += 1
        _subscriptions[callback_url] = sub
        log.info("subscription created: %s -> %s", sub["subscription_id"], callback_url)
        return jsonify({"subscription_id": sub["subscription_id"],
                        "callback_url": callback_url,
                        "events": ["payment.received"]}), 201


@app.get("/api/v1/webhook-subscriptions")
def list_subscriptions():
    with _lock:
        subs = [{"subscription_id": s["subscription_id"], "callback_url": url,
                 "events": ["payment.received"]}
                for url, s in _subscriptions.items()]
    return jsonify({"subscriptions": subs})


# ---------------------------------------------------------------------------
# Ledger events feed (for the scheduled sync)
# ---------------------------------------------------------------------------

@app.get("/api/v1/ledger-events")
def ledger_events():
    # Simulated flakiness
    roll = random.random()
    if roll < 0.10:
        log.warning("ledger-events: simulated 500")
        return jsonify({"error": "internal server error"}), 500
    if roll < 0.20:
        log.warning("ledger-events: simulated 429 (rate limited)")
        return jsonify({"error": "rate limited"}), 429, {"Retry-After": "2"}

    try:
        cursor = int(request.args.get("cursor", 0))
        limit = min(int(request.args.get("limit", 10)), 50)
    except ValueError:
        return jsonify({"error": "cursor and limit must be integers"}), 400

    with _lock:
        matching = [p for p in PAYMENTS if p["seq"] > cursor]
    page = [dict(p, event_type="payment.received") for p in matching[:limit]]

    # Occasionally repeat an event within the page (partners do this).
    if page and random.random() < 0.15:
        page.append(dict(page[-1]))
        log.info("ledger-events: page contains a duplicate of %s", page[-1]["event_id"])

    next_cursor = max((p["seq"] for p in page), default=cursor)
    return jsonify({
        "events": page,
        "next_cursor": next_cursor,
        "has_more": bool(matching[limit:]),
    })


# ---------------------------------------------------------------------------
# Authoritative per-resident payment totals (for reconciliation)
# ---------------------------------------------------------------------------

@app.get("/api/v1/residents/summaries")
def all_summaries():
    """Bulk totals — one call instead of N."""
    with _lock:
        totals = {}
        for p in PAYMENTS:
            entry = totals.setdefault(p["resident_id"], {"total_payments_cents": 0, "payment_count": 0})
            entry["total_payments_cents"] += p["amount_cents"]
            entry["payment_count"] += 1
    return jsonify({
        "residents": [{"resident_id": rid, **vals} for rid, vals in sorted(totals.items())],
        "as_of": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/api/v1/residents/<resident_id>/summary")
def resident_summary(resident_id):
    with _lock:
        total = sum(p["amount_cents"] for p in PAYMENTS if p["resident_id"] == resident_id)
        count = sum(1 for p in PAYMENTS if p["resident_id"] == resident_id)
    return jsonify({
        "resident_id": resident_id,
        "total_payments_cents": total,
        "payment_count": count,
        "as_of": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/health")
def health():
    with _lock:
        n_subs = len(_subscriptions)
    return jsonify({"status": "ok", "service": "mock-bigpms", "webhook_subscriptions": n_subs})


# ---------------------------------------------------------------------------
# Webhook sender (background thread)
# ---------------------------------------------------------------------------

def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post_webhook(url: str, body: bytes, signature: str, label: str):
    try:
        resp = requests.post(
            url,
            data=body,
            headers={"Content-Type": "application/json", "X-BigPMS-Signature": signature},
            timeout=5,
        )
        log.info("webhook %s -> %s HTTP %s", label, url, resp.status_code)
    except requests.RequestException as exc:
        log.warning("webhook %s -> %s delivery failed: %s", label, url, exc)


def _emit_new_payment():
    global _next_seq, _next_evt
    with _lock:
        subscribers = dict(_subscriptions)

    if not subscribers:
        log.info("no webhook subscriptions registered; skipping emit "
                 "(register via POST /api/v1/webhook-subscriptions)")
        return

    with _lock:
        resident_id = random.choice(list(RENTS))
        amount = RENTS[resident_id] if random.random() < 0.7 else RENTS[resident_id] // 2
        event = {
            "seq": _next_seq,
            "event_id": f"evt-{_next_evt}",
            "resident_id": resident_id,
            "amount_cents": amount,
            "method": random.choice(["ach", "card"]),
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
        PAYMENTS.append(event)  # the event also appears in the ledger-events feed
        _next_seq += 1
        _next_evt += 1

    payload = {
        "event_id": event["event_id"],
        "event_type": "payment.received",
        "occurred_at": event["occurred_at"],
        "data": {
            "resident_id": event["resident_id"],
            "amount_cents": event["amount_cents"],
            "method": event["method"],
            "seq": event["seq"],
        },
    }
    body = json.dumps(payload).encode()

    for url, sub in subscribers.items():
        signature = _sign(sub["secret"], body)

        # Occasionally lead with a bad signature (your endpoint should reject it).
        if random.random() < 0.15:
            log.info("webhook %s: sending with INVALID signature first (expect a rejection)",
                     event["event_id"])
            _post_webhook(url, body, "deadbeef" + signature[8:], f"{event['event_id']} (bad sig)")

        _post_webhook(url, body, signature, event["event_id"])

        # Occasionally deliver twice (your endpoint should be idempotent).
        if random.random() < 0.25:
            log.info("webhook %s: delivering a duplicate", event["event_id"])
            _post_webhook(url, body, signature, f"{event['event_id']} (duplicate)")


def _webhook_loop():
    log.info("webhook sender: checking every %ss for registered subscriptions",
             WEBHOOK_INTERVAL_SECONDS)
    while True:
        time.sleep(WEBHOOK_INTERVAL_SECONDS)
        try:
            _emit_new_payment()
        except Exception:
            log.exception("webhook sender: unexpected error")


threading.Thread(target=_webhook_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3001)
