import re
import json
import streamlit as st
import pandas as pd
from core.config import (
    DEFAULT_RM_PRICES, RM_LABELS, PRODUCT_CONFIG, RAW_MATERIALS, HUME_PIPE_DIAMETERS_MM, GST_PCT,
    PRODUCTION_PRODUCTS, DISPATCH_PRODUCTS, SKU_TO_PRICING_KEY, PLANTS, SALE_TYPES,
    EMI_PER_DAY, POWER_PER_DAY, ADMIN_PER_DAY, MISC_PCT, selling_price_unit,
)
from core.db import (
    get_rm_prices, save_rm_prices, get_production, get_dispatch, delete_row,
    get_product_config, save_product_config, get_pipe_diameter_config, save_pipe_diameter_config,
    get_orders, update_order, update_dispatch,
    get_activity_log, insert_production, insert_dispatch,
    get_edit_requests, approve_edit_request, reject_edit_request,
)
from core.calculations import calculate_production, dispatch_value, gst_split
from core.ui import sanitize_for_export
from core.ui import interactive_table, date_range_filter

LAKH = 100_000


def show(PLOT):
    role = st.session_state.get("role", "viewer")
    can_edit = role == "admin"

    st.markdown("""
    <div class="page-title">⚙️ Admin Panel</div>
    <div class="page-subtitle">RM prices · Product config · Data management</div>
    """, unsafe_allow_html=True)
    if not can_edit:
        st.caption("👁️ View-only — you can see configuration and history here, but only Admin can make changes.")

    _pending_reqs = get_edit_requests(status="pending")
    if can_edit and not _pending_reqs.empty:
        st.warning(f"📝 **{len(_pending_reqs)} edit request(s)** waiting for review — see the "
                   f"**Edit Requests** tab below.")

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(
        ["💰 RM Prices", "📦 Product Config", "📋 All Production", "🚚 All Dispatch",
         "🧩 Merge Client Names", "🕵️ Activity Log", f"📝 Edit Requests ({len(_pending_reqs)})"]
    )

    # ── Tab 1: RM Prices ──────────────────────────────────────────────────────
    with tab1:
        st.markdown("### Current Raw Material Prices")
        st.caption("These prices are used in all DPR cost calculations.")

        current = get_rm_prices()

        if can_edit:
            with st.form("rm_prices_form"):
                cols = st.columns(2)
                new_prices = {}
                for i, (key, label) in enumerate(RM_LABELS.items()):
                    col = cols[i % 2]
                    new_prices[key] = col.number_input(
                        label,
                        value=float(current.get(key, DEFAULT_RM_PRICES.get(key, 0))),
                        min_value=0.0, step=0.01,
                    )
                if st.form_submit_button("💾 Save Prices", type="primary", use_container_width=True):
                    save_rm_prices(new_prices)
                    st.success("✅ RM prices updated! New DPR entries will use these prices.")
                    st.rerun()
        else:
            rm_rows = [{"Material": label, "Price (₹)": current.get(key, DEFAULT_RM_PRICES.get(key, 0))}
                       for key, label in RM_LABELS.items()]
            st.dataframe(pd.DataFrame(rm_rows), use_container_width=True, hide_index=True)

    # ── Tab 2: Product Config ─────────────────────────────────────────────────
    with tab2:
        st.markdown("### Product Cost Configuration")

        cfg_sub1, cfg_sub2 = st.tabs(["💲 Selling Price & Concrete", "📏 Pipe Diameter Rates"])

        # For Hume Pipes, Production/Loading/Power/Welding/Jalli/Steel are the
        # same for a given diameter regardless of class or Joint Type — set
        # once per diameter in the second sub-tab. Only Selling Price and
        # Concrete Volume vary by class, so they're edited per product here.
        with cfg_sub1:
            st.caption("Selling Price and Concrete Volume (m³) per product. Concrete Volume is "
                       "pre-computed from diameter+barrel thickness for Hume Pipes. For pipes, "
                       "Production/Loading/Power/Welding/Jalli/Steel are set once per diameter in "
                       "the **Pipe Diameter Rates** tab — they don't vary by class or Joint Type.")

            cfg_all = get_product_config()

            if can_edit:
                products = list(cfg_all.keys())
                sel_prod = st.selectbox("Select Product to Edit", products, key="cfg_prod_sel")
                cfg = cfg_all[sel_prod]
                is_pipe = sel_prod.startswith("Hume Pipe")

                with st.form("product_cfg_form"):
                    _unit = selling_price_unit(sel_prod)
                    new_sell = st.number_input(f"Selling Price (Rs./{_unit})", value=float(cfg["selling_price"]), min_value=0.0, step=0.5)
                    st.caption(f"Invoice total incl. {GST_PCT:.0f}% GST: ₹{cfg['selling_price'] * (1 + GST_PCT/100):,.2f}/{_unit} "
                               f"— GST is collected from the customer but owed to the government, so it's shown here for "
                               f"reference only and never counted as profit.")

                    payload = {"selling_price": new_sell}

                    if is_pipe:
                        new_concrete = st.number_input(
                            "Concrete (m³/Unit)", value=float(cfg.get("concrete_volume_m3", 0)),
                            min_value=0.0, step=0.001, format="%.4f",
                        )
                        payload["concrete_volume_m3"] = new_concrete
                    else:
                        cc1, cc2 = st.columns(2)
                        new_prod = cc1.number_input("Production Cost (Rs./nos)",       value=float(cfg.get("production_cost", 0)), min_value=0.0, step=0.05)
                        new_lu   = cc2.number_input("Loading/Unloading Cost (Rs./nos)", value=float(cfg.get("loading_unloading_cost", 0)), min_value=0.0, step=0.05)

                        cc3, cc4 = st.columns(2)
                        new_weld = cc3.number_input("Welding Cost (Rs./nos)",             value=float(cfg.get("welding_cost", 0)), min_value=0.0, step=0.05)
                        new_jalli = cc4.number_input("Jalli — Cage Welding (Rs./nos)",    value=float(cfg.get("jalli_cost", 0)), min_value=0.0, step=0.05)

                        cc5, cc6 = st.columns(2)
                        new_concrete = cc5.number_input("Concrete (m³/Unit)",         value=float(cfg.get("concrete_volume_m3", 0)), min_value=0.0, step=0.001, format="%.4f")
                        new_steel    = cc6.number_input("Steel (Kg/Unit)", value=float(cfg.get("steel_kg_per_unit", 0)), min_value=0.0, step=0.1)

                        payload.update({
                            "production_cost":        new_prod,
                            "loading_unloading_cost": new_lu,
                            "welding_cost":            new_weld,
                            "jalli_cost":              new_jalli,
                            "concrete_volume_m3":      new_concrete,
                            "steel_kg_per_unit":       new_steel,
                        })

                    st.caption(
                        f"Factory-wide fixed costs, charged once per production day (not per product): "
                        f"EMI ₹{EMI_PER_DAY:,.2f} · Power (incl. DG) ₹{POWER_PER_DAY:,.0f} · "
                        f"Admin ₹{ADMIN_PER_DAY:,.0f} · Misc {MISC_PCT:.0f}% (on this product's raw material cost)"
                    )

                    if st.form_submit_button("💾 Save", type="primary", use_container_width=True):
                        save_product_config(sel_prod, payload)
                        st.success(f"✅ {sel_prod} config saved.")
                        st.rerun()

            st.markdown("---")
            st.markdown(f"**Current config — all products** (Sell incl. GST = Sell x {1 + GST_PCT/100:.2f}, reference only)")
            rows = []
            for prod, c in cfg_all.items():
                row = {
                    "Product": prod, "Sell (₹)": c["selling_price"],
                    "Sell incl. GST (₹)": round(c["selling_price"] * (1 + GST_PCT / 100), 2),
                    "Concrete (m³)": c.get("concrete_volume_m3", 0),
                }
                if not prod.startswith("Hume Pipe"):
                    row.update({
                        "Production":     c.get("production_cost", 0),
                        "Loading/Unload": c.get("loading_unloading_cost", 0),
                        "Welding":        c.get("welding_cost", 0),
                        "Jalli":          c.get("jalli_cost", 0),
                        "Steel (Kg)":     c.get("steel_kg_per_unit", 0),
                    })
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        with cfg_sub2:
            st.caption("These 5 rates apply to every class (NP2/NP3/NP4) and Joint Type at the "
                       "selected diameter — set once per diameter, not per SKU. Power is a factory-wide "
                       "fixed cost charged once per production day (Dashboard), not set here.")

            dia_cfg_all = get_pipe_diameter_config()

            if can_edit:
                sel_dia = st.selectbox("Select Diameter (mm)", HUME_PIPE_DIAMETERS_MM, key="dia_cfg_sel")
                dcfg = dia_cfg_all[sel_dia]

                with st.form("pipe_dia_cfg_form"):
                    dc1, dc2 = st.columns(2)
                    d_prod = dc1.number_input("Production Cost (Rs./nos)",       value=float(dcfg.get("production_cost", 0)), min_value=0.0, step=0.05)
                    d_lu   = dc2.number_input("Loading/Unloading Cost (Rs./nos)", value=float(dcfg.get("loading_unloading_cost", 0)), min_value=0.0, step=0.05)

                    dc3, dc4 = st.columns(2)
                    d_weld  = dc3.number_input("Welding Cost (Rs./nos)",           value=float(dcfg.get("welding_cost", 0)), min_value=0.0, step=0.05)
                    d_jalli = dc4.number_input("Jalli — Cage Welding (Rs./nos)",   value=float(dcfg.get("jalli_cost", 0)), min_value=0.0, step=0.05)

                    d_steel = st.number_input("Steel (Kg/Unit)", value=float(dcfg.get("steel_kg_per_unit", 0)), min_value=0.0, step=0.1)

                    if st.form_submit_button("💾 Save", type="primary", use_container_width=True):
                        save_pipe_diameter_config(sel_dia, {
                            "production_cost":        d_prod,
                            "loading_unloading_cost": d_lu,
                            "welding_cost":           d_weld,
                            "jalli_cost":             d_jalli,
                            "steel_kg_per_unit":      d_steel,
                        })
                        st.success(f"✅ {sel_dia}mm diameter rates saved.")
                        st.rerun()

            st.markdown("---")
            st.markdown("**Current rates — all diameters**")
            drows = []
            for d, c in dia_cfg_all.items():
                drows.append({
                    "Diameter (mm)":  d,
                    "Production":     c.get("production_cost", 0),
                    "Loading/Unload": c.get("loading_unloading_cost", 0),
                    "Welding":        c.get("welding_cost", 0),
                    "Jalli":          c.get("jalli_cost", 0),
                    "Steel (Kg)":     c.get("steel_kg_per_unit", 0),
                })
            st.dataframe(pd.DataFrame(drows), use_container_width=True, hide_index=True)

    # ── Tab 3: All Production ─────────────────────────────────────────────────
    with tab3:
        st.markdown("### All Production Records")
        c1, c2 = st.columns(2)
        from core.tz import today_ist
        start = c1.date_input("From", today_ist().replace(day=1), key="prod_start")
        end   = c2.date_input("To",   today_ist(), key="prod_end")

        df = get_production(str(start), str(end))
        if df.empty:
            st.info("No production records found.")
        else:
            st.markdown(f"**{len(df)} records** | "
                        f"Total Nos: {df['nos'].sum():,.0f} | "
                        f"Total Revenue: ₹{df['revenue'].sum()/LAKH:.2f}L | "
                        f"Total Profit: ₹{df['profit'].sum()/LAKH:.2f}L")

            st.dataframe(df.drop(columns=["created_at"], errors="ignore"),
                         use_container_width=True, hide_index=True)

            # Export
            csv = sanitize_for_export(df).to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", csv,
                               f"production_{start}_{end}.csv", "text/csv")

            # Delete record
            if can_edit:
                st.markdown("---")
                del_id = st.number_input("Delete record by ID", min_value=1, step=1)
                if st.button("🗑️ Delete", type="secondary"):
                    delete_row("production", int(del_id))
                    st.success(f"Record {del_id} deleted.")
                    st.rerun()

        # ── Import from CSV ──────────────────────────────────────────────────
        if can_edit:
            st.markdown("---")
            with st.expander("⬆️ Import Production (DPR) from CSV"):
                st.caption(
                    "Required columns: **date, product, nos**. Optional: **plant** "
                    f"(defaults to \"{PLANTS[0]}\"). `product` must exactly match a product name "
                    "from DPR Entry (e.g. \"Hume Pipe 300mm NP3 (Socket & Spigot)\"). Costs are "
                    "auto-calculated the same way as a manual DPR entry."
                )
                prod_file = st.file_uploader("CSV file", type=["csv"], key="prod_import_file")
                if prod_file is not None:
                    try:
                        imp_df = pd.read_csv(prod_file)
                    except Exception as e:
                        st.error(f"Could not read CSV: {e}")
                        imp_df = None

                    if imp_df is not None:
                        imp_df.columns = [re.sub(r"[\s\-]+", "_", c.strip().lower()) for c in imp_df.columns]
                        missing = [c for c in ("date", "product", "nos") if c not in imp_df.columns]
                        if missing:
                            st.error(f"Missing required column(s): {', '.join(missing)}")
                        else:
                            bad_products = sorted(set(imp_df["product"].astype(str)) - set(PRODUCTION_PRODUCTS))
                            if bad_products:
                                st.error("Unknown product name(s) — must match DPR Entry exactly: "
                                          + ", ".join(bad_products))
                            else:
                                st.markdown(f"**Preview — {len(imp_df)} row(s)**")
                                st.dataframe(imp_df.head(20), use_container_width=True, hide_index=True)
                                if st.button(f"✅ Import {len(imp_df)} Production Row(s)", type="primary", key="prod_import_btn"):
                                    rm = get_rm_prices()
                                    prod_cfg_i = get_product_config()
                                    pipe_dia_cfg_i = get_pipe_diameter_config()
                                    imported = 0
                                    for _, r in imp_df.iterrows():
                                        nos = float(r["nos"])
                                        if nos <= 0:
                                            continue
                                        product = str(r["product"])
                                        plant = str(r["plant"]).strip() if "plant" in imp_df.columns and pd.notna(r.get("plant")) and str(r.get("plant")).strip() else PLANTS[0]
                                        pricing_key = SKU_TO_PRICING_KEY.get(product, product)
                                        r_date = str(pd.to_datetime(r["date"]).date())
                                        result = calculate_production(pricing_key, nos, rm, prod_cfg_i,
                                                                        pipe_diameter_config=pipe_dia_cfg_i)
                                        record = {
                                            "date": r_date,
                                            "product": product, "nos": nos, "plant": plant,
                                            **result,
                                        }
                                        insert_production(record)
                                        imported += 1
                                    st.success(f"✅ Imported {imported} production row(s).")
                                    st.rerun()

    # ── Tab 4: All Dispatch ───────────────────────────────────────────────────
    with tab4:
        st.markdown("### All Dispatch Records")

        # One-time: mark all existing null bill_no entries as billed
        if can_edit:
            from core.db import get_dispatch as _get_all_disp, update_dispatch as _upd_disp, _use_supabase, _sb_url, _headers
            import requests as _req
            df_null = _get_all_disp()
            if not df_null.empty:
                null_mask = df_null["bill_no"].isna() | (df_null["bill_no"].astype(str).str.strip().isin(["","None","nan"]))
                null_count = null_mask.sum()
                if null_count > 0:
                    st.warning(f"⚠️ **{null_count} existing entries** have no Bill No. (showing as pending invoice).")
                    if st.button(f"✅ Mark all {null_count} existing entries as BILLED (one-time)", key="mark_all_billed"):
                        if _use_supabase():
                            r = _req.patch(
                                f"{_sb_url('dispatch')}",
                                headers={**_headers(), "Prefer": "return=minimal"},
                                params={"bill_no": "is.null"},
                                json={"bill_no": "BILLED"},
                            )
                            if r.status_code in (200, 204):
                                st.success(f"✅ {null_count} entries marked as BILLED.")
                                st.rerun()
                            else:
                                st.error(f"Error: {r.text}")
                        else:
                            from core.db import _conn
                            con = _conn()
                            con.execute("UPDATE dispatch SET bill_no = 'BILLED' WHERE bill_no IS NULL OR bill_no = ''")
                            con.commit(); con.close()
                            st.success(f"✅ {null_count} entries marked as BILLED.")
                            st.rerun()
            st.markdown("---")
        c3, c4 = st.columns(2)
        from core.tz import today_ist
        start2 = c3.date_input("From", today_ist().replace(day=1), key="disp_start")
        end2   = c4.date_input("To",   today_ist(), key="disp_end")

        df2 = get_dispatch(str(start2), str(end2))
        if df2.empty:
            st.info("No dispatch records found.")
        else:
            total_val2 = df2["dispatch_value"].sum()
            zero_rows  = df2[(df2["dispatch_value"] == 0) | df2["dispatch_value"].isna()].copy()
            fixable    = df2[(df2.get("qty_dispatched", pd.Series([0]*len(df2))).fillna(0) > 0) &
                             (df2.get("rate", pd.Series([0]*len(df2))).fillna(0) > 0) &
                             ((df2["dispatch_value"].fillna(0) == 0) |
                              ((df2["dispatch_value"].fillna(0) - df2.get("qty_dispatched", pd.Series([0]*len(df2))).fillna(0) *
                                df2.get("rate", pd.Series([0]*len(df2))).fillna(0)).abs() > 1))].copy()

            st.markdown(f"**{len(df2)} challans** | "
                        f"Total Dispatched: {df2['qty_dispatched'].sum():,.0f} nos | "
                        f"Total Value: ₹{total_val2/LAKH:.2f}L")

            if not fixable.empty:
                st.warning(f"⚠️ **{len(fixable)} entries** have dispatch_value that doesn't match qty × rate. "
                           f"Missing value: ₹{(fixable['qty_dispatched'].fillna(0) * fixable['rate'].fillna(0) - fixable['dispatch_value'].fillna(0)).sum()/LAKH:.2f}L")
                with st.expander(f"👁️ Show {len(fixable)} mismatched entries"):
                    fix_disp = fixable[["id","date","client_name","product","qty_dispatched","rate","dispatch_value"]].copy()
                    fix_disp["correct_value"] = (fix_disp["qty_dispatched"].fillna(0) * fix_disp["rate"].fillna(0)).round(2)
                    fix_disp["difference"] = fix_disp["correct_value"] - fix_disp["dispatch_value"].fillna(0)
                    st.dataframe(fix_disp, use_container_width=True, hide_index=True)

                if can_edit and st.button("🔧 Recalculate & Fix All dispatch_value (qty × rate)", type="primary", key="fix_dv"):
                    fixed = 0
                    for _, row in fixable.iterrows():
                        correct = round(float(row.get("qty_dispatched", 0) or 0) *
                                        float(row.get("rate", 0) or 0), 2)
                        update_dispatch(int(row["id"]), {"dispatch_value": correct})
                        fixed += 1
                    st.success(f"✅ Fixed {fixed} entries. Refresh to see updated totals.")
                    st.rerun()

            st.dataframe(df2.drop(columns=["created_at"], errors="ignore"),
                         use_container_width=True, hide_index=True)

            csv2 = sanitize_for_export(df2).to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", csv2,
                               f"dispatch_{start2}_{end2}.csv", "text/csv",
                               key="dl_disp")

            if can_edit:
                st.markdown("---")
                st.markdown("**Delete by ID**")
                del_id2 = st.number_input("Delete record by ID", min_value=1, step=1, key="del_disp")
                if st.button("🗑️ Delete Single Record", type="secondary"):
                    delete_row("dispatch", int(del_id2))
                    st.success(f"Record {del_id2} deleted.")
                    st.rerun()

                st.markdown("---")
                st.markdown("**🗑️ Bulk Delete — entire date range**")
                st.caption(f"This will delete ALL {len(df2)} dispatch records from {start2} to {end2} in one shot.")
                confirm_txt = st.text_input("Type DELETE to confirm", key="bulk_del_confirm")
                if st.button(f"🗑️ Delete ALL {len(df2)} records in range", type="primary", key="bulk_del_btn"):
                    if confirm_txt.strip() == "DELETE":
                        from core.db import delete_dispatch_range
                        delete_dispatch_range(str(start2), str(end2))
                        st.success(f"✅ Deleted {len(df2)} records ({start2} → {end2}). Now re-import fresh data.")
                        st.rerun()
                    else:
                        st.error("Type exactly DELETE to confirm.")

        # ── Import from CSV ──────────────────────────────────────────────────
        if can_edit:
            st.markdown("---")
            with st.expander("⬆️ Import Dispatch Challans from CSV"):
                st.caption(
                    "Required columns: **date, challan_no, product, qty_dispatched, rate**. Optional: "
                    "**di_no, bill_no, sale_type, client_name, delivery_address, qty_ordered, "
                    "trip_distance, truck_no, driver_name, remarks, form_filled_by, gst_applicable** "
                    "(yes/no — defaults to no). `product` must exactly match a Dispatch Entry product name, "
                    "`sale_type` must be one of: " + ", ".join(SALE_TYPES) + f" (defaults to \"{SALE_TYPES[0]}\")."
                )
                disp_file = st.file_uploader("CSV file", type=["csv"], key="disp_import_file")
                if disp_file is not None:
                    try:
                        dimp_df = pd.read_csv(disp_file)
                    except Exception as e:
                        st.error(f"Could not read CSV: {e}")
                        dimp_df = None

                    if dimp_df is not None:
                        dimp_df.columns = [re.sub(r"[\s\-]+", "_", c.strip().lower()) for c in dimp_df.columns]
                        missing = [c for c in ("date", "challan_no", "product", "qty_dispatched", "rate")
                                   if c not in dimp_df.columns]
                        if missing:
                            st.error(f"Missing required column(s): {', '.join(missing)}")
                        else:
                            bad_products = sorted(set(dimp_df["product"].astype(str)) - set(DISPATCH_PRODUCTS))
                            if bad_products:
                                st.error("Unknown product name(s) — must match Dispatch Entry exactly: "
                                          + ", ".join(bad_products))
                            else:
                                st.markdown(f"**Preview — {len(dimp_df)} row(s)**")
                                st.dataframe(dimp_df.head(20), use_container_width=True, hide_index=True)
                                if st.button(f"✅ Import {len(dimp_df)} Dispatch Row(s)", type="primary", key="disp_import_btn"):
                                    imported = 0
                                    for _, r in dimp_df.iterrows():
                                        qty_d = float(r["qty_dispatched"])
                                        rate  = float(r["rate"])
                                        if qty_d <= 0:
                                            continue
                                        sale_type_v = str(r["sale_type"]).strip() if "sale_type" in dimp_df.columns and pd.notna(r.get("sale_type")) and str(r.get("sale_type")).strip() else SALE_TYPES[0]
                                        gst_flag = str(r.get("gst_applicable", "")).strip().lower() in ("yes", "true", "1") if "gst_applicable" in dimp_df.columns else False
                                        base_value = dispatch_value(qty_d, rate)
                                        gst_amt, d_value = gst_split(base_value, gst_flag)

                                        def _opt(col):
                                            return str(r[col]) if col in dimp_df.columns and pd.notna(r.get(col)) else None

                                        record = {
                                            "date": str(pd.to_datetime(r["date"]).date()),
                                            "challan_no": str(r["challan_no"]), "di_no": _opt("di_no"),
                                            "bill_no": _opt("bill_no"), "sale_type": sale_type_v,
                                            "client_name": _opt("client_name"), "delivery_address": _opt("delivery_address"),
                                            "product": str(r["product"]),
                                            "qty_ordered": float(r["qty_ordered"]) if "qty_ordered" in dimp_df.columns and pd.notna(r.get("qty_ordered")) else qty_d,
                                            "qty_dispatched": qty_d, "rate": rate,
                                            "dispatch_value": d_value, "gst_applicable": gst_flag, "gst_amount": gst_amt,
                                            "trip_distance": float(r["trip_distance"]) if "trip_distance" in dimp_df.columns and pd.notna(r.get("trip_distance")) else 0.0,
                                            "truck_no": _opt("truck_no"), "driver_name": _opt("driver_name"),
                                            "remarks": _opt("remarks"), "form_filled_by": _opt("form_filled_by"),
                                        }
                                        insert_dispatch(record)
                                        imported += 1
                                    st.success(f"✅ Imported {imported} dispatch row(s).")
                                    st.rerun()

    # ── Tab 5: Merge Client Names ─────────────────────────────────────────────
    with tab5:
        st.markdown("### 🧩 Merge Duplicate Client Names")
        st.caption(
            "Fixes cases like **\"Frontage\"** vs **\"Frontage Construction\"** being counted as "
            "two different clients in Top-10-Clients analytics. Pick the variants below, choose "
            "(or type) the correct name, and every matching Sales Order + Dispatch record will be "
            "renamed to it. New entries now use a client dropdown (Sales Orders / Dispatch pages) "
            "to prevent this going forward."
        )

        if not can_edit:
            st.info("👁️ View-only — ask an Admin to merge client names.")
        else:
            df_ord_m  = get_orders()
            df_disp_m = get_dispatch()

            counts = {}
            if not df_ord_m.empty and "client_name" in df_ord_m.columns:
                for name, cnt in df_ord_m["client_name"].dropna().astype(str).value_counts().items():
                    counts[name] = counts.get(name, 0) + int(cnt)
            if not df_disp_m.empty and "client_name" in df_disp_m.columns:
                for name, cnt in df_disp_m["client_name"].dropna().astype(str).value_counts().items():
                    counts[name] = counts.get(name, 0) + int(cnt)

            all_names = sorted(n for n in counts if n.strip())
            if len(all_names) < 2:
                st.info("Not enough distinct client names yet to merge anything.")
            else:
                variants = st.multiselect(
                    "Select the name variants that are actually the same client",
                    all_names,
                    format_func=lambda n: f"{n}  ({counts.get(n, 0)} records)",
                    key="merge_variants",
                )

                if len(variants) >= 2:
                    target_options = variants + ["+ Type a different correct name"]
                    target_pick = st.selectbox("Correct name to use for all of these", target_options, key="merge_target_pick")
                    if target_pick == "+ Type a different correct name":
                        target = st.text_input("Correct client name", key="merge_target_new").strip()
                    else:
                        target = target_pick

                    affected_ord  = df_ord_m[df_ord_m["client_name"].astype(str).isin(variants)] if not df_ord_m.empty else pd.DataFrame()
                    affected_disp = df_disp_m[df_disp_m["client_name"].astype(str).isin(variants)] if not df_disp_m.empty else pd.DataFrame()
                    total_affected = len(affected_ord) + len(affected_disp)

                    st.warning(
                        f"This will rename **{total_affected} record(s)** "
                        f"({len(affected_ord)} order line(s), {len(affected_disp)} dispatch entr(y/ies)) "
                        f"to **\"{target or '—'}\"**."
                    )

                    confirm = st.text_input("Type MERGE to confirm", key="merge_confirm")
                    if st.button(f"🧩 Merge {total_affected} record(s)", type="primary", disabled=not target):
                        if confirm.strip() != "MERGE":
                            st.error("Type exactly MERGE to confirm.")
                        else:
                            n = 0
                            for _, row in affected_ord.iterrows():
                                if str(row["client_name"]) != target:
                                    update_order(int(row["id"]), {"client_name": target})
                                    n += 1
                            for _, row in affected_disp.iterrows():
                                if str(row["client_name"]) != target:
                                    update_dispatch(int(row["id"]), {"client_name": target})
                                    n += 1
                            st.success(f"✅ Merged {n} record(s) into \"{target}\".")
                            st.rerun()
                else:
                    st.caption("Select at least 2 name variants above to merge them.")

    # ── Tab 6: Activity Log ────────────────────────────────────────────────────
    with tab6:
        st.markdown("### Who opened / edited what")
        st.caption("Every login, page view, create, edit, and delete across the app.")

        df_log = get_activity_log()
        if df_log.empty:
            st.info("No activity recorded yet.")
        else:
            # Supabase's TIMESTAMPTZ comes back UTC; convert to IST before
            # stripping the tz so it can be compared against the tz-naive
            # From/To date inputs below — this used to just drop the tz
            # without converting, showing raw UTC time as if it were IST.
            df_log["created_at"] = (
                pd.to_datetime(df_log["created_at"], errors="coerce", utc=True)
                .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            )
            df_log = df_log.sort_values(["created_at", "id"], ascending=[False, False])

            log_start, log_end = date_range_filter(
                "activity_log", default_start=df_log["created_at"].min().date()
            )
            df_log = df_log[(df_log["created_at"] >= pd.Timestamp(log_start)) &
                             (df_log["created_at"] <= pd.Timestamp(log_end) + pd.Timedelta(days=1))]

            show_cols = ["created_at", "name", "role", "action", "module", "detail"]
            show_cols = [c for c in show_cols if c in df_log.columns]
            rename = {
                "created_at": "When", "name": "User", "role": "Role",
                "action": "Action", "module": "Module", "detail": "Detail",
            }
            col_cfg = {"created_at": st.column_config.DatetimeColumn("When", format="DD-MMM-YYYY HH:mm")}
            interactive_table(df_log, key="activity_log", show_cols=show_cols, rename=rename, col_config=col_cfg)

    # ── Tab 7: Edit Requests ───────────────────────────────────────────────────
    with tab7:
        st.markdown("### Pending Edit Requests")
        st.caption(
            "Submitted by roles that can't edit directly (Production/Factory on DPR, Dispatch/Factory "
            "on Dispatch, Headoffice on Sales Orders). Approving applies the change to the live record "
            "immediately; rejecting discards it — nothing here touches real data until you decide."
        )

        df_reqs = get_edit_requests()
        if df_reqs.empty:
            st.info("No edit requests yet.")
        else:
            pending = df_reqs[df_reqs["status"] == "pending"].sort_values("created_at")
            if pending.empty:
                st.success("✅ No pending requests.")
            else:
                for _, req in pending.iterrows():
                    old = json.loads(req["old_data"]) if req.get("old_data") else {}
                    new = json.loads(req["new_data"]) if req.get("new_data") else {}
                    changed = {k: (old.get(k), new.get(k)) for k in new if str(old.get(k)) != str(new.get(k))}

                    header = f"{req['module_label']} — {req['summary']} · by {req.get('requested_by_name') or req.get('requested_by')}"
                    with st.expander(header):
                        st.caption(f"Submitted {req['created_at']} by {req.get('requested_by_name','')} "
                                  f"({req.get('requested_role','')})")
                        if changed:
                            diff_rows = [
                                {"Field": k.replace("_", " ").title(), "Current": v_old, "Requested": v_new}
                                for k, (v_old, v_new) in changed.items()
                            ]
                            st.dataframe(pd.DataFrame(diff_rows), use_container_width=True, hide_index=True)
                        else:
                            st.caption("No field-level changes detected.")

                        if can_edit:
                            ac1, ac2 = st.columns(2)
                            if ac1.button("✅ Approve", type="primary", key=f"appr_{req['id']}", use_container_width=True):
                                try:
                                    approve_edit_request(int(req["id"]))
                                    st.success("Approved and applied.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Could not apply: {e}")
                            note_key = f"rej_note_{req['id']}"
                            ac2.text_input("Rejection note (optional)", key=note_key,
                                          label_visibility="collapsed", placeholder="Reason (optional)")
                            if ac2.button("❌ Reject", key=f"rej_{req['id']}", use_container_width=True):
                                reject_edit_request(int(req["id"]), st.session_state.get(note_key, ""))
                                st.success("Rejected.")
                                st.rerun()

            st.markdown("---")
            st.markdown("**Recent decisions**")
            decided = df_reqs[df_reqs["status"] != "pending"].sort_values("created_at", ascending=False).head(50)
            if decided.empty:
                st.caption("No decisions yet.")
            else:
                dd = decided[["created_at", "module_label", "summary", "status", "reviewed_by", "review_note"]].rename(columns={
                    "created_at": "Submitted", "module_label": "Module", "summary": "Entry",
                    "status": "Status", "reviewed_by": "Reviewed By", "review_note": "Note",
                })
                st.dataframe(dd, use_container_width=True, hide_index=True)
