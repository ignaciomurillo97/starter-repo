# Local Database Entity Relationship Diagram

```mermaid
erDiagram
    PROPERTIES ||--o{ UNITS : contains
    UNITS ||--o{ RESIDENTS : houses
    RESIDENTS ||--o{ LEASES : holds
    UNITS ||--o{ LEASES : applies_to

    PROPERTIES {
        TEXT id PK
        TEXT name
    }

    UNITS {
        TEXT id PK
        TEXT property_id FK
        TEXT unit_number
    }

    RESIDENTS {
        TEXT id PK
        TEXT unit_id FK
        TEXT name
        TEXT email
    }

    LEASES {
        TEXT id PK
        TEXT resident_id FK
        TEXT unit_id FK
        INTEGER monthly_rent_cents
        DATE start_date
        DATE end_date
        TEXT status
    }

    RESIDENT_LEDGER {
        BIGSERIAL id PK
        TEXT property_id
        TEXT resident_id
        TEXT entry_type
        TEXT charge_code
        INTEGER amount_cents
        TEXT bigpms_event_id
        TIMESTAMPTZ occurred_at
        TIMESTAMPTZ created_at
    }

    WEBHOOK_EVENTS {
        TEXT event_id PK
        TEXT event_type
        JSONB payload
        TEXT status
        TEXT error
        TIMESTAMPTZ received_at
        TIMESTAMPTZ processed_at
    }

    SYNC_STATE {
        TEXT source PK
        BIGINT cursor_seq
        TIMESTAMPTZ last_run_at
        TEXT last_status
    }

    SYNC_RUNS {
        BIGSERIAL id PK
        TEXT source
        TIMESTAMPTZ started_at
        TIMESTAMPTZ finished_at
        BIGINT cursor_start
        BIGINT cursor_end
        INTEGER events_fetched
        INTEGER events_applied
        INTEGER events_skipped
        TEXT status
        TEXT error
    }
```

Only declared foreign keys are shown as relationships. `resident_ledger` intentionally has no foreign keys, so orphaned entries are representable; its `property_id` and `resident_id` are not constrained to other tables. The webhook and sync audit/state tables are also standalone in the provided schema.

## Entities and likely uses

| Entity | Likely use |
|---|---|
| `properties` | Represents a managed property and anchors its units. |
| `units` | Represents rentable units within a property; residents and leases reference units. |
| `residents` | Stores resident identity and contact details, associated with a unit. |
| `leases` | Stores a resident's lease term, status, and monthly rent amount. |
| `resident_ledger` | Local financial history of charges and payments; supports balances, payment reconciliation, and data-quality checks. |
| `webhook_events` | Audit trail for every inbound BigPMS webhook, including processed, duplicate, or failed deliveries. |
| `sync_state` | Stores the feed sync cursor and latest run status so scheduled sync can resume from durable progress. |
| `sync_runs` | Records each feed sync attempt, its cursor range, event counts, status, and any error. |
