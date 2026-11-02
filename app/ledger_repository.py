"""
Database provider for BigPMS payment ingestion.
"""
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, Sequence

from psycopg2.extensions import cursor as Cursor
from psycopg2.extras import Json

from db import get_conn, put_conn

if TYPE_CHECKING:
    from feed_sync import LedgerEvent

SOURCE = "bigpms_ledger_events"
PROPERTY_ID = "prop-001"


class SyncStateError(RuntimeError):
    """Raised when the persisted sync state is missing or changed unexpectedly."""


class LedgerRepository:
    def __init__(self, source: str = SOURCE, property_id: str = PROPERTY_ID):
        self.source = source
        self.property_id = property_id

    # -- shared helpers (run inside the caller's transaction) ---------------

    @staticmethod
    def _lock_event(cur: Cursor, event_id: str) -> None:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (event_id,),
        )

    @staticmethod
    def _event_exists(cur: Cursor, event_id: str) -> bool:
        cur.execute(
            "SELECT 1 FROM resident_ledger WHERE bigpms_event_id = %s LIMIT 1",
            (event_id,),
        )
        return cur.fetchone() is not None

    def _insert_payment(
        self,
        cur: Cursor,
        event_id: str,
        resident_id: str,
        amount_cents: int,
        occurred_at: datetime,
    ) -> None:
        cur.execute(
            """
            INSERT INTO resident_ledger
                (property_id, resident_id, entry_type, amount_cents,
                 bigpms_event_id, occurred_at)
            VALUES (%s, %s, 'payment', %s, %s, %s)
            """,
            (self.property_id, resident_id, amount_cents, event_id, occurred_at),
        )

    # -- webhook ------------------------------------------------------------

    def apply_webhook_payment(
        self,
        event_id: str,
        payload: dict[str, Any],
        resident_id: str,
        amount_cents: int,
        occurred_at: datetime,
    ) -> str:
        """Atomically audit a webhook and apply its payment.

        Returns "processed" or "duplicate".
        """
        conn = get_conn()
        try:
            with conn, conn.cursor() as cur:
                self._lock_event(cur, event_id)
                already_applied = self._event_exists(cur, event_id)
                cur.execute(
                    """
                    INSERT INTO webhook_events
                        (event_id, event_type, payload, status, processed_at)
                    VALUES (%s, 'payment.received', %s, %s,
                            CASE WHEN %s = 'processed' THEN now() END)
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    (
                        event_id,
                        Json(payload),
                        "duplicate" if already_applied else "processed",
                        "duplicate" if already_applied else "processed",
                    ),
                )
                inserted = cur.fetchone() is not None
                if not inserted:
                    cur.execute(
                        "UPDATE webhook_events SET status = 'duplicate' "
                        "WHERE event_id = %s",
                        (event_id,),
                    )
                    return "duplicate"
                if already_applied:
                    return "duplicate"
                self._insert_payment(
                    cur, event_id, resident_id, amount_cents, occurred_at
                )
                return "processed"
        finally:
            put_conn(conn)

    # -- feed sync ----------------------------------------------------------

    def start_run(self) -> tuple[int, int]:
        """Return (durable cursor, new sync_runs id)."""
        conn = get_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT cursor_seq FROM sync_state WHERE source = %s",
                    (self.source,),
                )
                row = cur.fetchone()
                if row is None:
                    raise SyncStateError(f"Missing sync_state row for {self.source}")
                cursor_seq = int(row[0])
                cur.execute(
                    """
                    INSERT INTO sync_runs
                        (source, cursor_start, cursor_end, status,
                         events_fetched, events_applied, events_skipped)
                    VALUES (%s, %s, %s, 'running', 0, 0, 0)
                    RETURNING id
                    """,
                    (self.source, cursor_seq, cursor_seq),
                )
                return cursor_seq, int(cur.fetchone()[0])
        finally:
            put_conn(conn)

    def apply_page(
        self,
        run_id: int,
        expected_cursor: int,
        events: Sequence["LedgerEvent"],
        next_cursor: int,
    ) -> tuple[int, int]:
        """Atomically insert new payments and advance the cursor.

        Returns (applied, skipped) counts for the page.
        """
        applied = skipped = 0
        conn = get_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT cursor_seq FROM sync_state WHERE source = %s FOR UPDATE",
                    (self.source,),
                )
                row = cur.fetchone()
                actual = None if row is None else int(row[0])
                if actual != expected_cursor:
                    raise SyncStateError(
                        f"Sync cursor changed during run: expected "
                        f"{expected_cursor}, found {actual}"
                    )

                for event in events:
                    self._lock_event(cur, event.event_id)
                    if self._event_exists(cur, event.event_id):
                        skipped += 1
                        continue
                    self._insert_payment(
                        cur,
                        event.event_id,
                        event.resident_id,
                        event.amount_cents,
                        event.occurred_at,
                    )
                    applied += 1

                cur.execute(
                    "UPDATE sync_state SET cursor_seq = %s WHERE source = %s",
                    (next_cursor, self.source),
                )
                cur.execute(
                    """
                    UPDATE sync_runs
                    SET cursor_end = %s,
                        events_fetched = events_fetched + %s,
                        events_applied = events_applied + %s,
                        events_skipped = events_skipped + %s
                    WHERE id = %s
                    """,
                    (next_cursor, len(events), applied, skipped, run_id),
                )
        finally:
            put_conn(conn)
        return applied, skipped

    def finish_run(
        self,
        run_id: int,
        cursor_end: int,
        events_fetched: int,
        events_applied: int,
        events_skipped: int,
        status: str,
        error: Optional[str],
    ) -> None:
        conn = get_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE sync_runs
                    SET cursor_end = %s,
                        events_fetched = %s,
                        events_applied = %s,
                        events_skipped = %s,
                        status = %s,
                        error = %s,
                        finished_at = now()
                    WHERE id = %s
                    """,
                    (
                        cursor_end,
                        events_fetched,
                        events_applied,
                        events_skipped,
                        status,
                        error,
                        run_id,
                    ),
                )
                cur.execute(
                    """
                    UPDATE sync_state
                    SET last_run_at = now(), last_status = %s
                    WHERE source = %s
                    """,
                    (status, self.source),
                )
        finally:
            put_conn(conn)
