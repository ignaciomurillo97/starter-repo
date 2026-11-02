# BigPMS Ledger Feed Sync Spec

## Scope

Implement TODO 2 in `app/main.py`: sync payment events from BigPMS's paginated feed into `resident_ledger`. The scheduler already invokes `sync_bigpms_ledger_events()` every `SYNC_INTERVAL_SECONDS` (30 seconds by default). Webhook registration/callback behavior and reconciliation are out of scope.

## Feed contract

Request pages from:

```text
GET {BIGPMS_API_URL}/api/v1/ledger-events?cursor=<seq>&limit=<n>
```

The cursor is the last durably processed `seq`; `limit` must be no greater than 50. A successful response has this shape:

```json
{
  "events": [
    {
      "seq": 21,
      "event_id": "evt-2001",
      "event_type": "payment.received",
      "resident_id": "res-101",
      "amount_cents": 150000,
      "method": "ach",
      "occurred_at": "2026-07-03T14:00:00Z"
    }
  ],
  "next_cursor": 21,
  "has_more": false
}
```

Treat `method` as informational. `seq`, `event_id`, `event_type`, `resident_id`, positive `amount_cents`, and timezone-aware `occurred_at` are required for an applicable payment.

## Paging and durable progress

- Read the starting cursor from `sync_state` for source `bigpms_ledger_events`; the seeded value is `0`.
- Fetch and process pages until `has_more` is false. Use `next_cursor` for the next request and reject a response that claims more pages without advancing the cursor, to avoid an infinite loop.
- For each page, apply new payment rows and advance `sync_state.cursor_seq` in the same database transaction. Never commit a cursor beyond events that have been durably applied or confirmed as already applied.
- A failure in a page transaction rolls back both its ledger writes and cursor update. If an earlier page in the same run committed, retain that progress; the next run resumes from that durable cursor.
- Payment rows use `entry_type='payment'`, the event's resident, positive amount, BigPMS event ID, and occurrence time. `property_id` is not present in feed events; use `prop-001` for this single-property dataset or resolve it through the resident's unit. Do not modify the supplied schema.
- The seed already contains events `evt-1001` through `evt-1012`; the initial feed includes them. Do not add them again. Events `evt-1013` through `evt-1020` are the expected initial backfill.

## Idempotency across ingestion paths

Feed pages may repeat events, and every webhook event also appears in the feed. A BigPMS event must create at most one payment row, including when a webhook and feed sync process it concurrently.

The schema has no unique constraint on `resident_ledger.bigpms_event_id`, so a transaction that only checks for an existing row and then inserts is not concurrency-safe. Use a transaction-safe, shared per-event claim or lock in both webhook and feed processing before checking/inserting the ledger row. Preserve the webhook audit semantics in `webhook_events`; do not treat a feed event as a received webhook merely to obtain a uniqueness constraint.

Count an already-applied event, including an event repeated within a page or previously applied by the webhook, as skipped. Advance the cursor past it once its presence in the ledger is confirmed.

## Retry and run audit

- The feed can return transient **500** responses and **429** responses with `Retry-After`. Retry transient HTTP/network failures with a bounded policy; for 429, wait at least the advertised `Retry-After` interval before retrying. Do not retry permanent client errors as if they were transient.
- If retries are exhausted, fail the run, preserve the last committed cursor, record the error, and let a later scheduled run resume. Do not silently report success.
- Record one `sync_runs` row for each invocation, with `source='bigpms_ledger_events'`, start/finish times, start/end cursor, counts, status, and error when failed. Track raw events fetched (including feed duplicates), newly applied events, and skipped duplicates separately.
- A process crash may leave its `sync_runs` row in `running`. The next invocation must start from the durable `sync_state.cursor_seq` and record a new run; it must not rely on or advance from the unfinished run's counters.
- Keep `sync_state.last_run_at` and `last_status` consistent with the final run result. Log the outcome and failure details.

```mermaid
sequenceDiagram
    participant S as Scheduled sync
    participant B as BigPMS feed
    participant D as PostgreSQL
    S->>D: Read durable cursor; create running sync_runs row
    loop While has_more
        S->>B: GET ledger-events?cursor=cursor&limit<=50
        alt Transient failure
            B-->>S: 429 + Retry-After or 500
            S->>S: Bounded retry; honor Retry-After
        else Page received
            B-->>S: events, next_cursor, has_more
            S->>D: BEGIN page transaction
            S->>D: Claim/lock each event; insert only if not already applied
            S->>D: Update cursor in same transaction
            S->>D: COMMIT
        end
    end
    S->>D: Mark sync_runs success and update sync_state
    Note over S,D: On page failure, rollback that page and record failed run; earlier committed pages remain durable
```

## Persistence provider

All SQL for payment ingestion lives in one provider, `LedgerRepository` (`app/ledger_repository.py`), shared by the webhook callback and the feed sync. Both paths therefore use the same per-event lock and existence check. `process_webhook` and `sync_bigpms_ledger_events` accept an optional `repository` argument, so a fake provider can be injected without parsing SQL.

## Testing approach

Tests in `tests/test_feed_sync.py` run against the **real PostgreSQL database**, not a mocked SQL layer. This is what I would do for local environments: it exercises the actual transaction, locking, constraint and rollback behavior that this spec depends on, and keeps the tests simple. Only the BigPMS HTTP calls are stubbed.

- Each test creates its own `sync_state` source and event-ID prefix, and removes its rows afterward. It never touches the live app's cursor, and no `docker-compose down -v` reset is needed between runs.
- Run them in the app container, which has the dependencies and `DATABASE_URL`:

```bash
docker compose run --rm --no-deps -v .:/workspace -w /workspace \
  -e PYTHONPATH=/workspace/app app python -m unittest tests.test_feed_sync -v
```

- If isolated, database-free unit tests are wanted later (for example in CI without Postgres), mock `LedgerRepository` instead of SQL.

## Out of scope: operational enhancements

- **Alerting:** notify operators about repeated failures, prolonged sync lag, or a cursor that stops advancing.
- **Adaptive scheduling:** dynamically tune polling frequency based on feed volume or partner health.

These are useful production capabilities, but are not required for this implementation.

## Acceptance checks

1. A run starts from the persisted cursor, pages with a limit of at most 50, and continues until the feed reports `has_more: false`.
2. The initial sync does not duplicate seeded payments `evt-1001`–`evt-1012` and applies missing events `evt-1013`–`evt-1020` exactly once.
3. Repeated feed events and events already applied by the webhook are skipped; concurrent webhook/feed processing cannot create two ledger rows for one event ID.
4. Each committed page's ledger writes and cursor update are atomic. A failed page leaves no partial writes or cursor advancement for that page; the next run resumes from the last durable cursor.
5. A 429 retry honors `Retry-After`; transient failures are bounded, and exhausted retries produce a failed run with an error rather than a success-shaped result.
6. Every invocation is recorded in `sync_runs` with accurate cursor range, fetched/applied/skipped counts, final status, finish time, and error when applicable.
