# LeaseFlow ↔ BigPMS Integration — Starter Repo

This is the starter repo for the Senior Integration Engineer take-home.

## Start Here

1. **Read [docs/SPEC.md](docs/SPEC.md)** — the full project brief. Everything you're asked to build is defined there.
2. **Boot the repo** (Quick Start below) and verify everything runs.
3. **Build.** Your workspace is `app/main.py` — it has three stubbed TODOs matching the SPEC's requirements:

| # | Task | Where | SPEC section |
|---|------|-------|--------------|
| 1 | Webhook subscription + callback endpoint | `register_webhook_subscription()` / `POST /webhooks/bigpms` | §1 |
| 2 | Scheduled feed sync | `sync_bigpms_ledger_events()` (scheduler already calls it) | §2 |
| 3 | Reconciliation report | `GET /api/v1/reconciliation` | §3 |
| 4 | Write-up (time spent, decisions, AI usage) | your README | §4 |

**Ground rules:** 2-hour cap (stop even if unfinished — log your time). AI tools expected — you'll defend every decision in the review. Don't modify the DB schema or the mock BigPMS service.

## Quick Start

Be sure that you have Docker installed locally. 

```bash
docker-compose up --build
```

This starts three services:

| Service      | URL                   | Description                                      |
|--------------|-----------------------|--------------------------------------------------|
| **app**      | http://localhost:8000 | LeaseFlow integration service (your workspace)   |
| **db**       | localhost:5432        | PostgreSQL (user/pass/db: leaseflow)             |
| **mock-bigpms** | http://localhost:3001 | Mock BigPMS — the partner platform (do not modify)  |

## Verify It Works

1. **Your service:** http://localhost:8000/api/health → `{"status": "ok"}`
2. **Residents:** http://localhost:8000/api/v1/residents → 8 seeded residents
3. **Mock BigPMS:** http://localhost:3001/health → `{"status": "ok", "webhook_subscriptions": 0}`
4. **BigPMS's feed:** http://localhost:3001/api/v1/ledger-events?cursor=0&limit=5 (retry if you hit a simulated error — that's intentional)

## Mock BigPMS API (the partner surface you integrate with)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/webhook-subscriptions` | Register `{"callback_url", "secret"}` to start receiving webhooks. Idempotent per callback_url. |
| `GET /api/v1/webhook-subscriptions` | List registered subscriptions |
| `GET /api/v1/ledger-events?cursor=&limit=` | Paginated payment events feed (cursor = last seen `seq`, limit ≤ 50) |
| `GET /api/v1/residents/summaries` | Bulk authoritative payment totals per resident |
| `GET /api/v1/residents/{id}/summary` | Single-resident totals |
| `GET /health` | Health + subscription count |

**Expected partner behavior (deliberate — do not "fix"):**

- The feed randomly returns **500** (~10%) and **429 with `Retry-After`** (~10%)
- Feed pages occasionally contain a **duplicate event**
- Webhooks are sometimes **delivered twice**
- Some webhooks arrive with an **invalid signature** (reject them)
- Events delivered by webhook **also appear in the feed**
- BigPMS emits a new payment event every ~15s **once you've registered a subscription**

Signature scheme: `X-BigPMS-Signature` header = hex `HMAC-SHA256(secret, raw_request_body)`.

## Environment Variables (pre-configured in docker-compose.yml)

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | `postgres://leaseflow:leaseflow@db:5432/leaseflow` |
| `BIGPMS_API_URL` | `http://mock-bigpms:3001` |
| `CALLBACK_URL` | `http://app:8000/webhooks/bigpms` (what you register with BigPMS) |
| `WEBHOOK_SECRET` | `leaseflow-dev-secret` |
| `SYNC_INTERVAL_SECONDS` | `30` |

## What's Provided

