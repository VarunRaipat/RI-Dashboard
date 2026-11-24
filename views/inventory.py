import streamlit as st
import pandas as pd
from core.config import INVENTORY_ANCHOR_DATE, PLANTS
from core.inventory import finished_goods_summary, rm_summary, gate_tracked_balance
from core.ui import interactive_table, show_flashes, flash
from core.db import get_stock_adjustments, create_stock_adjustment, review_stock_adjustment
from core.permissions import has_permission
from core.tz import today_ist

LAKH = 100_000


def _render_plant_section(plant, can_export, show_value, can_export_rm):
    fg = finished_goods_summary(plant=plant)
    fg_disp = fg.copy()
    for col in ["Opening", "Produced", "Dispatched", "Adjusted", "Current Stock", "Value (₹)"]:
        fg_disp[col] = fg_disp[col].round(2)

    low_stock = fg_disp[fg_disp["Current Stock"] < 0]
    if not low_stock.empty:
        st.markdown(
            f'<div class="warn-box">⚠️ <b>{len(low_stock)} product(s) show negative stock</b> — '
            f'dispatched more than opening + produced. Check for missing production entries or a stale opening count.</div>',
            unsafe_allow_html=True,
        )

    fg_cols = ["Product", "Opening", "Produced", "Dispatched", "Adjusted", "Current Stock"]
    fg_sum_cols = ["Opening", "Produced", "Dispatched", "Adjusted", "Current Stock"]
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
    for col in ["Opening", "Received", "Consumed", "Adjusted", "Current Stock", "Value (₹)"]:
        rm_disp[col] = rm_disp[col].round(2)

    rm_cols = ["Material", "Opening", "Received", "Consumed", "Adjusted", "Current Stock"]
    rm_sum_cols = ["Opening", "Received", "Consumed", "Adjusted", "Current Stock"]
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
               f"Current stock = opening + production − dispatch ± admin-approved adjustments since that date. "
               f"Pipe Factory and Pole Factory each carry their own separate finished-goods and "
               f"Cement/GGBS stock.")

    _render_adjustments(role, locked_plant)

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


# ── Stock Adjustments ────────────────────────────────────────────────────────
# Operator enters the PHYSICAL count; the difference from the system balance
# is saved as a pending request and only changes stock once admin approves.

def _system_stock(plant, item_type, item_key):
    if item_type == "fg":
        df = finished_goods_summary(plant=plant)
        m = df[df["Product"] == item_key]
    else:
        from core.config import INVENTORY_MATERIAL_LABELS
        df = rm_summary(plant)
        m = df[df["Material"] == INVENTORY_MATERIAL_LABELS.get(item_key, item_key)]
    return float(m["Current Stock"].iloc[0]) if not m.empty else 0.0


