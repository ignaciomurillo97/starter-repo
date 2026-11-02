# Senior Integration Engineer Take-Home Project

## Payment Sync & Ledger Reconciliation

**Time Limit:** 2 hours (please stop at 2 hours even if unfinished, and log the time you actually spent in your README)
**Deliverable:** A working integration service, built on the provided starter repo, that boots with `docker-compose up` and actually works
**AI tools:** You are expected to use AI coding tools (Claude Code, Cursor, Copilot, etc.). You must be able to explain and defend every decision in the resulting code — that's the follow-up interview.

---

## Context

You're an integration engineer at **LeaseFlow**, a leasing platform for large multifamily operators. LeaseFlow needs resident payment data from **BigPMS** — the property management system of record — to power leasing and renewal decisions. You can't decide whether to renew a resident without knowing whether they actually pay rent.

LeaseFlow keeps a **local copy of the resident ledger** so its product features don't call BigPMS on every page load. Your job is to keep that local copy accurate.

BigPMS is a partner platform: you consume its APIs and webhooks, but you don't control it. Like every real partner system, it's imperfect — it rate-limits, it fails intermittently, and it sometimes delivers the same event twice. **Do not modify the mock BigPMS service.** Treat it like a partner you can't fix.

**The business stakes:** if LeaseFlow's ledger copy drifts from BigPMS's, operators make renewal decisions on wrong balances — chasing residents who already paid, or renewing residents who are months delinquent. Money data that's *almost* right is worse than no data.

---

## What We Provide

The starter repo boots with one command (`docker-compose up`) and includes:

- **Your service** (`app/`): FastAPI scaffold with a working health check, DB connection pool, and a background scheduler already wired to call your sync function every 30 seconds
- **Your database**: PostgreSQL, fully seeded — residents, leases, rent charges, and partial payment history. Every table you need exists. Don't alter or drop the provided tables or columns — but you **may** add your own indexes, constraints, or migrations (run from your app) if your design calls for them.
- **Mock BigPMS** (`mock-bigpms` service): the partner black box. Webhook subscription API, a paginated ledger-events feed, and per-resident payment totals.

See the README for endpoints, environment variables, and useful commands.

---

## Requirements

### 1. Webhook Subscription & Processing

BigPMS pushes `payment.received` events to registered subscribers. It won't send anything until you register.

**a) Register a subscription** on your app's startup (idempotent — safe to repeat):

```
POST {BIGPMS_API_URL}/api/v1/webhook-subscriptions
{"callback_url": "<CALLBACK_URL env var>", "secret": "<WEBHOOK_SECRET env var>"}
```

**b) Implement the callback endpoint** `POST /webhooks/bigpms`. For each incoming event:

- **Verify the signature.** The `X-BigPMS-Signature` header is a hex HMAC-SHA256 of the raw request body, keyed with the shared secret. Reject invalid signatures with a **401** — BigPMS deliberately sends some tampered payloads.
- **Apply the payment atomically.** Record the event in `webhook_events` AND insert the payment into `resident_ledger` in a **single transaction**. If either write fails, neither happens.
- **Handle redelivery.** BigPMS delivers ~25% of events twice. A duplicate must not double-post the payment — and must still get a **2xx** response (otherwise BigPMS keeps retrying forever). Record duplicates in `webhook_events` with status `duplicate`.

Webhook payload:

```json
{
  "event_id": "evt-2001",
  "event_type": "payment.received",
  "occurred_at": "2026-07-07T14:30:00Z",
  "data": {
    "resident_id": "res-101",
    "amount_cents": 150000,
    "method": "ach",
    "seq": 21
  }
}
```

> **Note — `property_id`:** payment events (both webhook and feed) do not carry a property, but `resident_ledger.property_id` is `NOT NULL`. Derive it. This dataset has a single property (`prop-001`); hardcoding it or resolving it via `residents → units → property_id` are both acceptable — document your choice. Multi-property is out of scope.

### 2. Scheduled Feed Sync

Webhooks miss things — deliveries fail, services restart, and LeaseFlow's local history predates the subscription. Implement `sync_bigpms_ledger_events()` (the scheduler already calls it every 30 seconds) to reconcile against BigPMS's feed:

```
GET {BIGPMS_API_URL}/api/v1/ledger-events?cursor=<seq>&limit=<n>
→ {"events": [...], "next_cursor": 20, "has_more": false}
```

Requirements:

- **Cursor-based paging.** Start from the cursor stored in `sync_state` (seeded at 0) and page through everything new. Apply events to `resident_ledger` idempotently.
- **Survive partner flakiness.** The feed randomly returns 500s and 429s (with a `Retry-After` header — honor it). Retry sensibly. A failed run must leave the database consistent and the next run must pick up where the last durable progress ended.
- **Advance the cursor transactionally.** The cursor must never move past events that weren't durably applied. A crash mid-batch must not lose or double-apply anything.
- **Record each run** in `sync_runs`: cursor range, events fetched/applied/skipped, status, error if any.

**Heads up:** events you already received via webhook *also appear in this feed* (and pages occasionally contain duplicates). Your ledger must count each payment exactly once, no matter how many times or through how many channels it arrives — and that has to hold under retries and concurrent delivery, not just on the happy path. How you guarantee it is a design decision.

When this works, the eight residents whose payment history was incomplete in the seed data (`evt-1013`–`evt-1020`) get backfilled automatically.

### 3. Reconciliation Report

Trust, but verify. Implement `GET /api/v1/reconciliation` returning a JSON report that proves (or disproves) that LeaseFlow's local ledger agrees with BigPMS:

