"""Feed sync tests against the real PostgreSQL database.

BigPMS HTTP calls are stubbed; everything else hits the database. Each test
uses its own sync_state source and event-ID prefix, so it neither touches the
live app's cursor nor leaves rows behind.

Run inside the app container (it provides DATABASE_URL and dependencies):

    docker compose run --rm --no-deps -v .:/workspace -w /workspace \
        -e PYTHONPATH=/workspace/app app \
        python -m unittest tests.test_feed_sync -v
"""
import hashlib
import hmac
import json
import unittest
import uuid
from unittest.mock import patch

import psycopg2

import feed_sync
import webhook
from db import get_conn, put_conn
from ledger_repository import LedgerRepository

WEBHOOK_SECRET = "test-secret"


def feed_event(seq, event_id, resident_id="res-101", amount_cents=150000):
    return {
        "seq": seq,
        "event_id": event_id,
        "event_type": "payment.received",
        "resident_id": resident_id,
        "amount_cents": amount_cents,
        "method": "ach",
        "occurred_at": "2026-07-03T14:00:00Z",
    }


def feed_response(response_body, status=200, headers=None):
    class Response:
        status_code = status

        def __init__(self):
            self.headers = headers or {}

        def raise_for_status(self):
            if status >= 400:
                raise feed_sync.requests.HTTPError(f"HTTP {status}")

        def json(self):
            return response_body

    return Response()


def feed_page(events, next_cursor=None, has_more=False):
    if next_cursor is None:
        next_cursor = max((e["seq"] for e in events), default=0)
    return {"events": events, "next_cursor": next_cursor, "has_more": has_more}


