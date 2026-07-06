-- Run this in Supabase SQL Editor (supabase.com → your project → SQL Editor)
--
-- Raw material columns below (cement_ppc_qty, ggbs_qty, ht_wire_*_qty / pct_*)
-- match core/config.py's RAW_MATERIALS list. If you add/rename a raw material
-- there, add/rename the matching "<key>_qty" and "pct_<key>" columns here too.

CREATE TABLE IF NOT EXISTS production (
    id              BIGSERIAL PRIMARY KEY,
    date            TEXT    NOT NULL,
    product         TEXT    NOT NULL,
    nos             REAL    NOT NULL,
    plant           TEXT,
    operator_name   TEXT,
    cement_ppc_qty      REAL DEFAULT 0,
    ggbs_qty            REAL DEFAULT 0,
    ht_wire_3mm_qty     REAL DEFAULT 0,
    ht_wire_3_5mm_qty   REAL DEFAULT 0,
    ht_wire_4mm_qty     REAL DEFAULT 0,
    ht_wire_5_5mm_qty   REAL DEFAULT 0,
    rm_cost         REAL DEFAULT 0,
    labour_cost     REAL DEFAULT 0,
    transport_cost  REAL DEFAULT 0,
    power_cost      REAL DEFAULT 0,
    emi_cost        REAL DEFAULT 0,
    dg_cost         REAL DEFAULT 0,
    admin_cost      REAL DEFAULT 0,
    misc_cost       REAL DEFAULT 0,
    total_cost      REAL DEFAULT 0,
    revenue         REAL DEFAULT 0,
    profit          REAL DEFAULT 0,
    profit_pct      REAL DEFAULT 0,
    total_wt_kg     REAL DEFAULT 0,
    pct_cement_ppc      REAL DEFAULT 0,
    pct_ggbs            REAL DEFAULT 0,
    pct_ht_wire_3mm     REAL DEFAULT 0,
    pct_ht_wire_3_5mm   REAL DEFAULT 0,
    pct_ht_wire_4mm     REAL DEFAULT 0,
    pct_ht_wire_5_5mm   REAL DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

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
    cement_ppc      REAL DEFAULT 0,
    ggbs            REAL DEFAULT 0,
    ht_wire_3mm     REAL DEFAULT 0,
    ht_wire_3_5mm   REAL DEFAULT 0,
    ht_wire_4mm     REAL DEFAULT 0,
    ht_wire_5_5mm   REAL DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Per-product selling price / labour / transport / power overrides, edited
-- live via Admin > Product Cost Configuration. "product" must be unique so
-- the app's upsert (Prefer: resolution=merge-duplicates) works.
CREATE TABLE IF NOT EXISTS product_config (
    id                    BIGSERIAL PRIMARY KEY,
    product               TEXT UNIQUE NOT NULL,
    selling_price         REAL DEFAULT 0,
    labour_production     REAL DEFAULT 0,
    labour_loading        REAL DEFAULT 0,
    transport_per_block   REAL DEFAULT 0,
    power_per_block       REAL DEFAULT 0,
    created_at            TIMESTAMPTZ DEFAULT NOW()
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
