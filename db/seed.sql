-- Seed data for the Integration Engineer take-home.
--
-- One property, 8 residents. Rent charges exist for April, May, and June 2026.
-- Payments through BigPMS event evt-1012 were synced by a previous import;
-- later BigPMS payments have NOT been synced yet — that's your sync job's work.
--
-- The data also contains a few quality issues, planted deliberately. Your
-- reconciliation report should surface them. Do not "fix" the seed.

INSERT INTO properties (id, name) VALUES
    ('prop-001', 'Park Meadows Apartments');

INSERT INTO units (id, property_id, unit_number) VALUES
    ('unit-101', 'prop-001', '101'),
    ('unit-102', 'prop-001', '102'),
    ('unit-103', 'prop-001', '103'),
    ('unit-104', 'prop-001', '104'),
    ('unit-105', 'prop-001', '105'),
    ('unit-106', 'prop-001', '106'),
    ('unit-107', 'prop-001', '107'),
    ('unit-108', 'prop-001', '108');

INSERT INTO residents (id, unit_id, name, email) VALUES
    ('res-101', 'unit-101', 'Jane Doe',        'jane@example.com'),
    ('res-102', 'unit-102', 'Marcus Chen',     'marcus@example.com'),
    ('res-103', 'unit-103', 'Sarah Kim',       'sarah@example.com'),
    ('res-104', 'unit-104', 'David Rodriguez', 'david@example.com'),
    ('res-105', 'unit-105', 'Priya Patel',     'priya@example.com'),
    ('res-106', 'unit-106', 'Tom Baker',       'tom@example.com'),
    ('res-107', 'unit-107', 'Lisa Tran',       'lisa@example.com'),
    ('res-108', 'unit-108', 'Carlos Mendez',   'carlos@example.com');

INSERT INTO leases (id, resident_id, unit_id, monthly_rent_cents, start_date, end_date, status) VALUES
    ('lease-101', 'res-101', 'unit-101', 150000, '2025-07-01', '2026-06-30', 'active'),
    ('lease-102', 'res-102', 'unit-102', 145000, '2025-09-01', '2026-08-31', 'active'),
    ('lease-103', 'res-103', 'unit-103', 160000, '2025-10-01', '2026-09-30', 'active'),
    ('lease-104', 'res-104', 'unit-104', 172500, '2025-11-01', '2026-10-31', 'active'),
    ('lease-105', 'res-105', 'unit-105', 150000, '2026-01-01', '2026-12-31', 'active'),
    ('lease-106', 'res-106', 'unit-106', 155000, '2026-02-01', '2027-01-31', 'active'),
    ('lease-107', 'res-107', 'unit-107', 140000, '2026-03-01', '2027-02-28', 'active'),
    ('lease-108', 'res-108', 'unit-108', 165000, '2026-04-01', '2027-03-31', 'active');

-- ---------------------------------------------------------------------------
-- Charges: monthly rent for April, May, June 2026
-- ---------------------------------------------------------------------------
INSERT INTO resident_ledger (property_id, resident_id, entry_type, charge_code, amount_cents, occurred_at) VALUES
    -- April
    ('prop-001', 'res-101', 'charge', 'rent', 150000, '2026-04-01T00:00:00Z'),
    ('prop-001', 'res-102', 'charge', 'rent', 145000, '2026-04-01T00:00:00Z'),
    ('prop-001', 'res-103', 'charge', 'rent', 160000, '2026-04-01T00:00:00Z'),
    ('prop-001', 'res-104', 'charge', 'rent', 172500, '2026-04-01T00:00:00Z'),
    ('prop-001', 'res-105', 'charge', 'rent', 150000, '2026-04-01T00:00:00Z'),
    ('prop-001', 'res-106', 'charge', 'rent', 155000, '2026-04-01T00:00:00Z'),
    ('prop-001', 'res-107', 'charge', 'rent', 140000, '2026-04-01T00:00:00Z'),
    ('prop-001', 'res-108', 'charge', 'rent', 165000, '2026-04-01T00:00:00Z'),
    -- May
    ('prop-001', 'res-101', 'charge', 'rent', 150000, '2026-05-01T00:00:00Z'),
    ('prop-001', 'res-102', 'charge', 'rent', 145000, '2026-05-01T00:00:00Z'),
    ('prop-001', 'res-103', 'charge', 'rent', 160000, '2026-05-01T00:00:00Z'),
    ('prop-001', 'res-104', 'charge', 'rent', 172500, '2026-05-01T00:00:00Z'),
    ('prop-001', 'res-105', 'charge', 'rent', 150000, '2026-05-01T00:00:00Z'),
    ('prop-001', 'res-106', 'charge', 'rent', 155000, '2026-05-01T00:00:00Z'),
    ('prop-001', 'res-107', 'charge', 'rent', 140000, '2026-05-01T00:00:00Z'),
    ('prop-001', 'res-108', 'charge', 'rent', 165000, '2026-05-01T00:00:00Z'),
    -- June
    ('prop-001', 'res-101', 'charge', 'rent', 150000, '2026-06-01T00:00:00Z'),
    ('prop-001', 'res-102', 'charge', 'rent', 145000, '2026-06-01T00:00:00Z'),
    ('prop-001', 'res-103', 'charge', 'rent', 160000, '2026-06-01T00:00:00Z'),
    ('prop-001', 'res-104', 'charge', 'rent', 172500, '2026-06-01T00:00:00Z'),
    ('prop-001', 'res-105', 'charge', 'rent', 150000, '2026-06-01T00:00:00Z'),
    ('prop-001', 'res-106', 'charge', 'rent', 155000, '2026-06-01T00:00:00Z'),
    ('prop-001', 'res-107', 'charge', 'rent', 140000, '2026-06-01T00:00:00Z'),
    ('prop-001', 'res-108', 'charge', 'rent', 165000, '2026-06-01T00:00:00Z');

