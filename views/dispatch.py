import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date
from core.config import DISPATCH_PRODUCTS, TRUCKS, DRIVERS, CLIENTS, SALE_TYPES
from core.calculations import dispatch_value
from core.db import insert_dispatch, get_dispatch, delete_row, update_dispatch
from core.ui import client_name_field, flash, show_flashes
from core.sequencing import next_sequence_number, is_duplicate

LAKH = 100_000


def _pending_mask(df):
    if "bill_no" not in df.columns:
        return pd.Series([True] * len(df), index=df.index)
    return df["bill_no"].isna() | (df["bill_no"].astype(str).str.strip().isin(["", "None", "nan"]))


def _show_headoffice():
    """Dedicated minimal view for headoffice: just bill entry form."""
    st.markdown("""
    <div class="page-title">🧾 Enter Bill No.</div>
    <div class="page-subtitle">Select a pending challan and assign its bill number</div>
    """, unsafe_allow_html=True)

    df_all = get_dispatch()
    if not df_all.empty and "sale_type" in df_all.columns:
        # Headoffice never bills Sale B — those challans are handled outside
        # this workflow, so they're excluded here regardless of DI status.
        df_all = df_all[df_all["sale_type"] != "Sale B"]
    if df_all.empty:
        st.info("No dispatch entries found.")
        return

    df_all["date"] = pd.to_datetime(df_all["date"], errors="coerce")
    df_all = df_all.sort_values(["date", "id"], ascending=[False, False]).reset_index(drop=True)

    pending = df_all[_pending_mask(df_all)].copy()

    if pending.empty:
        st.success("✅ All challans have Bill No. assigned. Nothing pending.")
        return

    pending["label"] = (
        pending["date"].dt.strftime("%d-%b-%Y") + " | Challan " +
        pending["challan_no"].fillna("").astype(str) + " | " +
        pending["client_name"].fillna("").astype(str) + " | " +
        pending["product"].fillna("").astype(str)
    )

    with st.form("ho_bill_form", clear_on_submit=True):
        sel = st.selectbox(f"Pending challan ({len(pending)} remaining)", pending["label"].tolist())
        bill_val = st.text_input("Bill No.", placeholder="e.g. INV-2026-001")
        if st.form_submit_button("💾 Save Bill No.", type="primary", use_container_width=True):
            if not bill_val.strip():
                st.error("Enter a Bill No.")
            else:
                rid = int(pending.loc[pending["label"] == sel, "id"].iloc[0])
                update_dispatch(rid, {"bill_no": bill_val.strip()})
                flash(f"✅ Bill No. {bill_val.strip()} saved!")
                st.success(f"✅ Saved! Bill No. **{bill_val.strip()}** assigned.")
                st.rerun()


