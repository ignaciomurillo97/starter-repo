# Reconciliation Report Spec

## Scope

Implement TODO 3 in `app/main.py`: `GET /api/v1/reconciliation`. Compare local payment totals with BigPMS and report the two planted ledger data-quality issues. Webhook processing and feed sync are out of scope.

## Report contract

- Return `generated_at` as a UTC ISO 8601 timestamp.
- Include every resident, with `resident_id`, `name`, local and BigPMS payment totals in cents, `delta_cents`, and `status`.
- Fetch authoritative totals with exactly one `GET {BIGPMS_API_URL}/api/v1/residents/summaries` request. Match results by resident ID; treat a resident omitted from BigPMS's response as having a zero total.
- Aggregate local payment totals in SQL, including residents with no local payments (zero). Use set-based queries, not Python loops over ledger rows.
- Define `delta_cents` as local total minus BigPMS total:
  - `in_sync`: delta is zero
  - `missing_local_payments`: delta is negative
  - `local_exceeds_bigpms`: delta is positive

Example:

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
      {
        "resident_id": "res-105",
        "charge_code": "rent",
        "amount_cents": 150000,
        "occurred_on": "2026-06-01",
        "count": 2
      }
    ],
    "orphaned_ledger_entries": [
      {
        "ledger_id": 42,
        "resident_id": "res-999",
        "entry_type": "charge",
        "amount_cents": 7500
      }
    ]
  },
  "summary": {
    "residents_out_of_sync": 1,
    "issues_found": 2
  }
}
```

## Data-quality checks

Detect both checks in SQL against `resident_ledger`:

- `duplicate_charges`: charge groups with the same resident, charge code, amount, and calendar date (`occurred_at`), where the count is greater than one. Return the fields shown above.
- `orphaned_ledger_entries`: every ledger row whose `resident_id` has no matching row in `residents`. Return `ledger_id`, `resident_id`, `entry_type`, and `amount_cents`.

`residents_out_of_sync` counts residents whose status is not `in_sync`. `issues_found` counts only data-quality results: the number of duplicate-charge groups plus orphaned ledger rows. A payment-total mismatch is not a data-quality issue.

If BigPMS or the database fails, return a non-2xx response; do not return a partial or success-shaped report.

## Expected seeded result

After feed sync has caught up and the system is quiescent, expect seven residents `in_sync`, `res-107` as `local_exceeds_bigpms`, and `issues_found: 2` (one duplicate charge group and one orphaned ledger row). A newly emitted BigPMS payment may temporarily make a resident appear as `missing_local_payments` until ingestion catches up.

## Acceptance checks

1. The endpoint returns one report covering all residents and makes one bulk BigPMS summaries request, not one request per resident.
2. Payment totals, deltas, and statuses agree, including zero totals and both mismatch directions.
3. SQL finds the duplicate charge group and orphaned ledger row; `issues_found` excludes reconciliation mismatches.
4. Upstream or database failures do not produce a successful or partial report.