-- ---------------------------------------------------------------------------
-- Payments already imported from BigPMS (events evt-1001..evt-1012)
-- ---------------------------------------------------------------------------
INSERT INTO resident_ledger (property_id, resident_id, entry_type, amount_cents, bigpms_event_id, occurred_at) VALUES
    ('prop-001', 'res-101', 'payment', 150000, 'evt-1001', '2026-04-03T14:00:00Z'),
    ('prop-001', 'res-102', 'payment', 145000, 'evt-1002', '2026-04-04T09:30:00Z'),
    ('prop-001', 'res-103', 'payment', 160000, 'evt-1003', '2026-04-02T16:15:00Z'),
    ('prop-001', 'res-104', 'payment', 172500, 'evt-1004', '2026-04-05T11:00:00Z'),
    ('prop-001', 'res-105', 'payment', 150000, 'evt-1005', '2026-04-03T10:45:00Z'),
    ('prop-001', 'res-106', 'payment', 155000, 'evt-1006', '2026-04-06T13:20:00Z'),
    ('prop-001', 'res-107', 'payment', 140000, 'evt-1007', '2026-04-04T08:00:00Z'),
    ('prop-001', 'res-108', 'payment', 165000, 'evt-1008', '2026-04-05T17:40:00Z'),
    ('prop-001', 'res-101', 'payment', 150000, 'evt-1009', '2026-05-03T14:10:00Z'),
    ('prop-001', 'res-102', 'payment', 145000, 'evt-1010', '2026-05-04T09:00:00Z'),
    ('prop-001', 'res-103', 'payment', 160000, 'evt-1011', '2026-05-03T15:30:00Z'),
    ('prop-001', 'res-104', 'payment', 172500, 'evt-1012', '2026-05-05T12:00:00Z');

-- ---------------------------------------------------------------------------
-- Planted data-quality issues (detect these in your reconciliation report)
-- ---------------------------------------------------------------------------

-- 1) Duplicate charge: res-105 was double-billed June rent.
INSERT INTO resident_ledger (property_id, resident_id, entry_type, charge_code, amount_cents, occurred_at) VALUES
    ('prop-001', 'res-105', 'charge', 'rent', 150000, '2026-06-01T00:00:00Z');

-- 2) Orphaned ledger entry: resident 'res-999' does not exist.
INSERT INTO resident_ledger (property_id, resident_id, entry_type, charge_code, amount_cents, occurred_at) VALUES
    ('prop-001', 'res-999', 'charge', 'late_fee', 7500, '2026-05-15T00:00:00Z');

-- 3) A payment recorded locally that BigPMS has no record of.
INSERT INTO resident_ledger (property_id, resident_id, entry_type, amount_cents, bigpms_event_id, occurred_at) VALUES
    ('prop-001', 'res-107', 'payment', 50000, 'evt-9999', '2026-05-20T10:00:00Z');
