-- LeaseFlow local schema for the Integration Engineer take-home.
--
-- This is YOUR side of the integration: LeaseFlow's local copy of resident
-- and ledger data, kept in sync from BigPMS (the system of record).
--
-- DO NOT modify this schema. Everything you need is here.

CREATE TABLE properties (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL
);

CREATE TABLE units (
    id          TEXT PRIMARY KEY,
    property_id TEXT NOT NULL REFERENCES properties(id),
    unit_number TEXT NOT NULL
);

CREATE TABLE residents (
    id      TEXT PRIMARY KEY,
    unit_id TEXT NOT NULL REFERENCES units(id),
    name    TEXT NOT NULL,
    email   TEXT
);

CREATE TABLE leases (
    id                 TEXT PRIMARY KEY,
    resident_id        TEXT NOT NULL REFERENCES residents(id),
    unit_id            TEXT NOT NULL REFERENCES units(id),
    monthly_rent_cents INTEGER NOT NULL,
    start_date         DATE NOT NULL,
    end_date           DATE NOT NULL,
    status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended'))
);

-- LeaseFlow's local copy of the resident ledger: charges (rent, fees) and
-- payments. Balance for a resident = SUM(charges) - SUM(payments).
-- BigPMS is the system of record for payments; this copy exists so LeaseFlow can
-- make leasing/renewal decisions without calling BigPMS on every page load.
--
-- NOTE: resident_id intentionally has NO foreign key. Ledger data has been
-- imported from more than one upstream system over time and, as in real
-- life, is not perfectly clean.
CREATE TABLE resident_ledger (
    id            BIGSERIAL PRIMARY KEY,
    property_id   TEXT NOT NULL,
    resident_id   TEXT NOT NULL,
    entry_type    TEXT NOT NULL CHECK (entry_type IN ('charge', 'payment')),
    charge_code   TEXT,                -- e.g. 'rent', 'late_fee' (charges only)
    amount_cents  INTEGER NOT NULL CHECK (amount_cents > 0),
    bigpms_event_id  TEXT,                -- BigPMS event id, when sourced from BigPMS
    occurred_at   TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX resident_ledger_resident_idx ON resident_ledger (property_id, resident_id);

-- Audit trail for inbound webhooks. Every received webhook should be recorded
-- here, whatever its fate.
CREATE TABLE webhook_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    payload      JSONB NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('processed', 'duplicate', 'failed')),
    error        TEXT,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

-- Cursor state for the scheduled feed sync.
CREATE TABLE sync_state (
    source       TEXT PRIMARY KEY,
    cursor_seq   BIGINT NOT NULL DEFAULT 0,
    last_run_at  TIMESTAMPTZ,
    last_status  TEXT
);

INSERT INTO sync_state (source, cursor_seq) VALUES ('bigpms_ledger_events', 0);

-- Audit trail for sync runs. Record one row per run of your scheduled job.
CREATE TABLE sync_runs (
    id             BIGSERIAL PRIMARY KEY,
    source         TEXT NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    cursor_start   BIGINT,
    cursor_end     BIGINT,
    events_fetched INTEGER,
    events_applied INTEGER,
    events_skipped INTEGER,
    status         TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'failed')),
    error          TEXT
);