def _show_dispatch_operator():
    """Minimal view for dispatch role: challan entry form only."""
    st.markdown("""
    <div class="page-title">🚚 Dispatch Entry</div>
    <div class="page-subtitle">Enter challan details below</div>
    """, unsafe_allow_html=True)

    df_known = get_dispatch()
    known_clients = set(df_known["client_name"].dropna().astype(str)) if not df_known.empty and "client_name" in df_known.columns else set()

    sale_type    = st.selectbox("Sale Type", SALE_TYPES, key="disp_op_sale_type")
    next_challan = next_sequence_number(df_known, "challan_no", sale_type, date_col="date")

    with st.form("dispatch_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        entry_date = c1.date_input("Date", date.today())
        # Keyed to the value itself so a fixed key doesn't "stick" to a
        # stale number from an earlier Sale Type selection.
        challan_no = c2.text_input("Challan No.", value=str(next_challan),
                                   key=f"disp_op_challan_{next_challan}", disabled=True,
                                   help="Auto-generated: next number for the selected Sale Type.")
        di_no      = c3.text_input("DI No.")

        ca, cb, cc = st.columns(3)
        client_name   = client_name_field(ca, known_clients, "disp_op_client")
        product       = cb.selectbox("Product", DISPATCH_PRODUCTS)
        delivery_addr = cc.text_input("Delivery Address")

        cd, ce, cf = st.columns(3)
        qty_ordered    = cd.number_input("Qty Ordered",              min_value=0,   step=100)
        qty_dispatched = ce.number_input("Qty Dispatched (Challan)", min_value=0,   step=100)
        rate           = cf.number_input("Rate All Inc. (₹/nos.)",   min_value=0.0, step=0.5)

        cg, ch, ci, cj = st.columns(4)
        truck_no       = cg.selectbox("Truck No.", TRUCKS)
        driver_name    = ch.selectbox("Driver Name", DRIVERS)
        trip_distance  = ci.number_input("Distance (km)", min_value=0.0, step=5.0)
        remarks        = cj.text_input("Remarks")
        form_filled_by = st.text_input("Form Filled By")

        submitted = st.form_submit_button("✅ Submit Challan", type="primary", use_container_width=True)

    if submitted:
        if is_duplicate(df_known, "challan_no", challan_no, sale_type=sale_type, date_col="date"):
            st.error(f"Challan No. {challan_no} already exists. Refresh the page and try again.")
        elif qty_dispatched <= 0:
            st.error("Qty Dispatched must be > 0.")
        elif rate <= 0:
            st.error("Rate must be > 0.")
        else:
            d_value = dispatch_value(qty_dispatched, rate)
            insert_dispatch({
                "date": str(entry_date), "challan_no": challan_no, "di_no": di_no,
                "bill_no": None, "sale_type": sale_type,
                "client_name": client_name, "delivery_address": delivery_addr,
                "product": product, "qty_ordered": qty_ordered,
                "qty_dispatched": qty_dispatched, "rate": rate,
                "dispatch_value": d_value, "trip_distance": trip_distance,
                "truck_no": truck_no, "driver_name": driver_name,
                "remarks": remarks, "form_filled_by": form_filled_by,
            })
            st.toast(f"✅ Challan {challan_no} saved!")
            st.markdown('<div class="success-box">✅ <b>Dispatch entry saved!</b></div>', unsafe_allow_html=True)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Qty Dispatched", f"{int(qty_dispatched):,} nos")
            m2.metric("Rate",           f"₹{rate:.2f}/nos")
            m3.metric("Dispatch Value", f"₹{d_value:,.0f}")
            m4.metric("Balance",        f"{int(qty_ordered - qty_dispatched):,} nos")


def show(PLOT):
    role = st.session_state.get("role", "dispatch")
    show_flashes()
    can_bill    = role in ("admin", "dispatch", "headoffice", "viewer")
    can_challan = role in ("admin", "dispatch", "viewer")

    if role == "headoffice":
        _show_headoffice()
        return

    if role == "dispatch":
        _show_dispatch_operator()
        return

    st.markdown("""
    <div class="page-title">🚚 Dispatch</div>
    <div class="page-subtitle">Challan entry · Dashboard · Invoice tracking</div>
    """, unsafe_allow_html=True)

    df_all = get_dispatch()
    if not df_all.empty:
        df_all["date"] = pd.to_datetime(df_all["date"], errors="coerce")

    # ── Date range filter ─────────────────────────────────────────────────────
    from datetime import date as _date
    _today = _date.today()
    fa, fb, fc = st.columns([1, 1, 1])
    f_start = fa.date_input("From", _today.replace(day=1), key="disp_f_start")
    f_end   = fb.date_input("To",   _today,               key="disp_f_end")
    all_time = fc.checkbox("All time", value=False,        key="disp_all_time")

    if not df_all.empty:
        if all_time:
            df = df_all.copy()
            period_label = "All Time"
        else:
            mask = (df_all["date"] >= pd.Timestamp(f_start)) & (df_all["date"] <= pd.Timestamp(f_end))
            df = df_all[mask].copy()
            period_label = f"{f_start.strftime('%d %b %Y')} – {f_end.strftime('%d %b %Y')}"
    else:
        df = df_all.copy()
        period_label = ""

    # ── KPIs ─────────────────────────────────────────────────────────────────
    if not df.empty:
        total_val   = df["dispatch_value"].sum()
        total_chal  = len(df)
        pending_cnt = _pending_mask(df).sum()
        billed_val  = df.loc[~_pending_mask(df), "dispatch_value"].sum()

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Challans",      f"{total_chal:,}")
        k2.metric("Total Dispatch Value",f"₹{total_val/LAKH:.2f}L")
        k3.metric("Invoiced Value",      f"₹{billed_val/LAKH:.2f}L")
        k4.metric("Pending Invoice",     f"{pending_cnt} challans")

        if "sale_type" in df.columns:
            a_val = df.loc[df["sale_type"] == "Sale A", "dispatch_value"].sum()
            b_val = df.loc[df["sale_type"] == "Sale B", "dispatch_value"].sum()
            kp1, kp2 = st.columns(2)
            kp1.metric("Sale A Dispatch Value", f"₹{a_val/LAKH:.2f}L")
            kp2.metric("Sale B Dispatch Value", f"₹{b_val/LAKH:.2f}L")

        st.markdown("---")

        COLORS = ["#8B2428","#3B82F6","#D4A011","#A78BFA","#27AE60","#22D3EE","#E05252","#E879F9","#F97316","#14B8A6","#888888"]

        # ── Monthly charts ────────────────────────────────────────────────────
        st.markdown(f'<div class="section-header">Dispatch Analytics — {period_label}</div>', unsafe_allow_html=True)

        df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()

        # Monthly total dispatch value
        m_total = df.groupby("month").agg(
            Value=("dispatch_value","sum"),
            Trips=("id","count"),
        ).reset_index().sort_values("month")
        m_total["month_str"] = m_total["month"].dt.strftime("%b %Y")

        st.markdown("**Monthly Dispatch Value (L)**")
        fig_mv = go.Figure(go.Bar(
            x=m_total["month_str"],
            y=(m_total["Value"]/LAKH).round(2),
            marker_color="#8B2428",
            text=(m_total["Value"]/LAKH).round(2).astype(str) + "L",
            textposition="outside",
        ))
        fig_mv.update_layout(**PLOT, height=320, yaxis_title="Value (L)")
        st.plotly_chart(fig_mv, use_container_width=True)

        # Monthly product-wise stacked bar
        st.markdown("**Monthly Dispatch Value by Product (L)**")
        m_prod = df.groupby(["month","product"])["dispatch_value"].sum().reset_index()
        m_prod["month_str"] = m_prod["month"].dt.strftime("%b %Y")
        month_labels = m_total["month_str"].tolist()
        products     = sorted(m_prod["product"].dropna().unique())
        fig_mp = go.Figure()
        for i, prod in enumerate(products):
            sub = m_prod[m_prod["product"] == prod][["month_str","dispatch_value"]]
            sub = sub.set_index("month_str").reindex(month_labels, fill_value=0).reset_index()
            fig_mp.add_trace(go.Bar(
                x=sub["month_str"],
                y=(sub["dispatch_value"]/LAKH).round(3),
                name=prod,
                marker_color=COLORS[i % len(COLORS)],
            ))
        fig_mp.update_layout(**PLOT, height=320, barmode="stack",
                             yaxis_title="Value (L)",
                             legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig_mp, use_container_width=True)

        # Client-wise + Product-wise pies
        col3, col4 = st.columns(2)
        with col3:
            st.markdown(f"**Top 10 Clients — {period_label}**")
            cl = df.groupby("client_name")["dispatch_value"].sum().reset_index().sort_values("dispatch_value", ascending=False)
            top10 = cl.head(10)
            others_val = cl.iloc[10:]["dispatch_value"].sum()
            if others_val > 0:
                top10 = pd.concat([top10, pd.DataFrame([{"client_name":"Others","dispatch_value":others_val}])], ignore_index=True)
            fig_cl = go.Figure(go.Pie(
                labels=top10["client_name"], values=top10["dispatch_value"],
                hole=0.42, textinfo="percent",
                hovertemplate="%{label}<br>₹%{value:,.0f}<br>%{percent}<extra></extra>",
                marker_colors=COLORS,
            ))
            fig_cl.update_layout(**PLOT, height=320, showlegend=True,
                                 legend=dict(orientation="v", x=1.02, y=0.5,
                                             font=dict(size=11)))
            st.plotly_chart(fig_cl, use_container_width=True)

        with col4:
            st.markdown(f"**Billed Value by Product — {period_label}**")
            df_billed = df[~_pending_mask(df)]
            src = df_billed if not df_billed.empty else df
            pv = src.groupby("product")["dispatch_value"].sum().reset_index().sort_values("dispatch_value", ascending=False)
            fig_pv = go.Figure(go.Pie(
                labels=pv["product"], values=pv["dispatch_value"],
                hole=0.42, textinfo="label+percent",
                marker_colors=COLORS,
            ))
            fig_pv.update_layout(**PLOT, height=300, showlegend=False)
            st.plotly_chart(fig_pv, use_container_width=True)

        # Top 10 clients bar
        st.markdown(f"**Top 10 Clients by Value — {period_label}**")
        cl_bar = df.groupby("client_name")["dispatch_value"].sum().reset_index().sort_values("dispatch_value", ascending=False)
        top10_b  = cl_bar.head(10)
        others_b = cl_bar.iloc[10:]["dispatch_value"].sum()
        if others_b > 0:
            top10_b = pd.concat([top10_b, pd.DataFrame([{"client_name":"Others","dispatch_value":others_b}])], ignore_index=True)
        fig_top = go.Figure(go.Bar(
            x=top10_b["dispatch_value"]/LAKH,
            y=top10_b["client_name"],
            orientation="h",
            marker_color=COLORS[:len(top10_b)],
            text=(top10_b["dispatch_value"]/LAKH).round(2).astype(str) + "L",
            textposition="outside",
        ))
        fig_top.update_layout(**PLOT, height=360, xaxis_title="Value (L)",
                              yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig_top, use_container_width=True)

        # Monthly breakup table
        st.markdown('<div class="section-header">Monthly Breakup Table</div>', unsafe_allow_html=True)
        tbl = m_total.copy()
        tbl["Value (L)"] = (tbl["Value"]/LAKH).round(3)
        st.dataframe(
            tbl[["month_str","Trips","Value (L)"]].rename(columns={"month_str":"Month","Trips":"Challans"}),
            use_container_width=True, hide_index=True,
        )

        st.markdown("---")

    # ── New Challan Entry (dispatch + admin only) ─────────────────────────────
    if can_challan:
        st.markdown('<div class="section-header">New Challan Entry</div>', unsafe_allow_html=True)

        sale_type          = st.selectbox("Sale Type", SALE_TYPES, key="disp_main_sale_type")
        next_challan_main  = next_sequence_number(df_all, "challan_no", sale_type, date_col="date")

        with st.form("dispatch_form", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns(4)
            entry_date = c1.date_input("Date", date.today())
            # Keyed to the value itself so a fixed key doesn't "stick" to a
            # stale number from an earlier Sale Type selection.
            challan_no = c2.text_input("Challan No.", value=str(next_challan_main),
                                       key=f"disp_main_challan_{next_challan_main}", disabled=True,
                                       help="Auto-generated: next number for the selected Sale Type.")
            di_no      = c3.text_input("DI No.")
            bill_no    = c4.text_input("Bill No.") if can_bill else None

            known_clients = set(df_all["client_name"].dropna().astype(str)) if not df_all.empty and "client_name" in df_all.columns else set()
            ca, cb, cc = st.columns(3)
            client_name   = client_name_field(ca, known_clients, "disp_main_client")
            product       = cb.selectbox("Product", DISPATCH_PRODUCTS)
            delivery_addr = cc.text_input("Delivery Address")

            cd, ce, cf = st.columns(3)
            qty_ordered    = cd.number_input("Qty Ordered",              min_value=0,   step=100)
            qty_dispatched = ce.number_input("Qty Dispatched (Challan)", min_value=0,   step=100)
            rate           = cf.number_input("Rate All Inc. (₹/nos.)",   min_value=0.0, step=0.5)

            cg, ch, ci, cj = st.columns(4)
            truck_no       = cg.selectbox("Truck No.", TRUCKS)
            driver_name    = ch.selectbox("Driver Name", DRIVERS)
            trip_distance  = ci.number_input("Distance (km)", min_value=0.0, step=5.0)
            remarks        = cj.text_input("Remarks")
            form_filled_by = st.text_input("Form Filled By")

            submitted = st.form_submit_button("✅ Submit Challan", type="primary", use_container_width=True)

        if submitted:
            if is_duplicate(df_all, "challan_no", challan_no, sale_type=sale_type, date_col="date"):
                st.error(f"Challan No. {challan_no} already exists. Refresh the page and try again.")
            elif qty_dispatched <= 0:
                st.error("Qty Dispatched must be > 0.")
            elif rate <= 0:
                st.error("Rate must be > 0.")
            else:
                d_value = dispatch_value(qty_dispatched, rate)
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Qty Dispatched", f"{int(qty_dispatched):,} nos")
                m2.metric("Rate",           f"₹{rate:.2f}/nos")
                m3.metric("Dispatch Value", f"₹{d_value:,.0f}")
                m4.metric("Balance",        f"{int(qty_ordered - qty_dispatched):,} nos")
                insert_dispatch({
                    "date": str(entry_date), "challan_no": challan_no, "di_no": di_no,
                    "bill_no": (bill_no.strip() if bill_no and bill_no.strip() else None),
                    "sale_type": sale_type,
                    "client_name": client_name, "delivery_address": delivery_addr,
                    "product": product, "qty_ordered": qty_ordered,
                    "qty_dispatched": qty_dispatched, "rate": rate,
                    "dispatch_value": d_value, "trip_distance": trip_distance,
                    "truck_no": truck_no, "driver_name": driver_name,
                    "remarks": remarks, "form_filled_by": form_filled_by,
                })
                st.toast(f"✅ Challan {challan_no} saved!")
            st.markdown('<div class="success-box">✅ <b>Dispatch entry saved!</b></div>', unsafe_allow_html=True)

    # ── All Dispatch Entries (filtered by date range above) ───────────────────
    st.markdown("---")
    # Use all data for edit/delete (not just filtered), but show filtered entries
    df_edit = df_all.copy() if not df_all.empty else pd.DataFrame()
    if df_edit.empty:
        st.info("No dispatch entries yet.")
        return

    df_edit["date"] = pd.to_datetime(df_edit["date"], errors="coerce")
    df_edit = df_edit.sort_values(["date","id"], ascending=[False,False]).reset_index(drop=True)

    # Display uses the filtered df (same date range as analytics)
    df = df.sort_values(["date","id"], ascending=[False,False]).reset_index(drop=True) if not df.empty else df_edit

    from core.ui import table_by_sale_type

    show_cols = ["date","challan_no","bill_no","sale_type","client_name","product",
                 "qty_dispatched","dispatch_value","truck_no","driver_name","trip_distance","remarks"]
    show_cols = [c for c in show_cols if c in df.columns]
    rename_map = {
        "date":"Date","challan_no":"Challan","bill_no":"Bill No.","sale_type":"Sale Type",
        "client_name":"Client","product":"Product",
        "qty_dispatched":"Qty","dispatch_value":"Value (₹)",
        "truck_no":"Truck","driver_name":"Driver","trip_distance":"Dist km","remarks":"Remarks",
    }
    sum_cols = [c for c in ["qty_dispatched","dispatch_value","trip_distance"] if c in df.columns]
    col_cfg  = {"date": st.column_config.DateColumn("Date", format="DD-MMM-YYYY")}

    pending_mask = _pending_mask(df)
    df_pending = df[pending_mask.values].copy()
    df_billed  = df[~pending_mask.values].copy()

    if not df_pending.empty:
        st.markdown(f'<div class="warn-box">⏳ <b>{len(df_pending)} challans pending invoice</b> — Bill No. not yet assigned</div>', unsafe_allow_html=True)
        table_by_sale_type(df_pending, key="disp_pend", sum_cols=sum_cols,
                           show_cols=show_cols, rename=rename_map, col_config=col_cfg)

    if not df_billed.empty:
        st.markdown('<div class="section-header">Invoiced Challans</div>', unsafe_allow_html=True)
        table_by_sale_type(df_billed, key="disp_billed", sum_cols=sum_cols,
                           show_cols=show_cols, rename=rename_map, col_config=col_cfg)

    # ── Edit Entry (uses all data, not date-filtered) ─────────────────────────
    st.markdown("---")
    with st.expander("✏️ Edit Dispatch Entry"):
        df_edit["label"] = (
            df_edit["date"].dt.strftime("%d-%b-%Y") + " | Challan " +
            df_edit["challan_no"].fillna("").astype(str) + " | " +
            df_edit["client_name"].fillna("").astype(str) + " | " +
            df_edit["product"].fillna("").astype(str) +
            " | ID:" + df_edit["id"].astype(str)
        )
        edit_label = st.selectbox("Select entry to edit", df_edit["label"].tolist(), key="edit_disp_sel")
        erow = df_edit.loc[df_edit["label"] == edit_label].iloc[0]

        with st.form("edit_disp_form"):
            ea, eb, ec = st.columns(3)
            e_date   = ea.date_input("Date", pd.to_datetime(erow["date"]).date())
            e_challan= eb.text_input("Challan No.", value=str(erow.get("challan_no","") or ""))
            e_di     = ec.text_input("DI No.",      value=str(erow.get("di_no","") or ""))

            edit_known_clients = set(df_edit["client_name"].dropna().astype(str)) if "client_name" in df_edit.columns else set()
            ed, ee, ef, est = st.columns(4)
            e_client = client_name_field(ed, edit_known_clients, "disp_edit_client",
                                         default=str(erow.get("client_name", "") or ""))
            e_prod   = ee.selectbox("Product", DISPATCH_PRODUCTS,
                                    index=DISPATCH_PRODUCTS.index(erow["product"])
                                    if erow.get("product") in DISPATCH_PRODUCTS else 0)
            e_addr   = ef.text_input("Delivery Address", value=str(erow.get("delivery_address","") or ""))
            _est     = str(erow.get("sale_type","") or "")
            e_stype  = est.selectbox("Sale Type", SALE_TYPES,
                                     index=SALE_TYPES.index(_est) if _est in SALE_TYPES else 0)

            eg, eh, ei = st.columns(3)
            e_qty_o  = eg.number_input("Qty Ordered",    value=float(erow.get("qty_ordered",0) or 0), min_value=0.0, step=100.0)
            e_qty_d  = eh.number_input("Qty Dispatched", value=float(erow.get("qty_dispatched",0) or 0), min_value=0.0, step=100.0)
            e_rate   = ei.number_input("Rate (₹/nos.)",  value=float(erow.get("rate",0) or 0), min_value=0.0, step=0.5)

            ej, ek, el = st.columns(3)
            e_truck  = ej.text_input("Truck No.",    value=str(erow.get("truck_no","") or ""))
            e_driver = ek.text_input("Driver Name",  value=str(erow.get("driver_name","") or ""))
            e_dist   = el.number_input("Distance km",value=float(erow.get("trip_distance",0) or 0), min_value=0.0, step=5.0)

            e_rem    = st.text_input("Remarks", value=str(erow.get("remarks","") or ""))

            if can_bill:
                e_bill = st.text_input("Bill No.", value=str(erow.get("bill_no","") or ""),
                                       help="Only accounts/admin can update this")
            else:
                e_bill = None
                st.caption(f"Bill No.: **{erow.get('bill_no','—') or '—'}** (only accounts team can edit)")

            if st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True):
                new_dv = round(float(e_qty_d) * float(e_rate), 2)
                payload = {
                    "date": str(e_date), "challan_no": e_challan, "di_no": e_di,
                    "client_name": e_client, "delivery_address": e_addr, "product": e_prod,
                    "sale_type": e_stype,
                    "qty_ordered": e_qty_o, "qty_dispatched": e_qty_d, "rate": e_rate,
                    "dispatch_value": new_dv, "trip_distance": e_dist,
                    "truck_no": e_truck, "driver_name": e_driver, "remarks": e_rem,
                }
                if can_bill and e_bill is not None:
                    payload["bill_no"] = e_bill.strip() if e_bill.strip() else None
                update_dispatch(int(erow["id"]), payload)
                flash("✅ Dispatch entry updated!")
                st.success(f"✅ Entry updated. Dispatch Value = ₹{new_dv:,.0f}")
                st.rerun()

    # ── Add Bill No. (Accounts / Admin only) — uses all data ─────────────────
    if can_bill:
        st.markdown("---")
        with st.expander("🧾 Add / Update Bill No. (Accounts Team)"):
            pending_b = df_edit[_pending_mask(df_edit)].copy()
            if pending_b.empty:
                st.success("✅ All challans have Bill No. assigned.")
            else:
                pending_b["label"] = (
                    pending_b["date"].dt.strftime("%d-%b-%Y") + " | Challan " +
                    pending_b["challan_no"].fillna("").astype(str) + " | " +
                    pending_b["client_name"].fillna("").astype(str) +
                    " | ₹" + pending_b["dispatch_value"].fillna(0).astype(int).astype(str)
                )
                sel_bill = st.selectbox("Select challan", pending_b["label"].tolist(), key="bill_sel2")
                bill_in  = st.text_input("Bill No.", placeholder="e.g. INV-2026-001", key="bill_in2")
                if st.button("💾 Save Bill No.", type="primary", key="save_bill2"):
                    if not bill_in.strip():
                        st.error("Enter a Bill No.")
                    else:
                        rid = int(pending_b.loc[pending_b["label"] == sel_bill, "id"].iloc[0])
                        update_dispatch(rid, {"bill_no": bill_in.strip()})
                        flash(f"✅ Bill No. {bill_in.strip()} saved!")
                        st.success(f"✅ Bill No. **{bill_in.strip()}** saved.")
                        st.rerun()

    # ── Driver & Truck summary (filtered period) ──────────────────────────────
    st.markdown("---")
    st.markdown(f'<div class="section-header">Driver & Truck Summary — {period_label}</div>', unsafe_allow_html=True)
    col5, col6 = st.columns(2)
    with col5:
        drv = df.groupby("driver_name").agg(
            Trips=("id","count"),
            Billed_Value=("dispatch_value","sum"),
        ).reset_index().rename(columns={"driver_name":"Driver"})
        drv["Billed Value (L)"] = (drv["Billed_Value"]/LAKH).round(3)
        st.dataframe(drv[["Driver","Trips","Billed Value (L)"]].sort_values("Billed Value (L)", ascending=False),
                     use_container_width=True, hide_index=True)

    with col6:
        trk = df.groupby("truck_no").agg(
            Trips=("id","count"),
            Total_km=("trip_distance","sum"),
            Billed_Value=("dispatch_value","sum"),
        ).reset_index().rename(columns={"truck_no":"Truck"})
        trk["Billed Value (L)"] = (trk["Billed_Value"]/LAKH).round(3)
        st.dataframe(trk[["Truck","Trips","Total_km","Billed Value (L)"]].sort_values("Billed Value (L)", ascending=False),
                     use_container_width=True, hide_index=True)

    # ── Delete (admin only) ───────────────────────────────────────────────────
    if role == "admin":
        st.markdown("---")
        with st.expander("🗑️ Bulk Delete Dispatch Entries"):
            from core.db import delete_dispatch_ids
            st.caption("Filter entries below, then delete all matching in one click.")

            da, db_, dc, dd = st.columns(4)
            del_start  = da.date_input("From", value=df_edit["date"].min().date() if not df_edit.empty else _date.today(), key="del_from")
            del_end    = db_.date_input("To",  value=df_edit["date"].max().date() if not df_edit.empty else _date.today(), key="del_to")
            all_clients  = ["All"] + sorted(df_edit["client_name"].dropna().unique().tolist())
            all_products = ["All"] + sorted(df_edit["product"].dropna().unique().tolist())
            del_client  = dc.selectbox("Client",  all_clients,  key="del_client")
            del_product = dd.selectbox("Product", all_products, key="del_product")

            del_mask = (
                (df_edit["date"] >= pd.Timestamp(del_start)) &
                (df_edit["date"] <= pd.Timestamp(del_end))
            )
            if del_client != "All":
                del_mask &= df_edit["client_name"] == del_client
            if del_product != "All":
                del_mask &= df_edit["product"] == del_product

            del_preview = df_edit[del_mask]
            st.info(f"**{len(del_preview)} records** match these filters  |  Total value: ₹{del_preview['dispatch_value'].sum()/LAKH:.2f}L")

            if not del_preview.empty:
                st.dataframe(
                    del_preview[["id","date","challan_no","client_name","product","dispatch_value"]]
                    .rename(columns={"id":"ID","date":"Date","challan_no":"Challan",
                                     "client_name":"Client","product":"Product","dispatch_value":"Value (₹)"})
                    .assign(Date=del_preview["date"].dt.strftime("%d-%b-%Y")),
                    use_container_width=True, hide_index=True,
                )
                del_confirm = st.text_input("Type DELETE to confirm", key="del_confirm_bulk")
                if st.button(f"🗑️ Delete {len(del_preview)} records", type="primary", key="del_bulk_btn"):
                    if del_confirm.strip() == "DELETE":
                        delete_dispatch_ids(del_preview["id"].tolist())
                        flash(f"🗑️ {len(del_preview)} records deleted.")
                        st.success(f"✅ {len(del_preview)} records deleted.")
                        st.rerun()
                    else:
                        st.error("Type exactly DELETE to confirm.")
