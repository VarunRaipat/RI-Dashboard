-- Run this in Supabase SQL Editor (supabase.com → your project → SQL Editor)
--
-- Raw material columns below (concrete_qty/cost, steel_qty/cost) match
-- core/config.py's RAW_MATERIALS list — both are fixed per-unit figures on
-- the product (never entered per DPR batch), multiplied by Nos. Jalli
-- (cage welding) is NOT a raw material — it's a flat Rs./nos rate like
-- Welding, so it only has a jalli_cost column, no jalli_qty/jalli price.
-- If you add/rename a raw material in RAW_MATERIALS, add/rename the
-- matching "<key>_qty" and "<key>_cost" columns here too.
--
-- If you already ran an earlier version of this file against a live
-- project, don't re-run the CREATE TABLE statements below (IF NOT EXISTS
-- makes them no-ops anyway) — instead scroll to the "Migration: Concrete
-- costing" block near the end of this file and run just that.

CREATE TABLE IF NOT EXISTS production (
    id              BIGSERIAL PRIMARY KEY,
    date            TEXT    NOT NULL,
    product         TEXT    NOT NULL,
    nos             REAL    NOT NULL,
    plant           TEXT,
    operator_name   TEXT,
    concrete_qty    REAL DEFAULT 0,
    concrete_cost   REAL DEFAULT 0,
    steel_qty       REAL DEFAULT 0,
    steel_cost      REAL DEFAULT 0,
    rm_cost         REAL DEFAULT 0,
    production_cost         REAL DEFAULT 0,
    loading_unloading_cost  REAL DEFAULT 0,
    power_cost      REAL DEFAULT 0,
    welding_cost    REAL DEFAULT 0,
    jalli_cost      REAL DEFAULT 0,
    emi_cost        REAL DEFAULT 0,
    dg_cost         REAL DEFAULT 0,
    admin_cost      REAL DEFAULT 0,
    misc_cost       REAL DEFAULT 0,
    total_cost      REAL DEFAULT 0,
    revenue         REAL DEFAULT 0,
    profit          REAL DEFAULT 0,
    profit_pct      REAL DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- One row per DPR submission (batch) — total Cement/GGBS bags consumed that
-- day, not tied to any single product line. Used for inventory
-- reconciliation only (see core/inventory.py's rm_summary()) — doesn't
-- affect cost/profit, which is based on Concrete Volume instead.
CREATE TABLE IF NOT EXISTS rm_usage (
    id              BIGSERIAL PRIMARY KEY,
    date            TEXT    NOT NULL,
    cement_bags     REAL DEFAULT 0,
    ggbs_bags       REAL DEFAULT 0,
    remarks         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE rm_usage DISABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS dispatch (
    id              BIGSERIAL PRIMARY KEY,
    date            TEXT    NOT NULL,
    challan_no      TEXT,
    di_no           TEXT,
    bill_no         TEXT,
    client_name     TEXT,
    delivery_address TEXT,
    product         TEXT,
    qty_ordered     REAL DEFAULT 0,
    qty_dispatched  REAL DEFAULT 0,
    rate            REAL DEFAULT 0,
    dispatch_value  REAL DEFAULT 0,
    trip_distance   REAL DEFAULT 0,
    truck_no        TEXT,
    driver_name     TEXT,
    remarks         TEXT,
    form_filled_by  TEXT,
    sale_type       TEXT DEFAULT 'Sale A',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rm_prices (
    id              BIGSERIAL PRIMARY KEY,
    effective_date  TEXT    NOT NULL,
    concrete        REAL DEFAULT 2500,
    steel           REAL DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Per-product Selling Price + Concrete Volume, edited live via
-- Admin > Product Cost Configuration > Selling Price & Concrete. For
-- non-pipe products this also carries Production/Loading/Power/Welding/
-- Jalli/Steel (Hume Pipes get those from pipe_diameter_config instead,
-- below, since they're shared across class+Joint Type at a given diameter).
-- "product" must be unique so the app's upsert
-- (Prefer: resolution=merge-duplicates) works. No transport column — real
-- transport cost is tracked in the Dispatch module instead.
-- No power_per_block column — Power is a flat Rs.1,000/entry cost (like
-- EMI/DG/Admin), not a per-unit rate; see core/config.py's POWER_PER_ENTRY.
CREATE TABLE IF NOT EXISTS product_config (
    id                      BIGSERIAL PRIMARY KEY,
    product                 TEXT UNIQUE NOT NULL,
    selling_price           REAL DEFAULT 0,
    production_cost         REAL DEFAULT 0,
    loading_unloading_cost  REAL DEFAULT 0,
    welding_cost            REAL DEFAULT 0,
    jalli_cost              REAL DEFAULT 0,
    concrete_volume_m3      REAL DEFAULT 0,
    steel_kg_per_unit       REAL DEFAULT 0,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Production/Loading-Unloading/Welding/Jalli/Steel rates for Hume Pipes,
-- keyed by diameter (mm) only — the same rate applies to every class
-- (NP2/NP3/NP4) and Joint Type at that diameter, confirmed by the client.
-- Edited via Admin > Product Cost Configuration > Pipe Diameter Rates.
-- "diameter_mm" must be unique for the app's upsert to work.
CREATE TABLE IF NOT EXISTS pipe_diameter_config (
    id                      BIGSERIAL PRIMARY KEY,
    diameter_mm             INTEGER UNIQUE NOT NULL,
    production_cost         REAL DEFAULT 0,
    loading_unloading_cost  REAL DEFAULT 0,
    welding_cost            REAL DEFAULT 0,
    jalli_cost              REAL DEFAULT 0,
    steel_kg_per_unit       REAL DEFAULT 0,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
    id                BIGSERIAL PRIMARY KEY,
    order_date        TEXT NOT NULL,
    di_no             TEXT NOT NULL,
    factory           TEXT,
    client_name       TEXT,
    contact_person    TEXT,
    phone             TEXT,
    office            TEXT,
    gstin             TEXT,
    client_type       TEXT,
    mode_of_payment   TEXT,
    delivery_address  TEXT,
    site_person       TEXT,
    site_phone        TEXT,
    product           TEXT NOT NULL,
    joint_type        TEXT,
    qty_ordered       REAL DEFAULT 0,
    rate              REAL DEFAULT 0,
    total_amount      REAL DEFAULT 0,
    delivery_date     TEXT,
    sale_type         TEXT DEFAULT 'Sale A',
    remarks           TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vendor_transactions (
    id              BIGSERIAL PRIMARY KEY,
    date            TEXT    NOT NULL,
    vendor_name     TEXT    NOT NULL,
    category        TEXT,
    txn_type        TEXT    NOT NULL,
    amount          REAL    NOT NULL,
    reference_no    TEXT,
    remarks         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS loan_payments (
    id              BIGSERIAL PRIMARY KEY,
    month           TEXT    NOT NULL,
    loan_name       TEXT    NOT NULL,
    emi_amount      REAL DEFAULT 0,
    paid_amount     REAL DEFAULT 0,
    payment_date    TEXT,
    remarks         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Quality Control test parameters differ for Hume Pipes / precast concrete
-- (e.g. hydrostatic pressure, three-edge bearing load) vs. Ecostructures'
-- compressive-strength blocks. This keeps the same 3-sample-average shape;
-- rename "sample_1/2/3" and "average" in views/quality.py if the actual test
-- you run isn't a 3-sample average.
CREATE TABLE IF NOT EXISTS quality_control (
    id              BIGSERIAL PRIMARY KEY,
    test_date       TEXT    NOT NULL,
    casting_date    TEXT    NOT NULL,
    product         TEXT    NOT NULL,
    sample_1        REAL DEFAULT 0,
    sample_2        REAL DEFAULT 0,
    sample_3        REAL DEFAULT 0,
    average         REAL DEFAULT 0,
    remarks         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rm_purchases (
    id              BIGSERIAL PRIMARY KEY,
    date            TEXT    NOT NULL,
    material        TEXT    NOT NULL,
    qty_bags        REAL DEFAULT 0,
    remarks         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gate_entries (
    id                  BIGSERIAL PRIMARY KEY,
    date                TEXT    NOT NULL,
    category            TEXT    NOT NULL,
    direction           TEXT    NOT NULL,
    item                TEXT,
    challan_no          TEXT,
    invoice_no          TEXT,
    truck_no            TEXT,
    qty                 REAL DEFAULT 0,
    unit                TEXT,
    supplier_name       TEXT,
    site                TEXT,
    responsible_person  TEXT,
    remarks             TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS activity_log (
    id         BIGSERIAL PRIMARY KEY,
    username   TEXT,
    role       TEXT,
    name       TEXT,
    action     TEXT,
    module     TEXT,
    detail     TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Migration: Concrete costing replaces Cement+GGBS batch entry ────────────
-- Run this block if your project was already set up before this change —
-- it's safe to run even with existing data (old cement_ppc_qty/ggbs_qty/
-- pct_*/labour_*/transport_* columns are left in place, just unused;
-- nothing is dropped). Jalli (cage welding) is a flat Rs./nos rate, not a
-- priced raw material — only jalli_cost exists, no jalli_qty/jalli price.
ALTER TABLE production     ADD COLUMN IF NOT EXISTS concrete_qty REAL DEFAULT 0;
ALTER TABLE production     ADD COLUMN IF NOT EXISTS concrete_cost REAL DEFAULT 0;
ALTER TABLE production     ADD COLUMN IF NOT EXISTS steel_qty REAL DEFAULT 0;
ALTER TABLE production     ADD COLUMN IF NOT EXISTS steel_cost REAL DEFAULT 0;
ALTER TABLE production     ADD COLUMN IF NOT EXISTS production_cost REAL DEFAULT 0;
ALTER TABLE production     ADD COLUMN IF NOT EXISTS loading_unloading_cost REAL DEFAULT 0;
ALTER TABLE production     ADD COLUMN IF NOT EXISTS welding_cost REAL DEFAULT 0;
ALTER TABLE production     ADD COLUMN IF NOT EXISTS jalli_cost REAL DEFAULT 0;
ALTER TABLE rm_prices      ADD COLUMN IF NOT EXISTS concrete REAL DEFAULT 2500;
ALTER TABLE rm_prices      ADD COLUMN IF NOT EXISTS steel REAL DEFAULT 0;
ALTER TABLE product_config ADD COLUMN IF NOT EXISTS production_cost REAL DEFAULT 0;
ALTER TABLE product_config ADD COLUMN IF NOT EXISTS loading_unloading_cost REAL DEFAULT 0;
ALTER TABLE product_config ADD COLUMN IF NOT EXISTS welding_cost REAL DEFAULT 0;
ALTER TABLE product_config ADD COLUMN IF NOT EXISTS jalli_cost REAL DEFAULT 0;
ALTER TABLE product_config ADD COLUMN IF NOT EXISTS concrete_volume_m3 REAL DEFAULT 0;
ALTER TABLE product_config ADD COLUMN IF NOT EXISTS steel_kg_per_unit REAL DEFAULT 0;

-- ── Migration: Pipe Diameter Rates (shared across class + Joint Type) ───────
-- New table — Production/Loading-Unloading/Welding/Jalli/Steel for Hume
-- Pipes now live here, keyed by diameter only, instead of being duplicated
-- (and edited separately) across every NP2/NP3/NP4 pricing key.
CREATE TABLE IF NOT EXISTS pipe_diameter_config (
    id                      BIGSERIAL PRIMARY KEY,
    diameter_mm             INTEGER UNIQUE NOT NULL,
    production_cost         REAL DEFAULT 0,
    loading_unloading_cost  REAL DEFAULT 0,
    welding_cost            REAL DEFAULT 0,
    jalli_cost              REAL DEFAULT 0,
    steel_kg_per_unit       REAL DEFAULT 0,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE pipe_diameter_config DISABLE ROW LEVEL SECURITY;

-- ── Migration: Cement/GGBS batch usage (inventory reconciliation only) ──────
-- New table — DPR now asks for the day's total Cement/GGBS bags consumed,
-- separate from any single product line. Doesn't affect cost/profit.
CREATE TABLE IF NOT EXISTS rm_usage (
    id              BIGSERIAL PRIMARY KEY,
    date            TEXT    NOT NULL,
    cement_bags     REAL DEFAULT 0,
    ggbs_bags       REAL DEFAULT 0,
    remarks         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE rm_usage DISABLE ROW LEVEL SECURITY;

-- ── Migration: Power becomes a flat Rs.1,000/entry cost ─────────────────────
-- Power_per_block columns (if they exist from an earlier version) are left
-- in place, just unused — nothing is dropped.

-- ── Row Level Security ───────────────────────────────────────────────────────
-- Supabase enables RLS by default on new tables. This app authenticates via
-- its own login screen (not Supabase Auth) and talks to Supabase with one
-- shared API key for every operation, so an RLS policy would just block all
-- reads/writes with a "new row violates row-level security policy" error
-- (Postgres code 42501) and provide no real benefit. Disable it on every
-- app table — always run this on a fresh project, right after creating the
-- tables above.
ALTER TABLE production         DISABLE ROW LEVEL SECURITY;
ALTER TABLE dispatch           DISABLE ROW LEVEL SECURITY;
ALTER TABLE rm_prices          DISABLE ROW LEVEL SECURITY;
ALTER TABLE product_config     DISABLE ROW LEVEL SECURITY;
ALTER TABLE orders             DISABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_transactions DISABLE ROW LEVEL SECURITY;
ALTER TABLE loan_payments      DISABLE ROW LEVEL SECURITY;
ALTER TABLE quality_control    DISABLE ROW LEVEL SECURITY;
ALTER TABLE rm_purchases       DISABLE ROW LEVEL SECURITY;
ALTER TABLE gate_entries       DISABLE ROW LEVEL SECURITY;
ALTER TABLE activity_log       DISABLE ROW LEVEL SECURITY;
