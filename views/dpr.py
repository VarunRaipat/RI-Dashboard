import streamlit as st
import pandas as pd
from datetime import date
from core.config import PRODUCTION_PRODUCTS, PRODUCT_CONFIG, RAW_MATERIALS, PLANTS, SKU_TO_PRICING_KEY
from core.calculations import calculate_production
from core.db import (
    insert_production, insert_rm_usage, get_rm_prices, get_production, delete_row, update_production,
    get_product_config, get_pipe_diameter_config,
)
from core.ui import flash, show_flashes

_RM_COST_FIELDS = [
    "rm_cost","production_cost","loading_unloading_cost","power_cost","welding_cost","jalli_cost",
    "emi_cost","dg_cost","admin_cost","misc_cost","total_cost","revenue","profit","profit_pct",
] + [f"{m['key']}_qty" for m in RAW_MATERIALS] + [f"{m['key']}_cost" for m in RAW_MATERIALS]


def _init_lines():
    if "dpr_lines" not in st.session_state:
        st.session_state.dpr_lines = 1


def show(PLOT):
    role = st.session_state.get("role", "production")
    show_flashes()

    st.markdown("""
    <div class="page-title">📋 Daily Production Report</div>
    <div class="page-subtitle">Enter production data · costs auto-calculated</div>
    """, unsafe_allow_html=True)

    rm = get_rm_prices()
    prod_cfg = get_product_config()
    pipe_dia_cfg = get_pipe_diameter_config()
    _init_lines()

    st.markdown('<div class="section-header">Basic Info</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    entry_date = c1.date_input("Date", date.today(), key="dpr_date")
    plant      = c2.radio("Plant", PLANTS, horizontal=True, key="dpr_plant")

    st.markdown('<div class="section-header">Products Made Today</div>', unsafe_allow_html=True)
    st.caption("Add one line per pipe/product made today. Concrete, Steel, Jalli, Welding, "
               "Production, and Power costs are computed automatically from each product's fixed "
               "per-unit figures (Admin > Product Cost Configuration / Pipe Diameter Rates).")

    n_lines = st.session_state.dpr_lines
    header_cols = st.columns([3, 2, 1])
    header_cols[0].markdown("**Product**")
    header_cols[1].markdown("**Nos.**")

    for i in range(n_lines):
        cols = st.columns([3, 2, 1])
        cols[0].selectbox("Product", PRODUCTION_PRODUCTS, key=f"dpr_prod_{i}", label_visibility="collapsed")
        cols[1].number_input("Nos.", min_value=0, step=100, key=f"dpr_nos_{i}", label_visibility="collapsed")
        if n_lines > 1:
            if cols[2].button("✕", key=f"dpr_rem_{i}"):
                for j in range(i, n_lines - 1):
                    st.session_state[f"dpr_prod_{j}"] = st.session_state.get(f"dpr_prod_{j+1}", PRODUCTION_PRODUCTS[0])
                    st.session_state[f"dpr_nos_{j}"]  = st.session_state.get(f"dpr_nos_{j+1}", 0)
                st.session_state.dpr_lines = n_lines - 1
                st.rerun()

    if st.button("➕ Add Product", key="dpr_add_line"):
        st.session_state.dpr_lines += 1
        st.rerun()

    st.markdown('<div class="section-header">Raw Materials Used Today</div>', unsafe_allow_html=True)
    st.caption("Total Cement and GGBS bags consumed today, across all products above — for "
               "inventory reconciliation only (doesn't affect cost/profit, which uses Concrete "
               "Volume instead).")
    rmc1, rmc2 = st.columns(2)
    cement_bags = rmc1.number_input("Cement Used (Bags)", min_value=0.0, step=0.5, key="dpr_cement_bags")
    ggbs_bags   = rmc2.number_input("GGBS Used (Bags)",    min_value=0.0, step=0.5, key="dpr_ggbs_bags")

    st.markdown("")
    if st.button("✅ Submit & Calculate", type="primary", use_container_width=True, key="dpr_submit"):
        saved_rows = []
        for i in range(st.session_state.dpr_lines):
            nos = st.session_state.get(f"dpr_nos_{i}", 0) or 0
            if nos <= 0:
                continue
            product = st.session_state.get(f"dpr_prod_{i}", PRODUCTION_PRODUCTS[0])
            pricing_key = SKU_TO_PRICING_KEY.get(product, product)
            result = calculate_production(pricing_key, nos, rm, prod_cfg, pipe_diameter_config=pipe_dia_cfg)
            record = {
                "date": str(entry_date), "product": product, "nos": nos,
                "plant": plant,
                **{k: result[k] for k in _RM_COST_FIELDS},
            }
            insert_production(record)
            saved_rows.append({
                "Product": product, "Nos.": nos, "Revenue": result["revenue"],
                "Total Cost": result["total_cost"], "Profit": result["profit"], "Profit %": result["profit_pct"],
            })

        if not saved_rows:
            st.error("Enter Nos. > 0 for at least one product line.")
        else:
            if cement_bags > 0 or ggbs_bags > 0:
                insert_rm_usage({
                    "date": str(entry_date), "cement_bags": cement_bags, "ggbs_bags": ggbs_bags,
                })

            st.toast("✅ DPR entry saved!")
            st.markdown(
                f'<div class="success-box">✅ <b>{len(saved_rows)} product line(s) saved for {entry_date}!</b></div>',
                unsafe_allow_html=True,
            )

            # Reset line widgets for the next entry
            for i in range(st.session_state.dpr_lines):
                for k in (f"dpr_prod_{i}", f"dpr_nos_{i}"):
                    if k in st.session_state:
                        del st.session_state[k]
            st.session_state.dpr_lines = 1
            for k in ("dpr_cement_bags", "dpr_ggbs_bags"):
                if k in st.session_state:
                    del st.session_state[k]

            if role != "production":
                st.markdown('<div class="section-header">Saved Entries — Summary</div>', unsafe_allow_html=True)
                summary_df = pd.DataFrame(saved_rows)
                st.dataframe(summary_df, use_container_width=True, hide_index=True)
                s1, s2, s3 = st.columns(3)
                s1.metric("Total Nos.", f"{summary_df['Nos.'].sum():,.0f}")
                s2.metric("Total Revenue", f"₹{summary_df['Revenue'].sum():,.0f}")
                s3.metric("Total Profit", f"₹{summary_df['Profit'].sum():,.0f}")

            st.rerun()

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
                     "rm_cost","production_cost","loading_unloading_cost","power_cost","welding_cost","jalli_cost",
                     "emi_cost","dg_cost","admin_cost","misc_cost","total_cost","revenue","profit","profit_pct",
                     ] + [f"{m['key']}_qty" for m in RAW_MATERIALS]
        show_cols = [c for c in show_cols if c in df.columns]
        rename = {
            "date":"Date","product":"Product","nos":"Nos.","plant":"Plant",
            "rm_cost":"RM Cost","production_cost":"Production","loading_unloading_cost":"Loading/Unloading",
            "power_cost":"Power","welding_cost":"Welding","jalli_cost":"Jalli","emi_cost":"EMI",
            "dg_cost":"DG","admin_cost":"Admin","misc_cost":"Misc","total_cost":"Total Cost","revenue":"Revenue",
            "profit":"Profit","profit_pct":"Profit %",
            **{f"{m['key']}_qty": f"{m['label']} ({m['unit']})" for m in RAW_MATERIALS},
        }
        sum_cols = [c for c in ["nos","revenue","rm_cost","production_cost","loading_unloading_cost",
                                 "power_cost","welding_cost","jalli_cost","emi_cost","dg_cost","admin_cost","misc_cost",
                                 "total_cost","profit"] if c in df.columns]
        col_cfg = {"date": st.column_config.DateColumn("Date", format="DD-MMM-YYYY")}
        interactive_table(df, key="dpr_rec", sum_cols=sum_cols, show_cols=show_cols,
                          rename=rename, col_config=col_cfg)
    else:
        st.info("No entries yet. Submit your first DPR above.")

    # ── Recent RM usage (Cement/GGBS) ──────────────────────────────────────────
    st.markdown('<div class="section-header">Recent Cement/GGBS Usage</div>', unsafe_allow_html=True)
    from core.db import get_rm_usage
    df_rmu = get_rm_usage()
    if not df_rmu.empty:
        df_rmu["date"] = pd.to_datetime(df_rmu["date"], errors="coerce")
        df_rmu = df_rmu[(df_rmu["date"] >= pd.Timestamp(dpr_start)) & (df_rmu["date"] <= pd.Timestamp(dpr_end))]
        df_rmu = df_rmu.sort_values(["date", "id"], ascending=[False, False]).reset_index(drop=True)
        show_cols_rmu = [c for c in ["date", "cement_bags", "ggbs_bags", "remarks"] if c in df_rmu.columns]
        interactive_table(
            df_rmu, key="dpr_rmu", show_cols=show_cols_rmu,
            sum_cols=[c for c in ["cement_bags", "ggbs_bags"] if c in df_rmu.columns],
            rename={"date": "Date", "cement_bags": "Cement (Bags)", "ggbs_bags": "GGBS (Bags)", "remarks": "Remarks"},
            col_config={"date": st.column_config.DateColumn("Date", format="DD-MMM-YYYY")},
        )
    else:
        st.info("No Cement/GGBS usage recorded yet.")

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

                save = st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True)

            if save:
                e_pricing_key = SKU_TO_PRICING_KEY.get(e_product, e_product)
                result = calculate_production(e_pricing_key, e_nos, rm, prod_cfg, pipe_diameter_config=pipe_dia_cfg)
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
