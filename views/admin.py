import streamlit as st
import pandas as pd
from core.config import DEFAULT_RM_PRICES, RM_LABELS, PRODUCT_CONFIG, RAW_MATERIALS
from core.db import (
    get_rm_prices, save_rm_prices, get_production, get_dispatch, delete_row,
    get_product_config, save_product_config, get_orders, update_order, update_dispatch,
    get_activity_log,
)
from core.ui import interactive_table, date_range_filter

LAKH = 100_000


def show(PLOT):
    st.markdown("""
    <div class="page-title">⚙️ Admin Panel</div>
    <div class="page-subtitle">RM prices · Product config · Data management</div>
    """, unsafe_allow_html=True)

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        ["💰 RM Prices", "📦 Product Config", "📋 All Production", "🚚 All Dispatch",
         "🧩 Merge Client Names", "🕵️ Activity Log"]
    )

    # ── Tab 1: RM Prices ──────────────────────────────────────────────────────
    with tab1:
        st.markdown("### Current Raw Material Prices")
        st.caption("These prices are used in all DPR cost calculations.")

        current = get_rm_prices()

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

        # Show price history
        from core.db import _use_supabase, _sb_url, _headers, _conn
        import requests as _req
        _hist_cols = ["effective_date"] + [m["key"] for m in RAW_MATERIALS]
        try:
            if _use_supabase():
                r = _req.get(_sb_url("rm_prices"), headers=_headers(), params={
                    "select": ",".join(_hist_cols),
                    "order": "created_at.desc", "limit": 10,
                })
                hist = pd.DataFrame(r.json()) if r.status_code == 200 and r.json() else pd.DataFrame()
            else:
                con = _conn()
                hist = pd.read_sql(
                    f"SELECT {', '.join(_hist_cols)} FROM rm_prices ORDER BY created_at DESC LIMIT 10", con)
                con.close()
            if not hist.empty:
                st.markdown("#### Price History (last 10)")
                st.dataframe(hist, use_container_width=True, hide_index=True)
        except Exception as e:
            st.caption(f"Price history unavailable: {e}")

    # ── Tab 2: Product Config ─────────────────────────────────────────────────
    with tab2:
        st.markdown("### Product Cost Configuration")
        st.caption("Edit selling price, production/loading/power/welding/jalli rates, and Concrete/Steel usage "
                   "per product. Changes apply to all new DPR entries. "
                   "(No Transport field — real transport cost is tracked in the Dispatch module.)")

        cfg_all = get_product_config()
        products = list(cfg_all.keys())
        sel_prod = st.selectbox("Select Product to Edit", products, key="cfg_prod_sel")
        cfg = cfg_all[sel_prod]

        with st.form("product_cfg_form"):
            cc1, cc2 = st.columns(2)
            new_sell = cc1.number_input("Selling Price (Rs./nos)",         value=float(cfg["selling_price"]), min_value=0.0, step=0.5)
            new_prod = cc2.number_input("Production Cost (Rs./nos)",       value=float(cfg.get("production_cost", 0)), min_value=0.0, step=0.05)

            cc3, cc4 = st.columns(2)
            new_lu   = cc3.number_input("Loading/Unloading Cost (Rs./nos)", value=float(cfg.get("loading_unloading_cost", 0)), min_value=0.0, step=0.05)
            new_pw   = cc4.number_input("Power (Rs./nos)",                 value=float(cfg["power_per_block"]), min_value=0.0, step=0.05)

            cc5, cc6 = st.columns(2)
            new_weld  = cc5.number_input("Welding Cost (Rs./nos)",         value=float(cfg.get("welding_cost", 0)), min_value=0.0, step=0.05)
            new_jalli = cc6.number_input("Jalli — Cage Welding (Rs./nos)", value=float(cfg.get("jalli_cost", 0)), min_value=0.0, step=0.05)

            st.markdown("**Raw material usage per unit** (Concrete m³ is pre-computed from diameter+barrel thickness for Hume Pipes)")
            mc1, mc2 = st.columns(2)
            new_concrete = mc1.number_input("Concrete (m³/Unit)", value=float(cfg.get("concrete_volume_m3", 0)), min_value=0.0, step=0.001, format="%.4f")
            new_steel    = mc2.number_input("Steel — HT Wire (Kg/Unit)", value=float(cfg.get("steel_kg_per_unit", 0)), min_value=0.0, step=0.1)

            st.caption(f"Fixed costs: EMI ₹20,000 · DG ₹5,000 · Admin ₹8,000 · Misc 10%")

            if st.form_submit_button("💾 Save", type="primary", use_container_width=True):
                save_product_config(sel_prod, {
                    "selling_price":          new_sell,
                    "production_cost":        new_prod,
                    "loading_unloading_cost": new_lu,
                    "power_per_block":        new_pw,
                    "welding_cost":           new_weld,
                    "jalli_cost":             new_jalli,
                    "concrete_volume_m3":     new_concrete,
                    "steel_kg_per_unit":      new_steel,
                })
                st.success(f"✅ {sel_prod} config saved.")
                st.rerun()

        st.markdown("---")
        st.markdown("**Current config — all products**")
        rows = []
        for prod, c in cfg_all.items():
            rows.append({
                "Product":         prod,
                "Sell (₹)":        c["selling_price"],
                "Production":      c.get("production_cost", 0),
                "Loading/Unload":  c.get("loading_unloading_cost", 0),
                "Power":           c["power_per_block"],
                "Welding":         c.get("welding_cost", 0),
                "Jalli":           c.get("jalli_cost", 0),
                "Concrete (m³)":   c.get("concrete_volume_m3", 0),
                "Steel (Kg)":      c.get("steel_kg_per_unit", 0),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── Tab 3: All Production ─────────────────────────────────────────────────
    with tab3:
        st.markdown("### All Production Records")
        c1, c2 = st.columns(2)
        from datetime import date
        start = c1.date_input("From", date.today().replace(day=1), key="prod_start")
        end   = c2.date_input("To",   date.today(), key="prod_end")

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
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", csv,
                               f"production_{start}_{end}.csv", "text/csv")

            # Delete record
            st.markdown("---")
            del_id = st.number_input("Delete record by ID", min_value=1, step=1)
            if st.button("🗑️ Delete", type="secondary"):
                delete_row("production", int(del_id))
                st.success(f"Record {del_id} deleted.")
                st.rerun()

    # ── Tab 4: All Dispatch ───────────────────────────────────────────────────
    with tab4:
        st.markdown("### All Dispatch Records")

        # One-time: mark all existing null bill_no entries as billed
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
        from datetime import date
        start2 = c3.date_input("From", date.today().replace(day=1), key="disp_start")
        end2   = c4.date_input("To",   date.today(), key="disp_end")

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

                if st.button("🔧 Recalculate & Fix All dispatch_value (qty × rate)", type="primary", key="fix_dv"):
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

            csv2 = df2.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Download CSV", csv2,
                               f"dispatch_{start2}_{end2}.csv", "text/csv",
                               key="dl_disp")

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

    # ── Tab 6: Activity Log (admin only) ──────────────────────────────────────
    with tab6:
        st.markdown("### Who opened / edited what")
        st.caption("Every login, page view, create, edit, and delete across the app — admin-only.")

        df_log = get_activity_log()
        if df_log.empty:
            st.info("No activity recorded yet.")
        else:
            # Supabase's TIMESTAMPTZ comes back tz-aware; strip the tz so it
            # can be compared against the tz-naive From/To date inputs below.
            df_log["created_at"] = pd.to_datetime(df_log["created_at"], errors="coerce", utc=True).dt.tz_localize(None)
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
