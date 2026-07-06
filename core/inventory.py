"""Inventory balance calculations for finished goods and raw materials,
rolled forward from a fixed opening count taken on INVENTORY_ANCHOR_DATE.
Current stock = opening + in - out since that date.
"""
import pandas as pd
from core.config import (INVENTORY_PRODUCTS, INVENTORY_ANCHOR_DATE, RM_INVENTORY_OPENING,
                          PRODUCT_CONFIG, INVENTORY_MATERIAL_LABELS,
                          GATE_UNTRACKED_ITEMS, GATE_RM_TRACKED_ITEMS)
from core.db import get_production, get_dispatch, get_rm_prices, get_gate_entries, get_rm_usage

_ANCHOR = pd.Timestamp(INVENTORY_ANCHOR_DATE)

_RM_LABEL = INVENTORY_MATERIAL_LABELS

# Steel's consumption is computed automatically (Nos x product's steel_kg_per_unit,
# summed from the production table's steel_qty column). Cement/GGBS aren't tied to
# any single product — they're the day's total batch usage entered once per DPR
# submission (see views/dpr.py), summed from the rm_usage table instead.
_PRODUCTION_CONSUME_COL = {"steel": "steel_qty"}
_RM_USAGE_CONSUME_COL   = {"cement_ppc": "cement_bags", "ggbs": "ggbs_bags"}


def _since_anchor(df, date_col="date"):
    if df is None or df.empty:
        return df
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    return df[df[date_col] >= _ANCHOR]


def _matches_product(series, disp_name):
    """disp_name is normally one SKU, but can be a tuple of SKUs that all
    draw down the same physical stock (e.g. an NP4 pipe sold against its
    matching NP3 SKU's inventory) — handle both."""
    if isinstance(disp_name, (list, tuple, set)):
        return series.isin(disp_name)
    return series == disp_name


def finished_goods_summary():
    """Current stock — and its value at selling price — for every product
    in INVENTORY_PRODUCTS, as of today."""
    df_prod = _since_anchor(get_production())
    df_disp = _since_anchor(get_dispatch())

    rows = []
    for canonical, prod_name, disp_name, opening in INVENTORY_PRODUCTS:
        produced = float(df_prod.loc[df_prod["product"] == prod_name, "nos"].sum()) \
            if prod_name and df_prod is not None and not df_prod.empty else 0.0
        dispatched = float(df_disp.loc[_matches_product(df_disp["product"], disp_name), "qty_dispatched"].sum()) \
            if df_disp is not None and not df_disp.empty else 0.0
        current_stock = opening + produced - dispatched
        selling_price = PRODUCT_CONFIG.get(prod_name, {}).get("selling_price", 0) if prod_name else 0
        rows.append({
            "Product": canonical,
            "Opening": opening,
            "Produced": produced,
            "Dispatched": dispatched,
            "Current Stock": current_stock,
            "Value (₹)": current_stock * selling_price,
        })
    return pd.DataFrame(rows)


def rm_summary():
    """Current stock — and its value at RM cost price — for Steel, Cement,
    and GGBS. "Received" comes from Gate Entry ("In" log rows for that item).
    "Consumed" comes from the production table for Steel (Nos x the
    product's fixed per-unit figure), or from the rm_usage table for
    Cement/GGBS (the day's total batch usage, entered once per DPR
    submission rather than tied to a single product)."""
    df_prod = _since_anchor(get_production())
    df_usage = _since_anchor(get_rm_usage())
    df_gate = _since_anchor(get_gate_entries())
    rm_prices = get_rm_prices()

    rows = []
    for material, opening in RM_INVENTORY_OPENING.items():
        if material in _PRODUCTION_CONSUME_COL:
            col = _PRODUCTION_CONSUME_COL[material]
            consumed = float(df_prod[col].sum()) if df_prod is not None and not df_prod.empty and col in df_prod.columns else 0.0
        else:
            col = _RM_USAGE_CONSUME_COL.get(material)
            consumed = float(df_usage[col].sum()) if col and df_usage is not None and not df_usage.empty and col in df_usage.columns else 0.0
        received = 0.0
        if df_gate is not None and not df_gate.empty and "item" in df_gate.columns:
            in_mask = (df_gate["item"] == material) & (df_gate["direction"] == "In")
            received = float(pd.to_numeric(df_gate.loc[in_mask, "qty"], errors="coerce").fillna(0).sum())
        current_stock = opening + received - consumed
        price_per_kg = rm_prices.get(material, 0)
        rows.append({
            "Material": _RM_LABEL.get(material, material),
            "Opening": opening,
            "Received": received,
            "Consumed": consumed,
            "Current Stock": current_stock,
            "Value (₹)": current_stock * price_per_kg,
        })
    return pd.DataFrame(rows)


