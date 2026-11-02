"""Scheduled, idempotent BigPMS ledger feed synchronization."""
import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal, Optional, Union

import requests
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
)

from ledger_repository import LedgerRepository

log = logging.getLogger("leaseflow.feed_sync")

PAGE_SIZE = 50
REQUEST_TIMEOUT_SECONDS = 10
MAX_RETRIES = 3


class FeedSyncError(RuntimeError):
    """Raised when a feed sync cannot safely complete."""


class LedgerEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    seq: StrictInt = Field(gt=0)
    event_id: str = Field(min_length=1)
    event_type: Literal["payment.received"]
    resident_id: str = Field(min_length=1)
    amount_cents: StrictInt = Field(gt=0)
    occurred_at: AwareDatetime


class LedgerEventPage(BaseModel):
    model_config = ConfigDict(extra="allow")

    events: list[LedgerEvent]
    next_cursor: StrictInt = Field(ge=0)
    has_more: StrictBool


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _get_page(
    bigpms_api_url: str,
    cursor_seq: int,
    limit: int,
    max_retries: int,
    retry_base_seconds: float,
) -> LedgerEventPage:
    url = f"{bigpms_api_url.rstrip('/')}/api/v1/ledger-events"
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(
                url,
                params={"cursor": cursor_seq, "limit": limit},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise FeedSyncError(
                    f"BigPMS feed request failed after {attempt + 1} attempts: {exc}"
                ) from exc
            time.sleep(retry_base_seconds * (2**attempt))
            continue

        if response.status_code == 429 or 500 <= response.status_code < 600:
            if attempt == max_retries:
                raise FeedSyncError(
                    f"BigPMS feed returned HTTP {response.status_code} "
                    f"after {attempt + 1} attempts"
                )
            delay = _retry_after_seconds(response.headers.get("Retry-After"))
            if delay is None:
                delay = retry_base_seconds * (2**attempt)
            time.sleep(delay)
            continue

        try:
            response.raise_for_status()
            return LedgerEventPage.model_validate(response.json())
        except (requests.RequestException, ValueError, ValidationError) as exc:
            raise FeedSyncError(f"Invalid BigPMS feed response: {exc}") from exc

    raise FeedSyncError("BigPMS feed retry policy ended unexpectedly")


def _validate_page(page: LedgerEventPage, cursor_seq: int) -> None:
    if page.next_cursor < cursor_seq:
        raise FeedSyncError(
            f"BigPMS cursor moved backwards from {cursor_seq} to {page.next_cursor}"
        )
    if page.has_more and page.next_cursor <= cursor_seq:
        raise FeedSyncError(
            f"BigPMS reported more events without advancing cursor {cursor_seq}"
        )
    for event in page.events:
        if event.seq <= cursor_seq or event.seq > page.next_cursor:
            raise FeedSyncError(
                f"Event {event.event_id} sequence {event.seq} is outside "
                f"page cursor range ({cursor_seq}, {page.next_cursor}]"
            )
    last_event_seq = max((event.seq for event in page.events), default=cursor_seq)
    if page.next_cursor != last_event_seq:
        raise FeedSyncError(
            f"BigPMS next_cursor {page.next_cursor} does not match the "
            f"last event sequence {last_event_seq}"
        )


def sync_bigpms_ledger_events(
    bigpms_api_url: str,
    *,
    repository: Optional[LedgerRepository] = None,
    page_size: int = PAGE_SIZE,
    max_retries: int = MAX_RETRIES,
    retry_base_seconds: float = 0.5,
) -> dict[str, Union[int, str]]:
    """Fetch and durably apply every available BigPMS payment event."""
    if not 1 <= page_size <= PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {PAGE_SIZE}")
    if max_retries < 0:
        raise ValueError("max_retries cannot be negative")

    repository = repository or LedgerRepository()
    cursor_seq, run_id = repository.start_run()
    start_cursor = cursor_seq
    events_fetched = 0
    events_applied = 0
    events_skipped = 0

    try:
        while True:
            page = _get_page(
                bigpms_api_url,
                cursor_seq,
                page_size,
                max_retries,
                retry_base_seconds,
            )
            _validate_page(page, cursor_seq)
            events_fetched += len(page.events)
            applied, skipped = repository.apply_page(
                run_id, cursor_seq, page.events, page.next_cursor
            )
            events_applied += applied
            events_skipped += skipped
            cursor_seq = page.next_cursor
            if not page.has_more:
                break

        repository.finish_run(
            run_id, cursor_seq, events_fetched, events_applied, events_skipped,
            "success", None,
        )
    except Exception as exc:
        try:
            repository.finish_run(
                run_id, cursor_seq, events_fetched, events_applied, events_skipped,
                "failed", f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            log.exception("Could not finalize failed BigPMS sync run %s", run_id)
        log.exception("BigPMS feed sync run %s failed", run_id)
        raise

    log.info(
        "BigPMS feed sync run %s succeeded: cursor %s -> %s, "
        "fetched=%s applied=%s skipped=%s",
        run_id,
        start_cursor,
        cursor_seq,
        events_fetched,
        events_applied,
        events_skipped,
    )
    return {
        "run_id": run_id,
        "cursor_start": start_cursor,
        "cursor_end": cursor_seq,
        "events_fetched": events_fetched,
        "events_applied": events_applied,
        "events_skipped": events_skipped,
        "status": "success",
    }
