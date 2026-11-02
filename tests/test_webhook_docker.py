"""Integration tests for the running Docker services.

Run from the repository root with: python tests/test_webhook_docker.py
"""
import hashlib
import hmac
import json
import os
import subprocess
import unittest
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_URL = os.environ.get("LEASEFLOW_URL", "http://localhost:8000")
BIGPMS_URL = os.environ.get("BIGPMS_URL", "http://localhost:3001")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "leaseflow-dev-secret")
CALLBACK_URL = os.environ.get(
    "CALLBACK_URL", "http://app:8000/webhooks/bigpms"
)


class WebhookDockerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.event_ids = []

    def tearDown(self):
        for event_id in self.event_ids:
            self.psql(
                "DELETE FROM resident_ledger "
                f"WHERE bigpms_event_id = '{event_id}'; "
                f"DELETE FROM webhook_events WHERE event_id = '{event_id}';"
            )

    def psql(self, query):
        return subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "leaseflow",
                "-d",
                "leaseflow",
                "-At",
                "-c",
                query,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def count_rows(self, table, event_id, column):
        return int(
            self.psql(
                f"SELECT count(*) FROM {table} WHERE {column} = '{event_id}'"
            )
        )

    def create_event(self, amount_cents=12345):
        event_id = f"it-{uuid.uuid4()}"
        self.event_ids.append(event_id)
        event = {
            "event_id": event_id,
            "event_type": "payment.received",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "data": {
                "resident_id": "res-101",
                "amount_cents": amount_cents,
                "method": "ach",
                "seq": 999999,
            },
        }
        raw_body = json.dumps(event, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(
            WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        return event_id, raw_body, signature

    def post_webhook(self, raw_body, signature=None):
        headers = {"Content-Type": "application/json"}
        if signature is not None:
            headers["X-BigPMS-Signature"] = signature
        request = urllib.request.Request(
            f"{APP_URL}/webhooks/bigpms",
            data=raw_body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = error.read()
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                body = body.decode("utf-8", errors="replace")
            return error.code, body

    def test_startup_registered_callback(self):
        with urllib.request.urlopen(
            f"{BIGPMS_URL}/api/v1/webhook-subscriptions", timeout=10
        ) as response:
            subscriptions = json.loads(response.read())["subscriptions"]
        matching = [
            item for item in subscriptions if item["callback_url"] == CALLBACK_URL
        ]
        self.assertEqual(
            len(matching),
            1,
            f"Callback {CALLBACK_URL} was not registered: {subscriptions}",
        )

    def test_valid_event_and_redelivery_apply_payment_once(self):
        event_id, raw_body, signature = self.create_event()

        first_status, first_body = self.post_webhook(raw_body, signature)
        second_status, second_body = self.post_webhook(raw_body, signature)

        self.assertEqual(first_status, 200)
        self.assertEqual(first_body["status"], "processed")
        self.assertEqual(second_status, 200)
        self.assertEqual(second_body["status"], "duplicate")
        self.assertEqual(
            self.count_rows("resident_ledger", event_id, "bigpms_event_id"), 1
        )
        self.assertEqual(
            self.psql(
                f"SELECT status FROM webhook_events WHERE event_id = '{event_id}'"
            ),
            "duplicate",
        )

    def test_database_failure_rolls_back_audit_and_ledger(self):
        event_id, raw_body, signature = self.create_event(amount_cents=2**31)

        status, _ = self.post_webhook(raw_body, signature)

        self.assertEqual(status, 500)
        self.assertEqual(
            self.count_rows("webhook_events", event_id, "event_id"), 0
        )
        self.assertEqual(
            self.count_rows("resident_ledger", event_id, "bigpms_event_id"), 0
        )

    def test_invalid_signature_is_rejected_without_database_writes(self):
        event_id, raw_body, _ = self.create_event()

        status, body = self.post_webhook(raw_body, "0" * 64)

        self.assertEqual(status, 401, body)
        self.assertEqual(
            self.count_rows("webhook_events", event_id, "event_id"), 0
        )
        self.assertEqual(
            self.count_rows("resident_ledger", event_id, "bigpms_event_id"), 0
        )

    def test_missing_and_malformed_signatures_are_rejected(self):
        for signature in (None, "a" * 62, "a" * 66):
            with self.subTest(signature_length=None if signature is None else len(signature)):
                event_id, raw_body, _ = self.create_event()

                status, _ = self.post_webhook(raw_body, signature)

                self.assertEqual(status, 401)
                self.assertEqual(
                    self.count_rows("webhook_events", event_id, "event_id"), 0
                )
                self.assertEqual(
                    self.count_rows("resident_ledger", event_id, "bigpms_event_id"),
                    0,
                )

    def test_invalid_signed_payload_is_rejected_without_database_writes(self):
        for amount_cents in (True, 0):
            with self.subTest(amount_cents=amount_cents):
                event_id, raw_body, signature = self.create_event(
                    amount_cents=amount_cents
                )

                status, _ = self.post_webhook(raw_body, signature)

                self.assertEqual(status, 400)
                self.assertEqual(
                    self.count_rows("webhook_events", event_id, "event_id"), 0
                )
                self.assertEqual(
                    self.count_rows("resident_ledger", event_id, "bigpms_event_id"),
                    0,
                )

    def test_concurrent_redeliveries_apply_payment_once(self):
        event_id, raw_body, signature = self.create_event()

        with ThreadPoolExecutor(max_workers=5) as executor:
            responses = list(
                executor.map(
                    lambda _: self.post_webhook(raw_body, signature), range(5)
                )
            )

        self.assertTrue(all(status == 200 for status, _ in responses), responses)
        self.assertEqual(
            self.count_rows("resident_ledger", event_id, "bigpms_event_id"), 1
        )
        self.assertEqual(
            self.psql(
                f"SELECT status FROM webhook_events WHERE event_id = '{event_id}'"
            ),
            "duplicate",
        )


if __name__ == "__main__":
    unittest.main()
