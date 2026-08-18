"""
One-off migration: apply the new Concrete rate (Rs. 3500/m3, was 2500) to
every existing production (DPR) row, not just future entries. Recomputes
concrete_cost, rm_cost, misc_cost, total_cost, profit, profit_pct from each
row's already-stored concrete_qty/steel_cost/production_cost/
loading_unloading_cost/welding_cost/jalli_cost/revenue, using the same
formula as core.calculations.calculate_production. Also inserts a new
rm_prices row so future DPR entries pick up 3500 too.

Run once: python scripts/recalc_concrete_rate.py
"""
import sys
import tomllib
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

ROOT = Path(__file__).parent.parent
NEW_CONCRETE_RATE = 3500.0
MISC_PCT = 10.0  # core.config.MISC_PCT

secrets = tomllib.loads((ROOT / ".streamlit" / "secrets.toml").read_text())
SUPABASE_URL = secrets["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = secrets["SUPABASE_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def today_ist():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def get_current_rm_prices():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/rm_prices",
        headers=HEADERS,
        params={"select": "*", "order": "created_at.desc", "limit": 1},
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {"concrete": 2500.0, "steel": 0.0}


def save_new_rm_price(steel_price):
    data = {"effective_date": str(today_ist()), "concrete": NEW_CONCRETE_RATE, "steel": steel_price}
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/rm_prices",
        headers={**HEADERS, "Prefer": "return=minimal"},
        json=data,
    )
    if r.status_code not in (200, 201):
        raise SystemExit(f"Failed to save new rm_prices row: {r.status_code} {r.text}")
    print(f"Saved new rm_prices row: concrete={NEW_CONCRETE_RATE}, steel={steel_price}, effective_date={data['effective_date']}")


def get_all_production():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/production",
        headers=HEADERS,
        params={"select": "*", "order": "id.asc", "limit": 10000},
    )
    r.raise_for_status()
    return r.json()


def patch_production(row_id, data):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/production",
        headers={**HEADERS, "Prefer": "return=minimal"},
        params={"id": f"eq.{row_id}"},
        json=data,
    )
    if r.status_code not in (200, 204):
        raise SystemExit(f"Failed to update production id={row_id}: {r.status_code} {r.text}")


def recompute(row):
    concrete_qty = float(row.get("concrete_qty") or 0)
    steel_cost = float(row.get("steel_cost") or 0)
    production_cost = float(row.get("production_cost") or 0)
    loading_unloading_cost = float(row.get("loading_unloading_cost") or 0)
    welding_cost = float(row.get("welding_cost") or 0)
    jalli_cost = float(row.get("jalli_cost") or 0)
    revenue = float(row.get("revenue") or 0)

    concrete_cost = round(concrete_qty * NEW_CONCRETE_RATE, 2)
    rm_cost = round(concrete_cost + steel_cost, 2)
    misc_cost = round(rm_cost * (MISC_PCT / 100), 2)
    sub_total = rm_cost + production_cost + loading_unloading_cost + welding_cost + jalli_cost
    total_cost = round(sub_total + misc_cost, 2)
    profit = round(revenue - total_cost, 2)
    profit_pct = round((profit / revenue * 100) if revenue > 0 else 0, 2)

    return {
        "concrete_cost": concrete_cost,
        "rm_cost": rm_cost,
        "misc_cost": misc_cost,
        "total_cost": total_cost,
        "profit": profit,
        "profit_pct": profit_pct,
    }


def main():
    current = get_current_rm_prices()
    steel_price = float(current.get("steel") or 0)
    save_new_rm_price(steel_price)

    rows = get_all_production()
    print(f"Fetched {len(rows)} production rows.")

    updated = 0
    skipped = 0
    for row in rows:
        new_fields = recompute(row)
        changed = any(round(float(row.get(k) or 0), 2) != new_fields[k] for k in new_fields)
        if not changed:
            skipped += 1
            continue
        patch_production(row["id"], new_fields)
        updated += 1

    print(f"Updated {updated} rows, {skipped} already matched (no change needed).")


if __name__ == "__main__":
    main()
