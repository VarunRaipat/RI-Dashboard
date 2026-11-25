"""Role-based data visibility rules that can't be expressed as a simple
column filter (e.g. time-limited access after an order is fulfilled)."""
from core.tz import today_ist
from core.config import BOUNDARY_WALL_DISPATCH_SKUS
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

    today = pd.Timestamp(today_ist())
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


def di_order_products(di_no, df_orders):
    """{product: qty_ordered} for the given DI No.'s Sales Order, or None if
    no Sales Order at all has this DI No. — used to catch a typo'd/reused
    DI No. on a Dispatch entry before it silently misattributes value to
    an unrelated order (see di_dispatch_warnings)."""
    di_no = str(di_no or "").strip()
    if not di_no or df_orders is None or df_orders.empty or "di_no" not in df_orders.columns:
        return None
    rows = df_orders[df_orders["di_no"].astype(str).str.strip() == di_no]
    if rows.empty:
        return None
    return rows.groupby("product")["qty_ordered"].sum().to_dict()


def di_dispatched_qty(di_no, product, df_disp):
    """Total already dispatched for this DI No. + product, from Dispatch
    history (excludes the challan currently being entered, since that
    hasn't been saved yet)."""
    di_no = str(di_no or "").strip()
    if df_disp is None or df_disp.empty or "di_no" not in df_disp.columns:
        return 0.0
    rows = df_disp[
        (df_disp["di_no"].astype(str).str.strip() == di_no) & (df_disp["product"] == product)
    ]
    return float(rows["qty_dispatched"].sum()) if not rows.empty else 0.0


def di_dispatch_warnings(di_no, products, df_orders, df_disp):
    """Sanity-check a DI No. being typed into a new Dispatch challan against
    the Sales Order it's supposed to reference. Returns a list of warning
    strings (empty if everything lines up) — never blocks the entry, since
    a legitimate dispatch can predate its Sales Order being entered (or the
    order may be legacy data); it's on the operator to judge after seeing
    the warning. This is what would have caught DI 25 being typo'd onto an
    unrelated KMV Projects challan instead of its real DI 250."""
    di_no = str(di_no or "").strip()
    if not di_no:
        return []
    ordered = di_order_products(di_no, df_orders)
    if ordered is None:
        return [f"No Sales Order found for DI {di_no} — double-check the number. "
                f"A typo here silently attributes this dispatch's value to whatever "
                f"order (if any) happens to reuse that DI No."]

    warnings = []
    for prod in dict.fromkeys(p for p in products if p):
        if prod not in ordered:
            # A Boundary Wall order is never dispatched as "Boundary Wall" —
            # it's fulfilled by dispatching the Slab/Pillar SKUs that make up
            # the wall (see core.config's BOUNDARY_WALL_* comments), so that's
            # not a mismatch worth warning about.
            if "Boundary Wall" in ordered and prod in BOUNDARY_WALL_DISPATCH_SKUS:
                continue
            warnings.append(f"DI {di_no}'s Sales Order doesn't include \"{prod}\" — check the product or DI No.")
            continue
        o_qty = ordered[prod]
        d_qty = di_dispatched_qty(di_no, prod, df_disp)
        if o_qty > 0 and d_qty >= o_qty:
            warnings.append(f"DI {di_no} · \"{prod}\" is already fully dispatched ({int(d_qty):,}/{int(o_qty):,}).")
    return warnings


