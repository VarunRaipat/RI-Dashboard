import streamlit as st
import pandas as pd
from core.config import INVENTORY_ANCHOR_DATE, PLANTS
from core.inventory import finished_goods_summary, rm_summary, gate_tracked_balance
from core.ui import interactive_table, show_flashes

LAKH = 100_000


def _render_plant_section(plant, can_export, show_value, can_export_rm):
    fg = finished_goods_summary(plant=plant)
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
        st.metric(f"{plant} — Finished Goods Value", f"₹{total_value/LAKH:.2f}L")
        fg_cols = fg_cols + ["Value (₹)"]
        fg_sum_cols = fg_sum_cols + ["Value (₹)"]

    interactive_table(fg_disp, key=f"inv_fg_{plant}", sum_cols=fg_sum_cols, show_cols=fg_cols,
                      show_export=can_export)

    st.markdown("---")
    st.markdown('<div class="section-header">Raw Materials — Cement / GGBS / Sand</div>', unsafe_allow_html=True)
    st.caption(f"{plant}'s own Cement/GGBS/Sand balance — received via Gate Entry (tagged {plant}), "
               "consumed via this plant's Production Entries.")

    rm = rm_summary(plant)
    rm_disp = rm.copy()
    for col in ["Opening", "Received", "Consumed", "Current Stock", "Value (₹)"]:
        rm_disp[col] = rm_disp[col].round(2)

    rm_cols = ["Material", "Opening", "Received", "Consumed", "Current Stock"]
    rm_sum_cols = ["Opening", "Received", "Consumed", "Current Stock"]
    if show_value:
        rm_cols = rm_cols + ["Value (₹)"]
        rm_sum_cols = rm_sum_cols + ["Value (₹)"]

    interactive_table(rm_disp, key=f"inv_rm_{plant}", sum_cols=rm_sum_cols, show_cols=rm_cols,
                      show_export=can_export_rm)


def show(PLOT):
    show_flashes()
    role = st.session_state.get("role", "dispatch")
    can_export = role not in ("dispatch", "factory")
    can_export_rm = role != "dispatch"  # factory operator can export the Raw Material table specifically
    show_value = role not in ("dispatch", "factory")  # dispatch/factory see quantities only, no ₹ value
    locked_plant = st.session_state.get("plant")

    st.markdown(f"""
    <div class="page-title">🏭 Inventory{f' — {locked_plant}' if locked_plant else ''}</div>
    <div class="page-subtitle">Finished goods &amp; raw material stock — live balance, per plant</div>
    """, unsafe_allow_html=True)
    st.caption(f"Opening stock counted on {pd.Timestamp(INVENTORY_ANCHOR_DATE).strftime('%d-%b-%Y')}. "
               f"Current stock = opening + production − dispatch since that date. "
               f"Pipe Factory and Pole Factory each carry their own separate finished-goods and "
               f"Cement/GGBS stock.")

    if locked_plant:
        _render_plant_section(locked_plant, can_export, show_value, can_export_rm)
    else:
        tabs = st.tabs([f"🔵 {PLANTS[0]}", f"⚙️ {PLANTS[1]}"] if len(PLANTS) >= 2 else [f"🏭 {p}" for p in PLANTS])
        for tab, plant in zip(tabs, PLANTS):
            with tab:
                _render_plant_section(plant, can_export, show_value, can_export_rm)

    # ── Plant Equipment & Misc Parts (from Gate Entry log) ─────────────────────
    st.markdown("---")
    st.markdown('<div class="section-header">Plant Equipment &amp; Misc Parts</div>', unsafe_allow_html=True)
    st.caption("Running In/Out balance from Gate Entry, for everything besides the raw materials tracked above.")

    def _render_equipment(plant):
        eq = gate_tracked_balance(plant=plant)
        if eq.empty:
            st.info("No equipment/parts movement logged yet.")
        else:
            eq_disp = eq.copy()
            for col in ["In", "Out", "Balance"]:
                eq_disp[col] = eq_disp[col].round(2)
            interactive_table(eq_disp, key=f"inv_gate_eq_{plant or 'locked'}",
                              sum_cols=["In", "Out", "Balance"],
                              show_cols=["Category", "Item", "Unit", "In", "Out", "Balance"],
                              show_export=can_export)

    if locked_plant:
        _render_equipment(locked_plant)
    else:
        eq_tabs = st.tabs([f"🔵 {PLANTS[0]}", f"⚙️ {PLANTS[1]}"] if len(PLANTS) >= 2 else [f"🏭 {p}" for p in PLANTS])
        for tab, plant in zip(eq_tabs, PLANTS):
            with tab:
                _render_equipment(plant)
