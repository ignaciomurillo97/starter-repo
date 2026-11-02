"""LeaseFlow Integration Service — your workspace.

You are building LeaseFlow's integration layer with BigPMS (the property
management system of record). Four TODOs, described fully in docs/SPEC.md:

  1. Register a webhook subscription with BigPMS, and implement the callback
     endpoint (POST /webhooks/bigpms)
  2. sync_bigpms_ledger_events()  — scheduled feed sync (scheduler wired below)
  3. GET /api/v1/reconciliation — reconciliation report
  4. README write-up

The health check, DB pool (db.py), and scheduler wiring are done for you.
Structure the rest however you like — add modules freely.
"""
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request, Response

from db import dict_cursor, get_conn, put_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("leaseflow")

BIGPMS_API_URL = os.environ.get("BIGPMS_API_URL", "http://localhost:3001")
CALLBACK_URL = os.environ.get("CALLBACK_URL", "http://localhost:8000/webhooks/bigpms")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "leaseflow-dev-secret")
SYNC_INTERVAL_SECONDS = int(os.environ.get("SYNC_INTERVAL_SECONDS", "30"))

app = FastAPI(title="LeaseFlow Integration Service")


# ---------------------------------------------------------------------------
# Provided endpoints (working — no changes needed)
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return {"status": "ok", "service": "leaseflow-integration"}
    finally:
        put_conn(conn)


@app.get("/api/v1/residents")
def list_residents():
    conn = get_conn()
    try:
        with conn, dict_cursor(conn) as cur:
            cur.execute("""
                SELECT r.id, r.name, u.unit_number
                FROM residents r JOIN units u ON u.id = r.unit_id
                ORDER BY r.id
            """)
            return {"residents": cur.fetchall()}
    finally:
        put_conn(conn)


# ---------------------------------------------------------------------------
# TODO 1 — Webhook subscription + callback endpoint (see SPEC section 1)
#
# Register with BigPMS so it starts sending events:
#   POST {BIGPMS_API_URL}/api/v1/webhook-subscriptions
#   body: {"callback_url": CALLBACK_URL, "secret": WEBHOOK_SECRET}
# Registration is idempotent — safe to do on every startup.
# ---------------------------------------------------------------------------

@app.on_event("startup")
def register_webhook_subscription():
    # TODO: implement — see docs/SPEC.md
    log.info("webhook subscription: not implemented yet")


@app.post("/webhooks/bigpms")
async def bigpms_webhook(request: Request):
    raw_body = await request.body()  # you'll need the raw bytes for signature verification
    # TODO: implement — see docs/SPEC.md
    return Response(status_code=501, content="Not implemented")


# ---------------------------------------------------------------------------
# TODO 2 — Scheduled feed sync (see SPEC section 2)
# ---------------------------------------------------------------------------

def sync_bigpms_ledger_events():
    """Runs every SYNC_INTERVAL_SECONDS (scheduler is wired below)."""
    # TODO: implement — see docs/SPEC.md
    log.info("sync_bigpms_ledger_events: not implemented yet")


scheduler = BackgroundScheduler()
scheduler.add_job(sync_bigpms_ledger_events, "interval", seconds=SYNC_INTERVAL_SECONDS,
                  id="bigpms_ledger_events_sync", max_instances=1)


@app.on_event("startup")
def start_scheduler():
    scheduler.start()
    log.info("Scheduler started (sync every %ss)", SYNC_INTERVAL_SECONDS)


@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# TODO 3 — Reconciliation report (see SPEC section 3)
# ---------------------------------------------------------------------------

@app.get("/api/v1/reconciliation")
def reconciliation_report():
    # TODO: implement — see docs/SPEC.md
    return Response(status_code=501, content="Not implemented")