- **Per resident:** local payment total (SQL over `resident_ledger`), BigPMS's authoritative total (one call to `GET /api/v1/residents/summaries` — don't call the per-resident endpoint N times), the delta, and a status:
  - `in_sync` — totals match
  - `missing_local_payments` — BigPMS has payments you don't
  - `local_exceeds_bigpms` — you have payments BigPMS doesn't know about
- **Data-quality issues**, detected in SQL against your local ledger:
  - `duplicate_charges`: same resident, same charge code, same amount, same date, more than once
  - `orphaned_ledger_entries`: ledger rows whose `resident_id` doesn't exist in `residents`

Suggested shape (adapt as long as it's clear and complete):

```json
{
  "generated_at": "2026-07-07T15:00:00Z",
  "residents": [
    {
      "resident_id": "res-101",
      "name": "Jane Doe",
      "local_payments_cents": 450000,
      "bigpms_payments_cents": 450000,
      "delta_cents": 0,
      "status": "in_sync"
    }
  ],
  "issues": {
    "duplicate_charges": [
      {"resident_id": "res-105", "charge_code": "rent", "amount_cents": 150000, "occurred_on": "2026-06-01", "count": 2}
    ],
    "orphaned_ledger_entries": [
      {"ledger_id": 42, "resident_id": "res-999", "entry_type": "charge", "amount_cents": 7500}
    ]
  },
  "summary": {"residents_out_of_sync": 1, "issues_found": 2}
}
```

> `issues_found` counts **data-quality issues only** — `len(duplicate_charges) + len(orphaned_ledger_entries)`. It is separate from `residents_out_of_sync`, which is a reconciliation status, not a data-quality issue.

The seed data contains **three planted issues**:

1. A **duplicate charge** (res-105 double-billed June rent) → `duplicate_charges`.
2. An **orphaned ledger entry** (res-999 doesn't exist in `residents`) → `orphaned_ledger_entries`.
3. A **local-only payment** (res-107, `evt-9999`) that BigPMS has no record of → surfaces as res-107 `local_exceeds_bigpms` in the `residents` array.

So once your sync works, a correct report shows **7 residents `in_sync`, res-107 `local_exceeds_bigpms`**, and `issues_found: 2` (the duplicate charge and the orphan; both are charges, so they don't move payment totals). Before your sync works, all eight residents show `missing_local_payments` — watching them converge is how you'll know your sync is correct.

> **Reconciliation is point-in-time.** BigPMS emits a new payment every ~15s once you're subscribed, and it lands in BigPMS's totals before your webhook/feed has applied it locally. A resident can briefly show `missing_local_payments` in the seconds between a new event and your service catching up — that transient delta is expected, not a bug. Judge correctness at a quiescent moment.

Do the aggregation in SQL. Set-based queries, not Python loops over rows.

### 4. README Write-Up

Include in your README (brief — bullet points are fine):

- **Time spent** — honestly. Stopping at 2 hours with unfinished work beats a suspiciously polished 6-hour submission.
- **Decisions and tradeoffs** — what you chose, what you considered, why. The *why* matters more than the *what*.
- **What you'd do next** — with more time. (This is where retry queues, DLQ, monitoring, and alerting belong: described, not built.)
- **AI usage** — which parts were AI-generated, what you kept as-is, what you corrected and why. You'll walk through this code with our engineers; "the AI wrote it" is not an explanation we accept, from candidates or from ourselves.

---

## Bonus (only if you have time left within the cap)

- **Diagnosable logging.** We will stop the mock BigPMS mid-run, wait a minute, restart it, and read your logs. Can we tell what happened — retries, failures, recovery — without guessing? Log like the person on call is you.
- **Sync status endpoint.** `GET /api/v1/sync/status`: current cursor, last run outcome, webhook counts by status. The thing you'd check first when someone says "the numbers look wrong."

---

## What NOT To Do

- **Don't modify** the mock BigPMS service, or alter/drop the provided tables and columns. (Adding your own indexes, constraints, or migrations is fair game — see "What You're Given.")
- **Don't build a frontend.** There isn't one.
- **Don't implement** authentication, a dead-letter queue, a message broker, or multi-property support. Describe them in the README if relevant; don't build them.
- **Don't make the mock BigPMS less flaky.** Handling its behavior *is* the test.
- **Don't leave dead code** or half-finished experiments.

---

## Evaluation

What we weigh (full rubric shared during the review):

- **Transaction semantics & idempotency (30%)** — money is counted exactly once, whatever the partner throws at you. Atomic writes, cross-channel dedupe, cursor correctness.
- **Webhook subscription & handling (20%)** — registration works, signatures verified, tampered payloads rejected, duplicates acknowledged but not double-applied.
- **Feed sync resilience (20%)** — paging, retries, `Retry-After` honored, durable progress, `sync_runs` audit trail.
- **Reconciliation & SQL (20%)** — set-based SQL, all planted issues found, correct deltas with direction.
- **Communication (10%)** — README decisions, honest time log, AI usage notes.
- **Bonus (up to +10%)** — diagnosable logging, sync status endpoint.

---

## Recommended Order

1. Read the README, skim the schema and seed data, poke the mock BigPMS endpoints (~15 min)
2. Webhook subscription + callback endpoint (~30 min)
3. Scheduled feed sync (~40 min)
4. Reconciliation report (~20 min)
5. README write-up (~15 min)
6. Bonus (remaining time, if any)

---

## Submission

Submit a **Git repository** (public or private, shared with us). It must boot with `docker-compose up` and work: we should be able to watch webhooks land, see the backfill converge, and pull a clean reconciliation report within a few minutes of starting it.

If something is ambiguous, **make a decision and document it in your README**. That's part of the test — this role is full of ambiguity, and we want to see how you handle it.

Good luck.