def _render_adjustments(role, locked_plant):
    username = st.session_state.get("username")
    is_admin = role == "admin"
    can_request = is_admin or has_permission(username, role, "inventory", "add")
    df_adj = get_stock_adjustments()
    pending = df_adj[df_adj["status"] == "pending"]
    if locked_plant:
        pending = pending[pending["plant"] == locked_plant]

    if is_admin and not pending.empty:
        st.warning(f"📝 **{len(pending)} stock adjustment(s)** waiting for your approval.")
        _render_approval_queue(pending)

    if not can_request:
        return

    with st.expander("✏️ Adjust Stock (physical count)", expanded=False):
        st.caption("Enter the actual count found at the plant. The difference from the system stock "
                   + ("applies immediately (admin)." if is_admin
                      else "is sent to Admin and changes the stock only after approval."))
        from core.config import (INVENTORY_PRODUCTS, GATE_RM_TRACKED_ITEMS,
                                 INVENTORY_MATERIAL_LABELS, plant_for_product)
        c1, c2 = st.columns(2)
        if locked_plant:
            plant = locked_plant
            c1.text_input("Plant", value=locked_plant, disabled=True, key="adj_plant_locked")
        else:
            plant = c1.selectbox("Plant", PLANTS, key="adj_plant")
        kind = c2.radio("Stock type", ["Finished Goods", "Raw Material"], horizontal=True, key="adj_kind")
        if kind == "Finished Goods":
            item_type = "fg"
            options = [row[0] for row in INVENTORY_PRODUCTS if plant_for_product(row[0]) == plant]
            labels = {o: o for o in options}
        else:
            item_type = "rm"
            options = list(GATE_RM_TRACKED_ITEMS)
            labels = {o: INVENTORY_MATERIAL_LABELS.get(o, o) for o in options}
        if not options:
            st.info("No items for this plant.")
            return
        item_key = st.selectbox("Item", options, format_func=lambda o: labels[o],
                                key=f"adj_item_{item_type}_{plant}")

        system_qty = round(_system_stock(plant, item_type, item_key), 2)
        c3, c4 = st.columns(2)
        c3.metric("System stock", f"{system_qty:g}")
        physical = c4.number_input("Physical count", min_value=0.0, value=float(max(system_qty, 0)),
                                   step=1.0, key=f"adj_phys_{item_type}_{plant}_{item_key}")
        change = round(physical - system_qty, 2)
        if change:
            st.markdown(f"Adjustment: **{change:+g}** ({'excess found' if change > 0 else 'shortage'})")
        reason = st.text_input("Reason *", placeholder="e.g. breakage, count mismatch, damaged pipes",
                               key="adj_reason")
        if st.button("✅ Apply adjustment" if is_admin else "📨 Send for approval", type="primary",
                     use_container_width=True, key="adj_submit"):
            if not change:
                st.error("Physical count matches system stock — nothing to adjust.")
            elif not reason.strip():
                st.error("Please give a reason.")
            else:
                try:
                    create_stock_adjustment({
                        "date": str(today_ist()), "plant": plant, "item_type": item_type,
                        "item_key": item_key, "item_label": labels[item_key],
                        "system_qty": system_qty, "physical_qty": physical, "qty_change": change,
                        "reason": reason.strip(),
                    }, auto_approve=is_admin)
                    flash("✅ Stock adjusted." if is_admin else "📨 Adjustment sent — pending admin approval.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not save: {e}")

        mine = df_adj if is_admin else df_adj[df_adj["requested_by"] == username]
        if locked_plant:
            mine = mine[mine["plant"] == locked_plant]
        if not mine.empty:
            st.markdown("**Adjustment history**" if is_admin else "**My adjustment requests**")
            hist = mine.head(50)[["date", "plant", "item_label", "system_qty", "physical_qty", "qty_change",
                                  "reason", "status", "requested_by_name", "review_note"]].rename(columns={
                "date": "Date", "plant": "Plant", "item_label": "Item", "system_qty": "System",
                "physical_qty": "Physical", "qty_change": "Change", "reason": "Reason",
                "status": "Status", "requested_by_name": "By", "review_note": "Admin Note"})
            st.dataframe(hist, use_container_width=True, hide_index=True)


def _render_approval_queue(pending):
    for _, adj in pending.sort_values("created_at").iterrows():
        chg = float(adj["qty_change"] or 0)
        head = (f"{adj['plant']} · {adj['item_label']} · {chg:+g} · "
                f"by {adj.get('requested_by_name') or adj.get('requested_by')}")
        with st.expander(head):
            now_qty = round(_system_stock(adj["plant"], adj["item_type"], adj["item_key"]), 2)
            st.markdown(f"Counted on **{adj['date']}** — system showed **{float(adj['system_qty']):g}**, "
                        f"physical count **{float(adj['physical_qty']):g}** → change **{chg:+g}**.")
            st.markdown(f"Reason: _{adj['reason']}_")
            st.caption(f"System stock right now: {now_qty:g} → after approval: {now_qty + chg:g}")
            a1, a2 = st.columns(2)
            if a1.button("✅ Approve", type="primary", key=f"adj_appr_{adj['id']}", use_container_width=True):
                review_stock_adjustment(int(adj["id"]), True)
                flash("✅ Adjustment approved — stock updated.")
                st.rerun()
            note_key = f"adj_rej_note_{adj['id']}"
            a2.text_input("Note", key=note_key, label_visibility="collapsed", placeholder="Reason (optional)")
            if a2.button("❌ Reject", key=f"adj_rej_{adj['id']}", use_container_width=True):
                review_stock_adjustment(int(adj["id"]), False, st.session_state.get(note_key, ""))
                flash("Adjustment rejected.")
                st.rerun()