class FeedSyncDatabaseTests(unittest.TestCase):
    def setUp(self):
        unique_suffix = uuid.uuid4().hex[:12]
        self.sync_source = f"test-{unique_suffix}"
        self.event_id_prefix = f"ft-{unique_suffix}-"
        self.repository = LedgerRepository(source=self.sync_source)
        self.run_sql("INSERT INTO sync_state (source) VALUES (%s)", (self.sync_source,))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        event_id_pattern = self.event_id_prefix + "%"
        self.run_sql("DELETE FROM resident_ledger WHERE bigpms_event_id LIKE %s", (event_id_pattern,))
        self.run_sql("DELETE FROM webhook_events WHERE event_id LIKE %s", (event_id_pattern,))
        self.run_sql("DELETE FROM sync_runs WHERE source = %s", (self.sync_source,))
        self.run_sql("DELETE FROM sync_state WHERE source = %s", (self.sync_source,))

    def run_sql(self, sql, params=()):
        conn = get_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if cur.description else None
        finally:
            put_conn(conn)

    def scoped_event_id(self, suffix):
        return f"{self.event_id_prefix}{suffix}"

    def synced_event_ids(self):
        rows = self.run_sql(
            "SELECT bigpms_event_id FROM resident_ledger "
            "WHERE bigpms_event_id LIKE %s ORDER BY bigpms_event_id",
            (self.event_id_prefix + "%",),
        )
        return [row[0] for row in rows]

    def cursor_and_last_status(self):
        return self.run_sql(
            "SELECT cursor_seq, last_status FROM sync_state WHERE source = %s",
            (self.sync_source,),
        )[0]

    def sync_run_rows(self):
        return self.run_sql(
            "SELECT status, cursor_start, cursor_end, events_fetched, "
            "events_applied, events_skipped, error, finished_at "
            "FROM sync_runs WHERE source = %s ORDER BY id",
            (self.sync_source,),
        )

    def deliver_webhook(self, event_id):
        raw_body = json.dumps({
            "event_id": event_id,
            "event_type": "payment.received",
            "occurred_at": "2026-07-03T14:00:00Z",
            "data": {"resident_id": "res-101", "amount_cents": 150000},
        }).encode()
        signature = hmac.new(WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
        return webhook.process_webhook(
            raw_body, signature, WEBHOOK_SECRET, repository=self.repository
        )

    def run_sync(self, responses, **kwargs):
        kwargs.setdefault("retry_base_seconds", 0)
        with patch.object(
            feed_sync.requests, "get", side_effect=responses
        ) as get:
            try:
                return feed_sync.sync_bigpms_ledger_events(
                    "http://bigpms", repository=self.repository, **kwargs
                )
            finally:
                self.feed_requests = get.call_args_list

    def test_pages_and_skips_already_applied_and_repeated_events(self):
        self.run_sql(
            "INSERT INTO resident_ledger (property_id, resident_id, entry_type, "
            "amount_cents, bigpms_event_id, occurred_at) "
            "VALUES ('prop-001', 'res-101', 'payment', 150000, %s, now())",
            (self.scoped_event_id("seeded"),),
        )
        responses = [
            feed_response(feed_page(
                [feed_event(1, self.scoped_event_id("seeded")), feed_event(2, self.scoped_event_id("first")), feed_event(2, self.scoped_event_id("first"))],
                has_more=True,
            )),
            feed_response(feed_page([feed_event(3, self.scoped_event_id("second"))])),
        ]

        result = self.run_sync(responses)

        self.assertEqual(
            [c.kwargs["params"]["cursor"] for c in self.feed_requests], [0, 2]
        )
        self.assertTrue(all(c.kwargs["params"]["limit"] <= 50 for c in self.feed_requests))
        self.assertEqual(
            self.synced_event_ids(),
            [self.scoped_event_id("first"), self.scoped_event_id("second"), self.scoped_event_id("seeded")],
        )
        self.assertEqual(self.cursor_and_last_status(), (3, "success"))
        self.assertEqual(
            (result["events_fetched"], result["events_applied"], result["events_skipped"]),
            (4, 2, 2),
        )
        (run,) = self.sync_run_rows()
        self.assertEqual(run[:7], ("success", 0, 3, 4, 2, 2, None))
        self.assertIsNotNone(run[7])

    def test_rerun_after_success_applies_nothing_twice(self):
        response_body = feed_page([feed_event(1, self.scoped_event_id("first"))])
        self.run_sync([feed_response(response_body)])
        result = self.run_sync([feed_response(feed_page([], next_cursor=1))])

        self.assertEqual(result["cursor_start"], 1)
        self.assertEqual(self.synced_event_ids(), [self.scoped_event_id("first")])

    def test_event_already_applied_by_webhook_is_skipped(self):
        self.assertEqual(self.deliver_webhook(self.scoped_event_id("hook")), "processed")

        result = self.run_sync([feed_response(feed_page([feed_event(1, self.scoped_event_id("hook"))]))])

        self.assertEqual((result["events_applied"], result["events_skipped"]), (0, 1))
        self.assertEqual(self.synced_event_ids(), [self.scoped_event_id("hook")])

    def test_webhook_for_event_already_synced_is_duplicate_without_second_payment(self):
        self.run_sync([feed_response(feed_page([feed_event(1, self.scoped_event_id("hook"))]))])

        self.assertEqual(self.deliver_webhook(self.scoped_event_id("hook")), "duplicate")
        self.assertEqual(self.synced_event_ids(), [self.scoped_event_id("hook")])

    def test_429_waits_for_retry_after_then_recovers(self):
        responses = [
            feed_response({"error": "rate limited"}, 429, {"Retry-After": "2"}),
            feed_response(feed_page([feed_event(1, self.scoped_event_id("first"))])),
        ]
        with patch.object(feed_sync.time, "sleep") as sleep:
            result = self.run_sync(responses)

        sleep.assert_called_once_with(2.0)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.synced_event_ids(), [self.scoped_event_id("first")])

    def test_exhausted_retries_fail_run_and_keep_cursor(self):
        responses = [feed_response({}, 500)] * 3
        with patch.object(feed_sync.time, "sleep"):
            with self.assertRaisesRegex(feed_sync.FeedSyncError, "HTTP 500"):
                self.run_sync(responses, max_retries=2)

        self.assertEqual(self.cursor_and_last_status(), (0, "failed"))
        status, _, _, _, _, _, error, finished_at = self.sync_run_rows()[0]
        self.assertEqual(status, "failed")
        self.assertIn("HTTP 500", error)
        self.assertIsNotNone(finished_at)

    def test_database_failure_rolls_back_page_but_keeps_earlier_page(self):
        responses = [
            feed_response(feed_page([feed_event(1, self.scoped_event_id("first"))], has_more=True)),
            # amount exceeds INTEGER, so the second insert fails in PostgreSQL
            feed_response(feed_page([
                feed_event(2, self.scoped_event_id("second")),
                feed_event(3, self.scoped_event_id("too-big"), amount_cents=2**31),
            ])),
        ]

        with self.assertRaises(psycopg2.Error):
            self.run_sync(responses)

        self.assertEqual(self.synced_event_ids(), [self.scoped_event_id("first")])
        self.assertEqual(self.cursor_and_last_status(), (1, "failed"))
        status, start, end, fetched, applied, _, error, _ = self.sync_run_rows()[0]
        self.assertEqual((status, start, end, fetched, applied), ("failed", 0, 1, 3, 1))
        self.assertTrue(error)

    def test_rejects_non_advancing_cursor(self):
        with self.assertRaisesRegex(feed_sync.FeedSyncError, "without advancing"):
            self.run_sync([feed_response(feed_page([], next_cursor=0, has_more=True))])

        self.assertEqual(self.cursor_and_last_status(), (0, "failed"))

    def test_rejects_cursor_past_last_event(self):
        with self.assertRaisesRegex(feed_sync.FeedSyncError, "does not match"):
            self.run_sync([feed_response(feed_page([feed_event(1, self.scoped_event_id("first"))], next_cursor=2))])

        self.assertEqual(self.synced_event_ids(), [])
        self.assertEqual(self.cursor_and_last_status(), (0, "failed"))

    def test_page_size_is_capped_at_partner_limit(self):
        with self.assertRaisesRegex(ValueError, "page_size"):
            self.run_sync([], page_size=51)
        self.assertEqual(self.sync_run_rows(), [])


if __name__ == "__main__":
    unittest.main()
