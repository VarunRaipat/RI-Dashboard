"""
Database layer — uses Supabase REST API (via requests, no DLL issues)
when credentials are in .streamlit/secrets.toml, otherwise falls back to SQLite.
"""
import sqlite3
import json
import pandas as pd
import requests
import streamlit as st
from pathlib import Path
from core.config import DEFAULT_RM_PRICES

DB_PATH = Path(__file__).parent.parent / "data" / "ecostructures.db"


def _invalidate_cache():
    """Call after any write so cached reads reflect it immediately, instead
    of waiting out the TTL."""
    st.cache_data.clear()


# ── Detect which backend ──────────────────────────────────────────────────────
def _creds():
    try:
        import streamlit as st
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY", "")
        if url and key:
            return url.rstrip("/"), key
    except Exception:
        pass
    return None, None


def _use_supabase():
    u, k = _creds()
    return bool(u and k)


# ── Supabase REST helpers ─────────────────────────────────────────────────────
def _headers():
    _, key = _creds()
    return {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }


def _sb_url(table):
    url, _ = _creds()
    return f"{url}/rest/v1/{table}"


def _sb_insert(table, data):
    r = requests.post(_sb_url(table), headers=_headers(), json=data)
    if r.status_code not in (200, 201):
        raise Exception(f"Supabase insert failed: {r.text}")


def _sb_select(table, filters=None, order="date.desc,id.desc", limit=5000):
    params = {"select": "*", "order": order, "limit": limit}
    if filters:
        params.update(filters)
    r = requests.get(_sb_url(table), headers=_headers(), params=params)
    if r.status_code != 200:
        raise Exception(f"Supabase select failed: {r.text}")
    return pd.DataFrame(r.json())


def _sb_update(table, row_id, data):
    r = requests.patch(
        _sb_url(table),
        headers={**_headers(), "Prefer": "return=minimal"},
        params={"id": f"eq.{row_id}"},
        json=data,
    )
    if r.status_code not in (200, 204):
        raise Exception(f"Supabase update failed: {r.text}")


def _sb_delete(table, row_id):
    r = requests.delete(
        _sb_url(table),
        headers={**_headers(), "Prefer": ""},
        params={"id": f"eq.{row_id}"},
    )
    if r.status_code not in (200, 204):
        raise Exception(f"Supabase delete failed: {r.text}")


# ── SQLite helpers ────────────────────────────────────────────────────────────
def _conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    return sqlite3.connect(str(DB_PATH), check_same_thread=False)


