"""BigPMS webhook subscription and callback processing."""
import hashlib
import hmac
import json
import logging
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
)
import requests
from psycopg2.extras import Json
from psycopg2.extensions import cursor as Cursor

from db import get_conn, put_conn

log = logging.getLogger("leaseflow.webhook")

PROPERTY_ID = "prop-001"


class InvalidWebhookSignature(Exception):
    """Raised when a callback's signature is missing or invalid."""


class InvalidWebhookPayload(Exception):
    """Raised when a signed callback does not match the payment event contract."""


class PaymentData(BaseModel):
    model_config = ConfigDict(extra="allow")

    resident_id: str = Field(min_length=1)
    amount_cents: StrictInt = Field(gt=0)


class PaymentReceivedEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1)
    event_type: Literal["payment.received"]
    occurred_at: AwareDatetime
    data: PaymentData


def register_webhook_subscription(
    bigpms_api_url: str, callback_url: str, secret: str
) -> None:
    response = requests.post(
        f"{bigpms_api_url.rstrip('/')}/api/v1/webhook-subscriptions",
        json={"callback_url": callback_url, "secret": secret},
        timeout=10,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or not result.get("subscription_id"):
        raise RuntimeError("BigPMS did not confirm the webhook subscription")

    log.info(
        "Registered BigPMS webhook subscription %s for %s",
        result["subscription_id"],
        callback_url,
    )


def _validate_signature(raw_body: bytes, signature: str | None, secret: str) -> None:
    if not signature or len(signature) != hashlib.sha256().digest_size * 2:
        raise InvalidWebhookSignature
    try:
        received = bytes.fromhex(signature)
    except ValueError as exc:
        raise InvalidWebhookSignature from exc

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, received):
        raise InvalidWebhookSignature


def _parse_payment_event(raw_body: bytes) -> PaymentReceivedEvent:
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidWebhookPayload("Request body must be valid JSON") from exc

    try:
        return PaymentReceivedEvent.model_validate(payload)
    except ValidationError as exc:
        raise InvalidWebhookPayload(str(exc)) from exc


def _insert_webhook_event(cur: Cursor, event: PaymentReceivedEvent) -> bool:
    cur.execute(
        """
        INSERT INTO webhook_events
            (event_id, event_type, payload, status, processed_at)
        VALUES (%s, 'payment.received', %s, 'processed', now())
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """,
        (event.event_id, Json(event.model_dump(mode="json"))),
    )
    return cur.fetchone() is not None


def _mark_duplicate(cur: Cursor, event_id: str) -> None:
    cur.execute(
        "UPDATE webhook_events SET status = 'duplicate' WHERE event_id = %s",
        (event_id,),
    )


def _insert_ledger_payment(cur: Cursor, event: PaymentReceivedEvent) -> None:
    cur.execute(
        """
        INSERT INTO resident_ledger
            (property_id, resident_id, entry_type, amount_cents,
             bigpms_event_id, occurred_at)
        VALUES (%s, %s, 'payment', %s, %s, %s)
        """,
        (
            PROPERTY_ID,
            event.data.resident_id,
            event.data.amount_cents,
            event.event_id,
            event.occurred_at,
        ),
    )


def process_webhook(raw_body: bytes, signature: str | None, secret: str) -> str:
    """Verify and atomically apply a BigPMS payment callback."""
    try:
        _validate_signature(raw_body, signature, secret)
    except InvalidWebhookSignature:
        log.warning("Rejected BigPMS webhook with missing or invalid signature")
        raise

    event = _parse_payment_event(raw_body)

    try:
        conn = get_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    if not _insert_webhook_event(cur, event):
                        _mark_duplicate(cur, event.event_id)
                        result = "duplicate"
                    else:
                        _insert_ledger_payment(cur, event)
                        result = "processed"
        finally:
            put_conn(conn)
    except Exception:
        log.exception("Failed to process BigPMS webhook event %s", event.event_id)
        raise

    if result == "duplicate":
        log.info("Acknowledged duplicate BigPMS event %s", event.event_id)
    else:
        log.info("Applied BigPMS payment event %s", event.event_id)
    return result