def di_product_balance(df_orders, df_disp):
    """One row per (DI No., product) with ordered / dispatched / pending qty
    and value — the per-product balance a multi-product DI needs (the DI-level
    pipeline only shows one pooled qty). Duplicate order lines for the same
    product are summed first so dispatches aren't double-counted. Boundary
    Wall lines are left out: they're fulfilled by dispatching Slab/Pillar
    SKUs, so a sqft-vs-pieces balance would be meaningless."""
    cols = ["di_no", "order_date", "client_name", "product", "qty_ordered",
            "dispatched_qty", "pending_qty", "ordered_value", "dispatched_value",
            "pending_value", "challans", "on_di", "extra_qty"]
    if df_orders is None or df_orders.empty or "di_no" not in df_orders.columns:
        return pd.DataFrame(columns=cols)
    o = df_orders[df_orders["product"].notna()].copy()
    o["di_no"] = o["di_no"].astype(str).str.strip()
    o = o[~o["di_no"].isin(["", "None", "nan"])]
    bw_dis = set(o.loc[o["product"] == "Boundary Wall", "di_no"])
    o = o[o["product"] != "Boundary Wall"]
    o["qty_ordered"]  = pd.to_numeric(o["qty_ordered"], errors="coerce").fillna(0)
    o["total_amount"] = pd.to_numeric(o.get("total_amount"), errors="coerce").fillna(0)
    bal = o.groupby(["di_no", "product"], sort=False).agg(
        order_date=("order_date", "min"),
        client_name=("client_name", "first"),
        qty_ordered=("qty_ordered", "sum"),
        ordered_value=("total_amount", "sum"),
    ).reset_index()

    if df_disp is not None and not df_disp.empty and "di_no" in df_disp.columns:
        d = df_disp[df_disp["di_no"].notna()].copy()
        d["di_no"] = d["di_no"].astype(str).str.strip()
        d["qty_dispatched"] = pd.to_numeric(d["qty_dispatched"], errors="coerce").fillna(0)
        d["dispatch_value"] = pd.to_numeric(d.get("dispatch_value"), errors="coerce").fillna(0)
        dg = d.groupby(["di_no", "product"]).agg(
            dispatched_qty=("qty_dispatched", "sum"),
            dispatched_value=("dispatch_value", "sum"),
            challans=("challan_no", lambda x: ", ".join(x.dropna().astype(str).unique())),
        ).reset_index()
        # Outer merge: a challan booked against this DI for a product the
        # DI doesn't list (other size / joint type) must still show up,
        # otherwise the per-product view hides it and won't reconcile with
        # the DI-level pooled Pending Qty. Boundary Wall DIs are skipped —
        # they're delivered as Slab/Pillar/caps, never as their own SKU.
        dg = dg[dg["di_no"].isin(set(bal["di_no"])) & ~dg["di_no"].isin(bw_dis)]
        bal = bal.merge(dg, on=["di_no", "product"], how="outer", indicator=True)
        bal["on_di"] = bal.pop("_merge") != "right_only"
        hdr = o.groupby("di_no").agg(_d=("order_date", "min"), _c=("client_name", "first"))
        bal["order_date"]  = bal["order_date"].fillna(bal["di_no"].map(hdr["_d"]))
        bal["client_name"] = bal["client_name"].fillna(bal["di_no"].map(hdr["_c"]))
    if "on_di" not in bal.columns:
        bal["on_di"] = True
    for c in ("qty_ordered", "ordered_value", "dispatched_qty", "dispatched_value"):
        bal[c] = bal[c].fillna(0) if c in bal.columns else 0.0
    bal["challans"] = bal["challans"].fillna("") if "challans" in bal.columns else ""

    bal["pending_qty"] = (bal["qty_ordered"] - bal["dispatched_qty"]).clip(lower=0)
    bal["extra_qty"]   = (bal["dispatched_qty"] - bal["qty_ordered"]).clip(lower=0)
    rate = (bal["ordered_value"] / bal["qty_ordered"].where(bal["qty_ordered"] > 0)).fillna(0)
    bal["pending_value"] = bal["pending_qty"] * rate
    return bal[cols]


def di_balance_text(bal, di_no):
    """'NP3 600mm: 12 left · Slab 7': done' for one DI — short enough for a
    table cell or a caption under the DI No. field."""
    rows = bal[bal["di_no"] == str(di_no or "").strip()]
    parts = []
    for _, r in rows.iterrows():
        left, extra = int(round(r["pending_qty"])), int(round(r["extra_qty"]))
        if not r["on_di"]:
            parts.append(f"⚠️ {r['product']}: {extra:,} sent (not on DI)")
        elif left > 0:
            parts.append(f"{r['product']}: {left:,} left")
        elif extra > 0:
            parts.append(f"{r['product']}: ✓ done (+{extra:,} extra)")
        else:
            parts.append(f"{r['product']}: ✓ done")
    return " · ".join(parts)