def init_db():
    if _use_supabase():
        return  # tables created via supabase_schema.sql
    con = _conn()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_date TEXT NOT NULL,
        di_no TEXT NOT NULL,
        factory TEXT,
        client_name TEXT,
        contact_person TEXT,
        phone TEXT,
        office TEXT,
        gstin TEXT,
        client_type TEXT,
        mode_of_payment TEXT,
        delivery_address TEXT,
        site_person TEXT,
        site_phone TEXT,
        product TEXT NOT NULL,
        joint_type TEXT,
        qty_ordered REAL DEFAULT 0,
        rate REAL DEFAULT 0,
        total_amount REAL DEFAULT 0,
        delivery_date TEXT,
        sale_type TEXT DEFAULT 'Sale A',
        remarks TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS production (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, product TEXT NOT NULL, nos REAL NOT NULL,
        plant TEXT, operator_name TEXT,
        concrete_qty REAL DEFAULT 0, concrete_cost REAL DEFAULT 0,
        steel_qty REAL DEFAULT 0, steel_cost REAL DEFAULT 0,
        rm_cost REAL DEFAULT 0,
        production_cost REAL DEFAULT 0, loading_unloading_cost REAL DEFAULT 0,
        power_cost REAL DEFAULT 0, welding_cost REAL DEFAULT 0, jalli_cost REAL DEFAULT 0,
        emi_cost REAL DEFAULT 0, dg_cost REAL DEFAULT 0, admin_cost REAL DEFAULT 0, misc_cost REAL DEFAULT 0,
        total_cost REAL DEFAULT 0, revenue REAL DEFAULT 0,
        profit REAL DEFAULT 0, profit_pct REAL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS rm_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        cement_bags REAL DEFAULT 0,
        ggbs_bags REAL DEFAULT 0,
        remarks TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS dispatch (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, challan_no TEXT, di_no TEXT, bill_no TEXT,
        client_name TEXT, delivery_address TEXT, product TEXT,
        qty_ordered REAL DEFAULT 0, qty_dispatched REAL DEFAULT 0,
        rate REAL DEFAULT 0, dispatch_value REAL DEFAULT 0,
        trip_distance REAL DEFAULT 0, truck_no TEXT, driver_name TEXT,
        remarks TEXT, form_filled_by TEXT, sale_type TEXT DEFAULT 'Sale A',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS rm_prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        effective_date TEXT NOT NULL,
        concrete REAL DEFAULT 2500, steel REAL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS product_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product TEXT UNIQUE NOT NULL,
        selling_price REAL DEFAULT 0,
        production_cost REAL DEFAULT 0,
        loading_unloading_cost REAL DEFAULT 0,
        welding_cost REAL DEFAULT 0,
        jalli_cost REAL DEFAULT 0,
        concrete_volume_m3 REAL DEFAULT 0,
        steel_kg_per_unit REAL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS pipe_diameter_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        diameter_mm INTEGER UNIQUE NOT NULL,
        production_cost REAL DEFAULT 0,
        loading_unloading_cost REAL DEFAULT 0,
        welding_cost REAL DEFAULT 0,
        jalli_cost REAL DEFAULT 0,
        steel_kg_per_unit REAL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS vendor_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        vendor_name TEXT NOT NULL,
        category TEXT,
        txn_type TEXT NOT NULL,
        amount REAL NOT NULL,
        reference_no TEXT,
        remarks TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS loan_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT NOT NULL,
        loan_name TEXT NOT NULL,
        emi_amount REAL DEFAULT 0,
        paid_amount REAL DEFAULT 0,
        payment_date TEXT,
        remarks TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS quality_control (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        test_date    TEXT NOT NULL,
        casting_date TEXT NOT NULL,
        product      TEXT NOT NULL,
        sample_1     REAL DEFAULT 0,
        sample_2     REAL DEFAULT 0,
        sample_3     REAL DEFAULT 0,
        average      REAL DEFAULT 0,
        remarks      TEXT,
        created_at   TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS rm_purchases (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        date       TEXT NOT NULL,
        material   TEXT NOT NULL,
        qty_bags   REAL DEFAULT 0,
        remarks    TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS gate_entries (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        date               TEXT NOT NULL,
        category           TEXT NOT NULL,
        direction          TEXT NOT NULL,
        item               TEXT,
        challan_no         TEXT,
        invoice_no         TEXT,
        truck_no           TEXT,
        qty                REAL DEFAULT 0,
        unit               TEXT,
        supplier_name      TEXT,
        site               TEXT,
        responsible_person TEXT,
        remarks            TEXT,
        created_at         TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS activity_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        username   TEXT, role TEXT, name TEXT,
        action     TEXT, module TEXT, detail TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    """)
    con.commit()
    con.close()


def _sqlite_insert(table, data):
    con = _conn()
    cols = ", ".join(data.keys())
    ph   = ", ".join("?" for _ in data)
    con.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", list(data.values()))
    con.commit(); con.close()


# ── Activity Log (admin-only audit trail — who opened/edited what) ───────────
def log_activity(action, module, detail=""):
    """Record who did what, for the admin-only Activity Log.

    Never raises — a logging hiccup (e.g. the table isn't migrated yet on a
    fresh Supabase project) must not block the real action the user took.
    """
    data = {
        "username": st.session_state.get("username") or "",
        "role":     st.session_state.get("role") or "",
        "name":     st.session_state.get("name") or "",
        "action":   action,
        "module":   module,
        "detail":   detail,
    }
    try:
        if _use_supabase(): _sb_insert("activity_log", data)
        else: _sqlite_insert("activity_log", data)
    except Exception:
        pass


@st.cache_data(ttl=15)
def get_activity_log():
    if _use_supabase():
        return _sb_select("activity_log", order="created_at.desc,id.desc", limit=3000)
    try:
        con = _conn()
        df = pd.read_sql("SELECT * FROM activity_log ORDER BY created_at DESC, id DESC", con)
        con.close()
        return df
    except Exception:
        return pd.DataFrame()


# ── Public API ────────────────────────────────────────────────────────────────
def insert_production(data):
    if _use_supabase(): _sb_insert("production", data)
    else: _sqlite_insert("production", data)
    _invalidate_cache()
    log_activity("create", "DPR Entry", f"{data.get('product','')} · {data.get('nos','')} nos · {data.get('plant','')}")


def insert_rm_usage(data):
    """One row per DPR submission (batch) — Cement/GGBS bags consumed that
    day, not tied to any single product line (see views/dpr.py)."""
    if _use_supabase(): _sb_insert("rm_usage", data)
    else: _sqlite_insert("rm_usage", data)
    _invalidate_cache()
    log_activity("create", "DPR Entry", f"RM usage: cement {data.get('cement_bags','')} bags, ggbs {data.get('ggbs_bags','')} bags")


@st.cache_data(ttl=30)
def get_rm_usage(start=None, end=None):
    if _use_supabase():
        params = {"select": "*", "order": "date.desc,id.desc", "limit": 5000}
        if start: params["date"] = f"gte.{start}"
        r = requests.get(_sb_url("rm_usage"), headers=_headers(), params=params)
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        if start and end:
            data = [row for row in data if start <= row.get("date", "") <= end]
        return pd.DataFrame(data) if data else pd.DataFrame()
    else:
        try:
            con = _conn()
            q = "SELECT * FROM rm_usage"
            if start and end:
                q += f" WHERE date >= '{start}' AND date <= '{end}'"
            q += " ORDER BY date DESC, id DESC"
            df = pd.read_sql(q, con); con.close()
            return df
        except Exception:
            return pd.DataFrame()


def insert_dispatch(data):
    if _use_supabase(): _sb_insert("dispatch", data)
    else: _sqlite_insert("dispatch", data)
    _invalidate_cache()
    log_activity("create", "Dispatch", f"Challan {data.get('challan_no','')} · {data.get('product','')}")


@st.cache_data(ttl=30)
def get_production(start=None, end=None):
    if _use_supabase():
        f = {}
        if start: f["date"] = f"gte.{start}"
        if end:   f["date"] = f"lte.{end}"
        # For date range, use separate params
        params = {"select": "*", "order": "date.desc,id.desc", "limit": 5000}
        if start: params["date"] = f"gte.{start}"
        url, _ = _creds()
        r = requests.get(
            f"{url}/rest/v1/production",
            headers=_headers(),
            params=params,
        )
        if r.status_code != 200:
            raise Exception(f"Supabase error: {r.text}")
        data = r.json()
        if start and end:
            data = [row for row in data if start <= row.get("date","") <= end]
        return pd.DataFrame(data) if data else pd.DataFrame()
    else:
        con = _conn()
        q = "SELECT * FROM production"
        if start and end:
            q += f" WHERE date >= '{start}' AND date <= '{end}'"
        q += " ORDER BY date DESC, id DESC"
        df = pd.read_sql(q, con); con.close()
        return df


@st.cache_data(ttl=30)
def get_dispatch(start=None, end=None):
    if _use_supabase():
        params = {"select": "*", "order": "date.desc,id.desc", "limit": 5000}
        if start: params["date"] = f"gte.{start}"
        url, _ = _creds()
        r = requests.get(
            f"{url}/rest/v1/dispatch",
            headers=_headers(),
            params=params,
        )
        if r.status_code != 200:
            raise Exception(f"Supabase error: {r.text}")
        data = r.json()
        if start and end:
            data = [row for row in data if start <= row.get("date","") <= end]
        return pd.DataFrame(data) if data else pd.DataFrame()
    else:
        con = _conn()
        q = "SELECT * FROM dispatch"
        if start and end:
            q += f" WHERE date >= '{start}' AND date <= '{end}'"
        q += " ORDER BY date DESC, id DESC"
        df = pd.read_sql(q, con); con.close()
        return df


@st.cache_data(ttl=60)
def get_rm_prices():
    if _use_supabase():
        r = requests.get(
            _sb_url("rm_prices"),
            headers=_headers(),
            params={"select": "*", "order": "created_at.desc", "limit": 1},
        )
        if r.status_code == 200 and r.json():
            row = r.json()[0]
            return {k: float(row[k]) for k in DEFAULT_RM_PRICES if k in row}
        return DEFAULT_RM_PRICES.copy()
    else:
        con = _conn()
        df = pd.read_sql("SELECT * FROM rm_prices ORDER BY created_at DESC LIMIT 1", con)
        con.close()
        if df.empty: return DEFAULT_RM_PRICES.copy()
        row = df.iloc[0]
        return {k: float(row[k]) for k in DEFAULT_RM_PRICES if k in row.index}


def save_rm_prices(prices):
    from datetime import date
    data = {"effective_date": str(date.today()), **prices}
    if _use_supabase(): _sb_insert("rm_prices", data)
    else: _sqlite_insert("rm_prices", data)
    _invalidate_cache()
    log_activity("update", "Admin", "RM prices updated")


_PRODUCT_CFG_FIELDS = ["selling_price","production_cost","loading_unloading_cost",
                       "welding_cost","jalli_cost","concrete_volume_m3","steel_kg_per_unit"]


@st.cache_data(ttl=60)
def get_product_config():
    """Pipe entries in PRODUCT_CONFIG only carry selling_price/concrete_volume_m3
    (their other 6 rates live in pipe_diameter_config instead — see
    calculate_production()), so the overlay below only copies a field if the
    product's own base config already has that key. Without this guard, a
    pipe's product_config row would still return the other columns' table
    DEFAULT 0 and silently zero out its real diameter-shared rates."""
    from core.config import PRODUCT_CONFIG
    result = {k: dict(v) for k, v in PRODUCT_CONFIG.items()}
    if _use_supabase():
        r = requests.get(_sb_url("product_config"), headers=_headers(), params={"select": "*", "limit": 100})
        if r.status_code == 200:
            for row in r.json():
                prod = row.get("product")
                if prod in result:
                    for f in _PRODUCT_CFG_FIELDS:
                        if f in result[prod] and row.get(f) is not None:
                            result[prod][f] = float(row[f])
    else:
        try:
            con = _conn()
            df = pd.read_sql("SELECT * FROM product_config", con)
            con.close()
            for _, row in df.iterrows():
                prod = row.get("product")
                if prod in result:
                    for f in _PRODUCT_CFG_FIELDS:
                        if f in result[prod] and pd.notna(row.get(f)):
                            result[prod][f] = float(row[f])
        except Exception:
            pass
    return result


_PIPE_DIAMETER_FIELDS = ["production_cost","loading_unloading_cost",
                         "welding_cost","jalli_cost","steel_kg_per_unit"]


@st.cache_data(ttl=60)
def get_pipe_diameter_config():
    """Production/Loading-Unloading/Power/Welding/Jalli/Steel rates for Hume
    Pipes, keyed by diameter (mm) only — shared across every class/Joint Type
    SKU at that diameter. See core/config.py's PIPE_DIAMETER_CONFIG."""
    from core.config import PIPE_DIAMETER_CONFIG
    result = {d: dict(v) for d, v in PIPE_DIAMETER_CONFIG.items()}
    if _use_supabase():
        r = requests.get(_sb_url("pipe_diameter_config"), headers=_headers(), params={"select": "*", "limit": 100})
        if r.status_code == 200:
            for row in r.json():
                d = row.get("diameter_mm")
                if d in result:
                    for f in _PIPE_DIAMETER_FIELDS:
                        if row.get(f) is not None:
                            result[d][f] = float(row[f])
    else:
        try:
            con = _conn()
            df = pd.read_sql("SELECT * FROM pipe_diameter_config", con)
            con.close()
            for _, row in df.iterrows():
                d = int(row.get("diameter_mm"))
                if d in result:
                    for f in _PIPE_DIAMETER_FIELDS:
                        if pd.notna(row.get(f)):
                            result[d][f] = float(row[f])
        except Exception:
            pass
    return result


def save_pipe_diameter_config(diameter_mm, data):
    payload = {"diameter_mm": diameter_mm, **data}
    if _use_supabase():
        r = requests.post(
            _sb_url("pipe_diameter_config"),
            headers={**_headers(), "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "diameter_mm"},
            json=payload,
        )
        if r.status_code not in (200, 201):
            raise Exception(f"Save failed: {r.text}")
    else:
        con = _conn()
        cols = ", ".join(payload.keys())
        ph   = ", ".join("?" for _ in payload)
        con.execute(
            f"INSERT INTO pipe_diameter_config ({cols}) VALUES ({ph}) "
            f"ON CONFLICT(diameter_mm) DO UPDATE SET "
            + ", ".join(f"{k} = excluded.{k}" for k in data),
            list(payload.values()),
        )
        con.commit(); con.close()
    _invalidate_cache()
    log_activity("update", "Admin", f"Pipe diameter config updated: {diameter_mm}mm")


def save_product_config(product, data):
    payload = {"product": product, **data}
    if _use_supabase():
        r = requests.post(
            _sb_url("product_config"),
            headers={**_headers(), "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "product"},
            json=payload,
        )
        if r.status_code not in (200, 201):
            raise Exception(f"Save failed: {r.text}")
    else:
        con = _conn()
        cols = ", ".join(payload.keys())
        ph   = ", ".join("?" for _ in payload)
        con.execute(
            f"INSERT INTO product_config ({cols}) VALUES ({ph}) "
            f"ON CONFLICT(product) DO UPDATE SET "
            + ", ".join(f"{k} = excluded.{k}" for k in data),
            list(payload.values()),
        )
        con.commit(); con.close()
    _invalidate_cache()
    log_activity("update", "Admin", f"Product config updated: {product}")


def update_production(row_id, data):
    if _use_supabase():
        _sb_update("production", row_id, data)
    else:
        con = _conn()
        sets = ", ".join(f"{k} = ?" for k in data)
        con.execute(f"UPDATE production SET {sets} WHERE id = ?", list(data.values()) + [row_id])
        con.commit(); con.close()
    _invalidate_cache()
    log_activity("update", "DPR Entry", f"ID {row_id}")


def update_dispatch(row_id, data):
    if _use_supabase():
        _sb_update("dispatch", row_id, data)
    else:
        con = _conn()
        sets = ", ".join(f"{k} = ?" for k in data)
        con.execute(f"UPDATE dispatch SET {sets} WHERE id = ?", list(data.values()) + [row_id])
        con.commit(); con.close()
    _invalidate_cache()
    detail = f"Bill No. {data['bill_no']} → ID {row_id}" if data.get("bill_no") else f"ID {row_id}"
    log_activity("update", "Dispatch", detail)


_MODULE_LABELS = {"production": "DPR Entry", "dispatch": "Dispatch", "quality_control": "Quality Control"}


def delete_row(table, row_id):
    if _use_supabase(): _sb_delete(table, row_id)
    else:
        con = _conn()
        con.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        con.commit(); con.close()
    _invalidate_cache()
    log_activity("delete", _MODULE_LABELS.get(table, table), f"ID {row_id}")


def delete_dispatch_range(start, end):
    """Delete all dispatch rows in date range — single API call to Supabase."""
    if _use_supabase():
        url, _ = _creds()
        r = requests.delete(
            f"{url}/rest/v1/dispatch",
            headers={**_headers(), "Prefer": ""},
            params={"and": f"(date.gte.{start},date.lte.{end})"},
        )
        if r.status_code not in (200, 204):
            raise Exception(f"Bulk delete failed: {r.text}")
    else:
        con = _conn()
        con.execute("DELETE FROM dispatch WHERE date >= ? AND date <= ?", (start, end))
        con.commit(); con.close()
    _invalidate_cache()
    log_activity("delete", "Dispatch", f"All entries {start} to {end}")


def insert_order(data):
    if _use_supabase(): _sb_insert("orders", data)
    else: _sqlite_insert("orders", data)
    _invalidate_cache()
    log_activity("create", "Sales Orders", f"DI {data.get('di_no','')} · {data.get('product','')}")


@st.cache_data(ttl=30)
def get_orders():
    if _use_supabase():
        r = requests.get(_sb_url("orders"), headers=_headers(),
                         params={"select": "*", "order": "order_date.desc,id.desc", "limit": 5000})
        if r.status_code != 200:
            return pd.DataFrame()  # table likely not created yet
        data = r.json()
        return pd.DataFrame(data) if data else pd.DataFrame()
    else:
        try:
            con = _conn()
            df = pd.read_sql("SELECT * FROM orders ORDER BY order_date DESC, id DESC", con)
            con.close()
            return df
        except Exception:
            return pd.DataFrame()


@st.cache_data(ttl=30)
def get_order_by_di(di_no):
    """Return all product lines for a given DI number."""
    if _use_supabase():
        r = requests.get(_sb_url("orders"), headers=_headers(),
                         params={"di_no": f"eq.{di_no}", "select": "*"})
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        return pd.DataFrame(data) if data else pd.DataFrame()
    else:
        try:
            con = _conn()
            df = pd.read_sql("SELECT * FROM orders WHERE di_no = ?", con, params=(di_no,))
            con.close()
            return df
        except Exception:
            return pd.DataFrame()


def update_order(row_id, data):
    if _use_supabase():
        _sb_update("orders", row_id, data)
    else:
        con = _conn()
        sets = ", ".join(f"{k} = ?" for k in data)
        con.execute(f"UPDATE orders SET {sets} WHERE id = ?", list(data.values()) + [row_id])
        con.commit(); con.close()
    _invalidate_cache()
    log_activity("update", "Sales Orders", f"ID {row_id}")


def delete_order(row_id):
    if _use_supabase(): _sb_delete("orders", row_id)
    else:
        con = _conn()
        con.execute("DELETE FROM orders WHERE id = ?", (row_id,))
        con.commit(); con.close()
    _invalidate_cache()
    log_activity("delete", "Sales Orders", f"ID {row_id}")


def delete_dispatch_ids(ids):
    """Delete a list of dispatch IDs in a single API call."""
    if not ids:
        return
    if _use_supabase():
        url, _ = _creds()
        id_list = ",".join(str(i) for i in ids)
        r = requests.delete(
            f"{url}/rest/v1/dispatch",
            headers={**_headers(), "Prefer": ""},
            params={"id": f"in.({id_list})"},
        )
        if r.status_code not in (200, 204):
            raise Exception(f"Bulk delete failed: {r.text}")
    else:
        con = _conn()
        ph = ",".join("?" for _ in ids)
        con.execute(f"DELETE FROM dispatch WHERE id IN ({ph})", ids)
        con.commit(); con.close()
    _invalidate_cache()
    log_activity("delete", "Dispatch", f"{len(ids)} rows: {ids}")


# ── Vendor Transactions ───────────────────────────────────────────────────────
def insert_vendor_transaction(data):
    if _use_supabase(): _sb_insert("vendor_transactions", data)
    else: _sqlite_insert("vendor_transactions", data)
    _invalidate_cache()


@st.cache_data(ttl=30)
def get_vendor_transactions():
    if _use_supabase():
        r = requests.get(
            _sb_url("vendor_transactions"), headers=_headers(),
            params={"select": "*", "order": "date.desc,id.desc", "limit": 5000},
        )
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        return pd.DataFrame(data) if data else pd.DataFrame()
    else:
        try:
            con = _conn()
            df = pd.read_sql(
                "SELECT * FROM vendor_transactions ORDER BY date DESC, id DESC", con)
            con.close()
            return df
        except Exception:
            return pd.DataFrame()


def delete_vendor_transaction(row_id):
    if _use_supabase(): _sb_delete("vendor_transactions", row_id)
    else:
        con = _conn()
        con.execute("DELETE FROM vendor_transactions WHERE id = ?", (row_id,))
        con.commit(); con.close()
    _invalidate_cache()


# ── Loan Payments ─────────────────────────────────────────────────────────────
def insert_loan_payment(data):
    if _use_supabase(): _sb_insert("loan_payments", data)
    else: _sqlite_insert("loan_payments", data)
    _invalidate_cache()


@st.cache_data(ttl=30)
def get_loan_payments(month=None):
    if _use_supabase():
        params = {"select": "*", "order": "month.desc,id.desc", "limit": 1000}
        if month: params["month"] = f"eq.{month}"
        r = requests.get(_sb_url("loan_payments"), headers=_headers(), params=params)
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        return pd.DataFrame(data) if data else pd.DataFrame()
    else:
        try:
            con = _conn()
            q = "SELECT * FROM loan_payments"
            if month: q += f" WHERE month = '{month}'"
            q += " ORDER BY month DESC, id DESC"
            df = pd.read_sql(q, con); con.close()
            return df
        except Exception:
            return pd.DataFrame()


def delete_loan_payment(row_id):
    if _use_supabase(): _sb_delete("loan_payments", row_id)
    else:
        con = _conn()
        con.execute("DELETE FROM loan_payments WHERE id = ?", (row_id,))
        con.commit(); con.close()
    _invalidate_cache()


# ── Gate Entries (raw material / equipment / parts movement log) ─────────────
def insert_gate_entry(data):
    if _use_supabase(): _sb_insert("gate_entries", data)
    else: _sqlite_insert("gate_entries", data)
    _invalidate_cache()
    log_activity("create", "Gate Entry", f"{data.get('category','')} · {data.get('item','')} · {data.get('direction','')}")


@st.cache_data(ttl=30)
def get_gate_entries():
    if _use_supabase():
        r = requests.get(_sb_url("gate_entries"), headers=_headers(),
                         params={"select": "*", "order": "date.desc,id.desc", "limit": 5000})
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        return pd.DataFrame(data) if data else pd.DataFrame()
    else:
        try:
            con = _conn()
            df = pd.read_sql("SELECT * FROM gate_entries ORDER BY date DESC, id DESC", con)
            con.close()
            return df
        except Exception:
            return pd.DataFrame()


def delete_gate_entry(row_id):
    if _use_supabase(): _sb_delete("gate_entries", row_id)
    else:
        con = _conn()
        con.execute("DELETE FROM gate_entries WHERE id = ?", (row_id,))
        con.commit(); con.close()
    _invalidate_cache()
    log_activity("delete", "Gate Entry", f"ID {row_id}")


# ── Quality Control ───────────────────────────────────────────────────────────
def insert_quality(data):
    if _use_supabase(): _sb_insert("quality_control", data)
    else: _sqlite_insert("quality_control", data)
    _invalidate_cache()
    log_activity("create", "Quality Control", f"{data.get('product','')} · {data.get('test_date','')}")


@st.cache_data(ttl=30)
def get_quality():
    if _use_supabase():
        r = requests.get(
            _sb_url("quality_control"), headers=_headers(),
            params={"select": "*", "order": "test_date.desc,id.desc", "limit": 5000},
        )
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        return pd.DataFrame(data) if data else pd.DataFrame()
    else:
        try:
            con = _conn()
            df = pd.read_sql(
                "SELECT * FROM quality_control ORDER BY test_date DESC, id DESC", con)
            con.close()
            return df
        except Exception:
            return pd.DataFrame()


def update_quality(row_id, data):
    if _use_supabase():
        _sb_update("quality_control", row_id, data)
    else:
        con = _conn()
        sets = ", ".join(f"{k} = ?" for k in data)
        con.execute(f"UPDATE quality_control SET {sets} WHERE id = ?", list(data.values()) + [row_id])
        con.commit(); con.close()
    _invalidate_cache()
    log_activity("update", "Quality Control", f"ID {row_id}")


def bulk_insert_quality(records):
    """Insert multiple QC records. Returns count inserted."""
    if not records:
        return 0
    if _use_supabase():
        r = requests.post(
            _sb_url("quality_control"),
            headers={**_headers(), "Prefer": "return=minimal"},
            json=records,
        )
        if r.status_code not in (200, 201):
            raise Exception(f"Supabase bulk insert failed: {r.text}")
    else:
        for rec in records:
            _sqlite_insert("quality_control", rec)
    _invalidate_cache()
    return len(records)
