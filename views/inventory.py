import streamlit as st
import pandas as pd
from datetime import date, timedelta
from core.config import INVENTORY_PRODUCTS, INVENTORY_ANCHOR_DATE, RM_INVENTORY_OPENING, INVENTORY_MATERIAL_LABELS
from core.inventory import finished_goods_summary, rm_summary, daily_breakdown, gate_tracked_balance
from core.db import get_inventory_opening, save_inventory_opening
from core.ui import interactive_table, show_flashes

LAKH = 100_000


def show(PLOT):
    show_flashes()
    role = st.session_state.get("role", "dispatch")
    name = st.session_state.get("name", "")
    can_export = role not in ("dispatch", "factory")
    show_value = role not in ("dispatch", "factory")  # dispatch/factory see quantities only, no ₹ value
    can_set_opening = role in ("admin", "factory")

    st.markdown("""
    <div class="page-title">🏭 Inventory</div>
    <div class="page-subtitle">Finished goods &amp; raw material stock — live balance</div>
    """, unsafe_allow_html=True)
    st.caption(f"Opening stock counted on {pd.Timestamp(INVENTORY_ANCHOR_DATE).strftime('%d-%b-%Y')}. "
               f"Current stock = opening + production − dispatch since that date.")

    if can_set_opening:
        with st.expander("✏️ Set Opening Stock (one-time physical count)"):
            st.caption(
                "Enter the physical stock count for a product/material once — Current Stock then "
                "rolls forward automatically from Production/Dispatch or Gate Entry. Come back here "
                "only if you do a fresh physical recount."
            )
            db_opening = get_inventory_opening()
            kind = st.radio("Type", ["Finished Good", "Raw Material"], horizontal=True, key="inv_open_kind")

            if kind == "Finished Good":
                item_keys = [row[0] for row in INVENTORY_PRODUCTS]
                st.caption("Enter the physical count for each pipe/product, then Save All. Rows left unchanged are skipped.")
                bulk_df = pd.DataFrame({
                    "Product": item_keys,
                    "Opening Stock": [db_opening.get(k, {}).get("qty", 0.0) for k in item_keys],
                })
                edited = st.data_editor(
                    bulk_df, hide_index=True, use_container_width=True,
                    column_config={
                        "Product": st.column_config.TextColumn(disabled=True),
                        "Opening Stock": st.column_config.NumberColumn(min_value=0.0, step=1.0),
                    },
                    key="inv_open_bulk_editor",
                )
                if st.button("💾 Save All Opening Stock", key="inv_open_bulk_save"):
                    changed = 0
                    for _, row in edited.iterrows():
                        item_key = row["Product"]
                        new_qty = float(row["Opening Stock"])
                        existing_qty = db_opening.get(item_key, {}).get("qty", 0.0)
                        if new_qty != existing_qty:
                            save_inventory_opening(item_key, "finished_good", new_qty, updated_by=name)
                            changed += 1
                    if changed:
                        st.success(f"✅ Saved opening stock for {changed} product(s).")
                        st.rerun()
                    else:
                        st.info("No changes to save.")
            else:
                item_keys = list(RM_INVENTORY_OPENING.keys())
                labels = {k: INVENTORY_MATERIAL_LABELS.get(k, k) for k in item_keys}
                sel_item = st.selectbox("Item", item_keys, format_func=lambda k: labels.get(k, k), key="inv_open_item")
                existing = db_opening.get(sel_item)
                current_val = existing["qty"] if existing else 0.0
                if existing:
                    st.caption(f"Currently set to **{current_val:,.2f}** — last updated by {existing['updated_by'] or 'unknown'} on {existing['updated_at']}.")
                else:
                    st.caption("Not set yet — defaults to 0.")

                new_val = st.number_input(f"Opening stock — {labels.get(sel_item, sel_item)}",
                                           min_value=0.0, value=float(current_val), step=1.0,
                                           key=f"inv_open_val_{sel_item}")
                if st.button("💾 Save Opening Stock", key="inv_open_save"):
                    save_inventory_opening(sel_item, "raw_material", new_val, updated_by=name)
                    st.success(f"✅ Opening stock for {labels.get(sel_item, sel_item)} set to {new_val:,.2f}.")
                    st.rerun()

    # ── Finished Goods ─────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Finished Goods</div>', unsafe_allow_html=True)

    fg = finished_goods_summary()
    fg_disp = fg.copy()
    for col in ["Opening", "Produced", "Dispatched", "Current Stock", "Value (₹)"]:
        fg_disp[col] = fg_disp[col].round(2)

    low_stock = fg_disp[fg_disp["Current Stock"] < 0]
    if not low_stock.empty:
        st.markdown(
            f'<div class="warn-box">⚠️ <b>{len(low_stock)} product(s) show negative stock</b> — '
            f'dispatched more than opening + produced. Check for missing production entries or a stale opening count.</div>',
            unsafe_allow_html=True,
        )

    fg_cols = ["Product", "Opening", "Produced", "Dispatched", "Current Stock"]
    fg_sum_cols = ["Opening", "Produced", "Dispatched", "Current Stock"]
    if show_value:
        total_value = fg_disp["Value (₹)"].sum()
        st.metric("Total Finished Goods Value", f"₹{total_value/LAKH:.2f}L")
        fg_cols = fg_cols + ["Value (₹)"]
        fg_sum_cols = fg_sum_cols + ["Value (₹)"]

    interactive_table(fg_disp, key="inv_fg", sum_cols=fg_sum_cols, show_cols=fg_cols,
                      show_export=can_export)

    # ── Day-by-day drill-down ──────────────────────────────────────────────────
    st.markdown("---")
    st.markdown('<div class="section-header">Day-by-Day Detail</div>', unsafe_allow_html=True)

    products = [row[0] for row in INVENTORY_PRODUCTS]
    d1, d2, d3 = st.columns([2, 1, 1])
    sel_product = d1.selectbox("Product", products, key="inv_detail_product")
    anchor = pd.Timestamp(INVENTORY_ANCHOR_DATE).date()
    start_default = max(anchor, date.today() - timedelta(days=6))
    detail_start = d2.date_input("From", value=start_default, min_value=anchor, key="inv_detail_start")
    detail_end   = d3.date_input("To",   value=date.today(),  min_value=anchor, key="inv_detail_end")

    daily = daily_breakdown(sel_product, detail_start, detail_end)
    if daily.empty:
        st.info("No data for this range.")
    else:
        daily_disp = daily.copy()
        for col in ["Opening", "Produced", "Dispatched", "Closing"]:
            daily_disp[col] = daily_disp[col].round(2)
        interactive_table(daily_disp, key="inv_daily",
                          sum_cols=["Produced", "Dispatched"],
                          show_cols=["Date", "Opening", "Produced", "Dispatched", "Closing"],
                          col_config={"Date": st.column_config.DateColumn("Date", format="DD-MMM-YYYY")},
                          show_export=can_export)

    # ── Raw Materials ────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown('<div class="section-header">Raw Materials</div>', unsafe_allow_html=True)
    st.caption("Received quantities come from Gate Entry (\"In\" log rows for each material); "
               "consumed comes from Production Entry.")

    rm = rm_summary()
    rm_disp = rm.copy()
    for col in ["Opening", "Received", "Consumed", "Current Stock", "Value (₹)"]:
        rm_disp[col] = rm_disp[col].round(2)

    rm_cols = ["Material", "Opening", "Received", "Consumed", "Current Stock"]
    rm_sum_cols = ["Opening", "Received", "Consumed", "Current Stock"]
    if show_value:
        rm_cols = rm_cols + ["Value (₹)"]
        rm_sum_cols = rm_sum_cols + ["Value (₹)"]

    interactive_table(rm_disp, key="inv_rm", sum_cols=rm_sum_cols, show_cols=rm_cols,
                      show_export=can_export)

    # ── Plant Equipment & Misc Parts (from Gate Entry log) ─────────────────────
    st.markdown("---")
    st.markdown('<div class="section-header">Plant Equipment &amp; Misc Parts</div>', unsafe_allow_html=True)
    st.caption("Running In/Out balance from Gate Entry, for everything besides the "
               "raw materials tracked above.")

    eq = gate_tracked_balance()
    if eq.empty:
        st.info("No equipment/parts movement logged yet.")
    else:
        eq_disp = eq.copy()
        for col in ["In", "Out", "Balance"]:
            eq_disp[col] = eq_disp[col].round(2)
        interactive_table(eq_disp, key="inv_gate_eq",
                          sum_cols=["In", "Out", "Balance"],
                          show_cols=["Category", "Item", "Unit", "In", "Out", "Balance"],
                          show_export=can_export)