def daily_breakdown(canonical_name, start, end):
    """Day-by-day opening/produced/dispatched/closing for one finished-good
    product, for the given [start, end] date range (clamped to the anchor)."""
    match = next((row for row in INVENTORY_PRODUCTS if row[0] == canonical_name), None)
    if not match:
        return pd.DataFrame()
    _, prod_name, disp_name, opening = match

    df_prod = get_production()
    df_disp = get_dispatch()

    daily_prod = pd.Series(dtype=float)
    if prod_name and not df_prod.empty:
        df_prod = df_prod.copy()
        df_prod["date"] = pd.to_datetime(df_prod["date"], errors="coerce")
        df_prod = df_prod[df_prod["product"] == prod_name]
        if not df_prod.empty:
            daily_prod = df_prod.groupby("date")["nos"].sum()

    daily_disp = pd.Series(dtype=float)
    if not df_disp.empty:
        df_disp = df_disp.copy()
        df_disp["date"] = pd.to_datetime(df_disp["date"], errors="coerce")
        df_disp = df_disp[_matches_product(df_disp["product"], disp_name)]
        if not df_disp.empty:
            daily_disp = df_disp.groupby("date")["qty_dispatched"].sum()

    start_ts = max(pd.Timestamp(start), _ANCHOR)
    end_ts   = pd.Timestamp(end)
    if end_ts < start_ts:
        return pd.DataFrame()

    running = opening
    if start_ts > _ANCHOR:
        for d in pd.date_range(_ANCHOR, start_ts - pd.Timedelta(days=1), freq="D"):
            running += daily_prod.get(d, 0) - daily_disp.get(d, 0)

    rows = []
    for d in pd.date_range(start_ts, end_ts, freq="D"):
        produced   = daily_prod.get(d, 0)
        dispatched = daily_disp.get(d, 0)
        closing    = running + produced - dispatched
        rows.append({"Date": d, "Opening": running, "Produced": produced,
                     "Dispatched": dispatched, "Closing": closing})
        running = closing

    return pd.DataFrame(rows)


def gate_tracked_balance():
    """Running In/Out balance for gate-logged items that aren't in the
    untracked bulk raw-material list, and aren't OPC 53/GGBS (which get their
    own specialised balance in rm_summary()) — i.e. Plant Equipment & Parts
    and Miscellaneous Parts, grouped by item + unit (quantities in different
    units for the same item are kept separate rather than summed together).
    """
    cols = ["Category", "Item", "Unit", "In", "Out", "Balance"]
    df = get_gate_entries()
    if df.empty or "item" not in df.columns:
        return pd.DataFrame(columns=cols)

    excluded = set(GATE_UNTRACKED_ITEMS) | set(GATE_RM_TRACKED_ITEMS)
    df = df[~df["item"].isin(excluded)].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)

    df["qty"]  = pd.to_numeric(df["qty"], errors="coerce").fillna(0)
    df["unit"] = df["unit"].fillna("")
    df["item"] = df["item"].fillna("(unspecified)")

    grouped = df.groupby(["category", "item", "unit", "direction"])["qty"].sum().unstack(fill_value=0)
    grouped = grouped.reset_index()
    for d in ("In", "Out"):
        if d not in grouped.columns:
            grouped[d] = 0
    grouped["Balance"] = grouped["In"] - grouped["Out"]
    grouped = grouped.rename(columns={"category": "Category", "item": "Item", "unit": "Unit"})
    return grouped[cols]
