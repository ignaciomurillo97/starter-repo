"""BigPMS webhook subscription and callback processing."""
import hashlib
import hmac
import json
import logging
from typing import Literal, Optional

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
)
import requests

from ledger_repository import LedgerRepository

log = logging.getLogger("leaseflow.webhook")


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


def process_webhook(
    raw_body: bytes,
    signature: str | None,
    secret: str,
    repository: Optional[LedgerRepository] = None,
) -> str:
    """Verify and atomically apply a BigPMS payment callback."""
    try:
        _validate_signature(raw_body, signature, secret)
    except InvalidWebhookSignature:
        log.warning("Rejected BigPMS webhook with missing or invalid signature")
        raise

    event = _parse_payment_event(raw_body)
    repository = repository or LedgerRepository()

    try:
        result = repository.apply_webhook_payment(
            event.event_id,
            event.model_dump(mode="json"),
            event.data.resident_id,
            event.data.amount_cents,
            event.occurred_at,
        )
    except Exception:
        log.exception("Failed to process BigPMS webhook event %s", event.event_id)
        raise

    if result == "duplicate":
        log.info("Acknowledged duplicate BigPMS event %s", event.event_id)
    else:
        log.info("Applied BigPMS payment event %s", event.event_id)
    return result
