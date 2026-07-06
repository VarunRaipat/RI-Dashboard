import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date
from core.config import PRODUCTION_PRODUCTS, PRODUCT_CONFIG, RAW_MATERIALS, PLANTS, SKU_TO_PRICING_KEY
from core.calculations import calculate_production
from core.db import insert_production, get_rm_prices, get_production, delete_row, update_production, get_product_config
from core.ui import flash, show_flashes

_RM_COST_FIELDS = [
    "rm_cost","labour_cost","transport_cost","power_cost",
    "emi_cost","dg_cost","admin_cost","misc_cost","total_cost","revenue","profit","profit_pct",
    "total_wt_kg",
] + [f"pct_{m['key']}" for m in RAW_MATERIALS] + [f"{m['key']}_qty" for m in RAW_MATERIALS]


def show(PLOT):
    role = st.session_state.get("role", "production")
    show_flashes()

    st.markdown("""
    <div class="page-title">📋 Daily Production Report</div>
    <div class="page-subtitle">Enter production data · costs auto-calculated</div>
    """, unsafe_allow_html=True)

    rm = get_rm_prices()
    prod_cfg = get_product_config()

    with st.form("dpr_form", clear_on_submit=True):
        st.markdown('<div class="section-header">Basic Info</div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        entry_date    = c1.date_input("Date", date.today())
        product       = c2.selectbox("Product", PRODUCTION_PRODUCTS)
        nos           = c3.number_input("Production (Nos.)", min_value=0, step=100)

        plant = st.radio("Plant", PLANTS, horizontal=True)

        st.markdown('<div class="section-header">Raw Materials Used</div>', unsafe_allow_html=True)
        rm_inputs = {}
        rm_cols = st.columns(min(len(RAW_MATERIALS), 4))
        for i, m in enumerate(RAW_MATERIALS):
            step = 0.5 if m["unit"] == "Bags" else 10.0
            rm_inputs[m["key"]] = rm_cols[i % len(rm_cols)].number_input(
                f"{m['label']} ({m['unit']})", min_value=0.0, step=step, key=f"dpr_rm_{m['key']}"
            )

        submitted = st.form_submit_button("✅ Submit & Calculate", type="primary", use_container_width=True)

    if submitted:
        if nos <= 0:
            st.error("Production (Nos.) must be greater than 0.")
            return

        pricing_key = SKU_TO_PRICING_KEY.get(product, product)
        result = calculate_production(pricing_key, nos, rm_inputs, rm, prod_cfg)

        record = {
            "date": str(entry_date), "product": product, "nos": nos,
            "plant": plant,
            **{k: result[k] for k in _RM_COST_FIELDS},
        }
        insert_production(record)
        st.toast("✅ DPR entry saved!")
        st.markdown(
            '<div class="success-box">✅ <b>DPR saved successfully!</b></div>',
            unsafe_allow_html=True,
        )

        if role == "production":
            return  # show nothing else to production operator

        # ── RM % Breakdown ────────────────────────────────────────────────────
        st.markdown("---")
        st.markdown('<div class="section-header">Raw Material Composition (%)</div>', unsafe_allow_html=True)
        pct_data = {m["label"]: result[f"pct_{m['key']}"] for m in RAW_MATERIALS}
        pct_cols = st.columns(len(pct_data))
        for i, (mat, pct) in enumerate(pct_data.items()):
            pct_cols[i].metric(f"% {mat}", f"{pct:.1f}%")
        st.caption(f"Total weight: **{result['total_wt_kg']:,.1f} kg**")

        nonzero = {k: v for k, v in pct_data.items() if v > 0}
        if nonzero:
            fig_pie = go.Figure(go.Pie(
                labels=list(nonzero.keys()),
                values=list(nonzero.values()),
                hole=0.4,
                textinfo="label+percent",
                marker_colors=["#00C49A","#3B82F6","#FDBA44","#A78BFA","#FB7185","#34D399","#F97316","#F97316"],
            ))
            fig_pie.update_layout(**PLOT, height=280, showlegend=False)
            st.plotly_chart(fig_pie, use_container_width=True)

        # ── Cost Breakdown ────────────────────────────────────────────────────
        st.markdown('<div class="section-header">Cost Breakdown</div>', unsafe_allow_html=True)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Raw Material",  f"₹{result['rm_cost']:,.0f}")
        k2.metric("Labour",        f"₹{result['labour_cost']:,.0f}")
        k3.metric("Transport",     f"₹{result['transport_cost']:,.0f}")
        k4.metric("Power",         f"₹{result['power_cost']:,.0f}")

        k5, k6, k7, k8 = st.columns(4)
        k5.metric("EMI",                f"₹{result['emi_cost']:,.0f}",   "Fixed/entry")
        k6.metric("DG Cost",            f"₹{result['dg_cost']:,.0f}",    "Fixed/entry")
        k7.metric("Admin Overheads",    f"₹{result['admin_cost']:,.0f}", "Fixed/entry")
        k8.metric("Miscellaneous (10%)",f"₹{result['misc_cost']:,.0f}")

        k9, k10 = st.columns(2)
        k9.metric("Total Cost",  f"₹{result['total_cost']:,.0f}")
        k10.metric("Revenue",    f"₹{result['revenue']:,.0f}",
                   f"@ ₹{prod_cfg[pricing_key]['selling_price']}/nos")

        pcolor = "normal" if result["profit"] >= 0 else "inverse"
        st.metric(
            "Profit",
            f"₹{result['profit']:,.0f}",
            f"{result['profit_pct']:.1f}%  {'✅ Profit' if result['profit']>=0 else '❌ Loss'}",
            delta_color=pcolor,
        )

        labels = ["Raw Material","Labour","Transport","Power","EMI","DG","Admin","Misc"]
        values = [
            result["rm_cost"], result["labour_cost"],
            result["transport_cost"], result["power_cost"],
            result["emi_cost"], result["dg_cost"], result["admin_cost"], result["misc_cost"],
        ]
        colors = ["#00C49A","#3B82F6","#FDBA44","#A78BFA","#F97316","#22D3EE","#FB7185","#E879F9"]
        fig_bar = go.Figure(go.Bar(
            x=labels, y=values,
            marker_color=colors,
            text=[f"₹{v:,.0f}" for v in values],
            textposition="outside",
        ))
        fig_bar.update_layout(**PLOT, height=300, yaxis_title="Rs.", showlegend=False)
        st.plotly_chart(fig_bar, use_container_width=True)

    if role == "production":
        return

    # ── Recent entries ────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown('<div class="section-header">Recent DPR Entries</div>', unsafe_allow_html=True)
    from core.ui import interactive_table, date_range_filter
    dpr_start, dpr_end = date_range_filter("dpr")

    df = get_production()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df[(df["date"] >= pd.Timestamp(dpr_start)) & (df["date"] <= pd.Timestamp(dpr_end))]
        df = df.sort_values(["date", "id"], ascending=[False, False]).reset_index(drop=True)

        show_cols = ["date","product","nos","plant",
                     "rm_cost","labour_cost","transport_cost","power_cost",
                     "emi_cost","dg_cost","admin_cost","misc_cost","total_cost","revenue","profit","profit_pct",
                     ] + [f"pct_{m['key']}" for m in RAW_MATERIALS]
        show_cols = [c for c in show_cols if c in df.columns]
        rename = {
            "date":"Date","product":"Product","nos":"Nos.","plant":"Plant",
            "rm_cost":"RM Cost","labour_cost":"Labour",
            "transport_cost":"Transport","power_cost":"Power","emi_cost":"EMI",
            "dg_cost":"DG","admin_cost":"Admin","misc_cost":"Misc","total_cost":"Total Cost","revenue":"Revenue",
            "profit":"Profit","profit_pct":"Profit %",
            **{f"pct_{m['key']}": f"% {m['label']}" for m in RAW_MATERIALS},
        }
        sum_cols = [c for c in ["nos","revenue","rm_cost","labour_cost","transport_cost",
                                 "power_cost","emi_cost","dg_cost","admin_cost","misc_cost",
                                 "total_cost","profit"] if c in df.columns]
        col_cfg = {"date": st.column_config.DateColumn("Date", format="DD-MMM-YYYY")}
        interactive_table(df, key="dpr_rec", sum_cols=sum_cols, show_cols=show_cols,
                          rename=rename, col_config=col_cfg)
    else:
        st.info("No entries yet. Submit your first DPR above.")

    # ── Edit entry ────────────────────────────────────────────────────────────
    with st.expander("✏️ Edit a DPR Entry"):
        df_edit = get_production()
        if df_edit.empty:
            st.info("No entries to edit.")
        else:
            df_edit["date"] = pd.to_datetime(df_edit["date"], errors="coerce")
            df_edit = df_edit.sort_values(["date", "id"], ascending=[False, False]).reset_index(drop=True)
            df_edit["label"] = (
                df_edit["date"].dt.strftime("%d-%b-%Y") + " | " +
                df_edit["product"].astype(str) + " | " +
                df_edit["nos"].astype(int).astype(str) + " nos | ID:" +
                df_edit["id"].astype(str)
            )
            sel = st.selectbox("Select entry to edit", df_edit["label"].tolist(), key="edit_dpr_sel")
            row = df_edit.loc[df_edit["label"] == sel].iloc[0]
            row_id = int(row["id"])

            # Dynamic form name per row_id forces a fresh widget state on every selection change
            with st.form(f"edit_dpr_form_{row_id}"):
                st.markdown(f"**Editing ID {row_id}**")
                ec1, ec2, ec3 = st.columns(3)
                e_date    = ec1.date_input("Date", pd.to_datetime(row["date"]))
                e_product = ec2.selectbox("Product", PRODUCTION_PRODUCTS,
                                          index=PRODUCTION_PRODUCTS.index(row["product"])
                                          if row["product"] in PRODUCTION_PRODUCTS else 0)
                e_nos     = ec3.number_input("Nos.", min_value=0, value=int(row["nos"]), step=100)

                e_plant = st.radio("Plant", PLANTS,
                                    index=PLANTS.index(row["plant"]) if row.get("plant") in PLANTS else 0,
                                    horizontal=True)

                e_rm_inputs = {}
                er_cols = st.columns(min(len(RAW_MATERIALS), 4))
                for i, m in enumerate(RAW_MATERIALS):
                    step = 0.5 if m["unit"] == "Bags" else 10.0
                    e_rm_inputs[m["key"]] = er_cols[i % len(er_cols)].number_input(
                        f"{m['label']} ({m['unit']})", min_value=0.0,
                        value=float(row.get(f"{m['key']}_qty", 0) or 0), step=step,
                    )

                save = st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True)

            if save:
                e_pricing_key = SKU_TO_PRICING_KEY.get(e_product, e_product)
                result = calculate_production(e_pricing_key, e_nos, e_rm_inputs, rm, prod_cfg)
                updated = {
                    "date": str(e_date), "product": e_product, "nos": e_nos,
                    "plant": e_plant,
                    **{k: result[k] for k in _RM_COST_FIELDS},
                }
                update_production(row_id, updated)
                flash(f"✅ Entry ID {row_id} updated!")
                st.success(f"✅ Entry ID {row_id} updated successfully.")
                st.rerun()

    # ── Delete entries (admin only) ───────────────────────────────────────────
    if role != "admin":
        return
    st.markdown("---")
    with st.expander("🗑️ Delete DPR Entries"):
        df_del = get_production()
        if df_del.empty:
            st.info("No entries to delete.")
        else:
            df_del["date"] = pd.to_datetime(df_del["date"], errors="coerce")
            df_del = df_del.sort_values(["date", "id"], ascending=[False, False]).reset_index(drop=True)
            df_del["label"] = (
                df_del["date"].dt.strftime("%d-%b-%Y") + " | " +
                df_del["product"].astype(str) + " | " +
                df_del["nos"].astype(int).astype(str) + " nos | ID:" +
                df_del["id"].astype(str)
            )
            all_labels = df_del["label"].tolist()

            def _dpr_select_all():
                st.session_state.del_dpr_select = all_labels if st.session_state.del_dpr_all else []

            st.checkbox("Select All", key="del_dpr_all", on_change=_dpr_select_all)
            selected_labels = st.multiselect(
                "Select entries to delete (can pick multiple)",
                all_labels,
                key="del_dpr_select"
            )
            if selected_labels:
                ids_to_delete = df_del.loc[df_del["label"].isin(selected_labels), "id"].tolist()
                st.warning(f"You are about to delete **{len(ids_to_delete)} record(s)**.")
                if st.button(f"🗑️ Confirm Delete ({len(ids_to_delete)})", type="primary", key="del_dpr_btn"):
                    for rid in ids_to_delete:
                        delete_row("production", int(rid))
                    flash(f"🗑️ {len(ids_to_delete)} record(s) deleted.")
                    st.success(f"✅ {len(ids_to_delete)} record(s) deleted.")
                    st.rerun()
