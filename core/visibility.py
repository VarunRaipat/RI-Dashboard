"""Role-based data visibility rules that can't be expressed as a simple
column filter (e.g. time-limited access after an order is fulfilled)."""
from datetime import date
import pandas as pd

SALE_B_GRACE_DAYS = 5


def hidden_sale_b_dis(df_orders, df_disp, grace_days=SALE_B_GRACE_DAYS):
    """DI numbers that are Sale B and fully dispatched, where the last
    dispatch happened more than `grace_days` days ago.

    Headoffice is allowed to see Sale B orders/dispatch while a DI is
    pending or partially dispatched, and for a grace period after it's
    fully dispatched — after that the DI should disappear from their view.
    """
    if df_orders is None or df_orders.empty or "sale_type" not in df_orders.columns or "di_no" not in df_orders.columns:
        return set()

    b_orders = df_orders[df_orders["sale_type"] == "Sale B"].copy()
    if b_orders.empty:
        return set()

    b_orders["di_no"] = b_orders["di_no"].astype(str).str.strip()
    ordered_qty = b_orders.groupby("di_no")["qty_ordered"].sum()

    if df_disp is not None and not df_disp.empty and "di_no" in df_disp.columns:
        disp_b = df_disp.copy()
        disp_b["di_no"] = disp_b["di_no"].astype(str).str.strip()
        disp_b = disp_b[disp_b["di_no"].isin(set(ordered_qty.index))]
        disp_b["date"] = pd.to_datetime(disp_b["date"], errors="coerce")
        dispatched_qty = disp_b.groupby("di_no")["qty_dispatched"].sum()
        last_dispatch  = disp_b.groupby("di_no")["date"].max()
    else:
        dispatched_qty = pd.Series(dtype=float)
        last_dispatch  = pd.Series(dtype="datetime64[ns]")

    today = pd.Timestamp(date.today())
    hidden = set()
    for di_no, o_qty in ordered_qty.items():
        d_qty = dispatched_qty.get(di_no, 0) or 0
        pending = o_qty - d_qty
        fulfilled = d_qty > 0 and pending <= 1
        if not fulfilled:
            continue
        last_dt = last_dispatch.get(di_no)
        if pd.isna(last_dt) or (today - last_dt).days > grace_days:
            hidden.add(di_no)
    return hidden