- **`db/init.sql`** — full schema: residents, leases, `resident_ledger`, `webhook_events`, `sync_state`, `sync_runs`. **Do not modify.**
- **`db/seed.sql`** — 8 residents, three months of rent charges, payment history through BigPMS event `evt-1012`. Contains planted data-quality issues (see SPEC).
- **`app/main.py`** — FastAPI scaffold: health check works, scheduler is wired to call `sync_bigpms_ledger_events()` every 30s, webhook/reconciliation endpoints are stubbed with TODOs.
- **`app/db.py`** — connection pool, ready to use.
- **`mock-bigpms/`** — the mock BigPMS service (runs as `mock-bigpms`). Black box.

## Useful Commands

```bash
# Watch the partner's behavior (webhook sends, simulated failures)
docker-compose logs -f mock-bigpms

# Watch your own service
docker-compose logs -f app

# Connect to your database
docker-compose exec db psql -U leaseflow -d leaseflow

# Tear down and reset all data
docker-compose down -v && docker-compose up --build
```

## Tips

- The app volume-mounts `./app`, and uvicorn runs with `--reload` — edits apply without rebuilding.
- `docker-compose down -v` resets you to the seeded state. Useful for testing your sync from scratch.
- Watch `mock-bigpms` logs while you work — it announces duplicates, bad signatures, and simulated failures as it produces them.
- If something is ambiguous, make a decision and document it in your README.

## Work Log and Write-Up

**Time spent:** about 96 minutes (24 minutes remained in the 2-hour cap)

| Step | Status | Time spent | Notes / outcome |
|---|---|---:|---|
| Read the spec and prepare feature notes | Done | ~25m | Used AI to generate diagrams, summarize the codebase, and draft feature specs. |
| Webhook subscription and callback | Done | ~15m | Implemented from the spec; reviewed the AI-generated code, ran tests, and corrected issues found. |
| Scheduled feed sync and backfill | Done | ~20m | Implemented from the spec; reviewed and validated the AI-generated code, then fixed issues found. |
| Reconciliation report | Spec drafted; implementation deferred | ~2m | Wrote a spec for the report but did not implement it, leaving time for documentation and to avoid ending with incomplete code. |
| Bonus work (if any) | Not attempted | 0m | Deferred to stay within the time cap. |
| README write-up and final documentation | In progress | ~34m estimated | Estimated remainder based on the approximate step times above and the 96-minute elapsed-time note. |

### Decisions and Tradeoffs

#### Property ID Derivation

- **Decision:** Used harcoded instead of fetching from the database.
- **Alternatives considered:** Fetch the property ID from the database.
- **Why / tradeoff:** Given the docs allude to multiproperties as a future possibility, I felt the property ID might require a more complex derivation. As I was still working on the current features I opted for the simplest method that would get the prioritized work done to then discuss the ideal implementation here.

#### Idempotency and Cross-Channel Deduplication

- **Decision:** implemented an advisory lock such that the callbacks and feed sync can't update the same payment simultaneously. Though I later realized this isn't the optimal decision
- **Alternatives considered:** Unique constraint.
- **Why / tradeoff:** I initially decided for an advisory lock such that only one channel could perform write operations and given the callback had to update 2 places, I believed a lock was the only option. However, the event table should update anyway and if the insert to the ledger table succeeded or had a conflict it would not matter. So the unique ID would have lower complexity in that regard and I believe it would be marginally faster as it avoids blocking.

#### Feed Retries and Cursor Durability

- **Decision:** 
- **Alternatives considered:**
- **Why / tradeoff:**

#### Reconciliation Queries and Other Choices

- **Decision:**
- **Alternatives considered:**
- **Why / tradeoff:**

### What I'd Do Next

- [With more time, list the next improvements. Describe out-of-scope items rather than building them.]
- take more time planning how these features interact with each other and create appropiate work units to match instead of doing 1:1 with TODOs
- leverage git worktrees to implement changes in parallel. 

### AI Usage

- **AI-generated:** Diagrams, a codebase summary, feature specs, and implementation code for the webhook and scheduled feed sync.
- **Kept as-is:** Generated code that passed review, tests, and validation against the spec.
- **Corrected or changed:** Reviewed and tested the generated implementations, then corrected issues found during review and validation.

Good luck.
