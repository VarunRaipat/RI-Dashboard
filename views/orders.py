import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date
from core.config import ORDER_PRODUCTS, PAYMENT_MODES, CLIENT_TYPES, SALE_TYPES, FACTORIES, JOINT_TYPES, HUME_PIPE_PRODUCTS, HUME_PIPE_JOINT_TYPES
from core.db import insert_order, get_orders, get_order_by_di, update_order, delete_order, get_dispatch
from core.pdf import generate_dispatch_instruction
from core.ui import client_name_field, flash, show_flashes
from core.sequencing import next_sequence_number, is_duplicate

LAKH = 100_000


def _dispatch_summary(df_disp):
    if df_disp.empty or "di_no" not in df_disp.columns:
        return pd.DataFrame(columns=["di_no","product","dispatched_qty","dispatched_value","challans"])
    g = df_disp.groupby(["di_no","product"]).agg(
        dispatched_qty=("qty_dispatched","sum"),
        dispatched_value=("dispatch_value","sum"),
        challans=("challan_no", lambda x: ", ".join(x.dropna().astype(str).unique())),
    ).reset_index()
    return g


def _init_lines():
    if "order_lines" not in st.session_state:
        st.session_state.order_lines = 1


def show(PLOT):
    role = st.session_state.get("role", "admin")
    show_flashes()

    st.markdown("""
    <div class="page-title">📦 Sales Orders</div>
    <div class="page-subtitle">Order entry · Dispatch tracking · DI pipeline</div>
    """, unsafe_allow_html=True)

    if st.session_state.get("last_di_pdf"):
        st.markdown(
            f'<div class="success-box">📄 Dispatch Instruction ready for DI '
            f'<b>{st.session_state["last_di_no"]}</b></div>',
            unsafe_allow_html=True,
        )
        pcol1, pcol2, _ = st.columns([1.4, 1, 3])
        pcol1.download_button(
            "⬇️ Download Dispatch Instruction PDF",
            data=st.session_state["last_di_pdf"],
            file_name=f"DI_{st.session_state['last_di_no']}.pdf",
            mime="application/pdf", key="dl_last_di", use_container_width=True,
        )
        if pcol2.button("✕ Dismiss", key="dismiss_last_di_pdf", use_container_width=True):
            del st.session_state["last_di_pdf"]
            del st.session_state["last_di_no"]
            st.rerun()
        st.markdown("---")

    df_orders = get_orders()
    df_disp   = get_dispatch()
    df_orders_raw = df_orders  # unfiltered — used for DI numbering & dup checks

    # Headoffice sees Sale B orders/dispatch only while a DI is pending or
    # partially dispatched, plus a short grace period after it's fulfilled.
    if role not in ("admin", "viewer"):
        from core.visibility import hidden_sale_b_dis
        hidden_dis = hidden_sale_b_dis(df_orders, df_disp)
        if not df_orders.empty and "sale_type" in df_orders.columns:
            hide_mask = (df_orders["sale_type"] == "Sale B") & \
                        (df_orders["di_no"].astype(str).str.strip().isin(hidden_dis))
            df_orders = df_orders[~hide_mask]
        if not df_disp.empty and "sale_type" in df_disp.columns:
            hide_mask_d = (df_disp["sale_type"] == "Sale B") & \
                          (df_disp["di_no"].astype(str).str.strip().isin(hidden_dis))
            df_disp = df_disp[~hide_mask_d]

    if not df_orders.empty:
        df_orders["order_date"] = pd.to_datetime(df_orders["order_date"], errors="coerce")

    disp_summary = _dispatch_summary(df_disp)

    # ── Date filter (drives KPIs + Order Pipeline below) ─────────────────────
    from core.ui import quick_date_range_filter
    ord_start, ord_end = quick_date_range_filter(
        "ord", default_start=df_orders["order_date"].min().date() if not df_orders.empty else None
    )
    df_orders_kpi = (
        df_orders[(df_orders["order_date"] >= pd.Timestamp(ord_start)) &
                  (df_orders["order_date"] <= pd.Timestamp(ord_end))]
        if not df_orders.empty else df_orders
    )

    # ── KPIs (admin only) ────────────────────────────────────────────────────
    if role != "headoffice" and not df_orders_kpi.empty:
        order_dis = set(df_orders_kpi["di_no"].dropna().astype(str).str.strip()) - {""}
        # Only count dispatch value against DIs that actually exist as Sales
        # Orders — otherwise unrelated/legacy dispatch history (which predates
        # this module) drags "Pending Dispatch" to a wildly wrong number.
        disp_matched = (
            disp_summary[disp_summary["di_no"].astype(str).str.strip().isin(order_dis)]
            if not disp_summary.empty else disp_summary
        )

        total_ordered_val = df_orders_kpi["total_amount"].sum()
        total_disp_val    = disp_matched["dispatched_value"].sum() if not disp_matched.empty else 0
        pending_val       = total_ordered_val - total_disp_val
        open_dis          = df_orders_kpi["di_no"].nunique()

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Open DIs",          f"{open_dis}")
        k2.metric("Total Order Value", f"₹{total_ordered_val/LAKH:.2f}L")
        k3.metric("Total Dispatched",  f"₹{total_disp_val/LAKH:.2f}L")
        k4.metric("Pending Dispatch",  f"₹{pending_val/LAKH:.2f}L")

        if "sale_type" in df_orders_kpi.columns:
            a_val = df_orders_kpi.loc[df_orders_kpi["sale_type"] == "Sale A", "total_amount"].sum()
            b_val = df_orders_kpi.loc[df_orders_kpi["sale_type"] == "Sale B", "total_amount"].sum()
            kp1, kp2 = st.columns(2)
            kp1.metric("Sale A Order Value", f"₹{a_val/LAKH:.2f}L")
            kp2.metric("Sale B Order Value", f"₹{b_val/LAKH:.2f}L")

        st.markdown("---")

    # ── New Order Entry ───────────────────────────────────────────────────────
    st.markdown('<div class="section-header">New Order Entry</div>', unsafe_allow_html=True)

    _init_lines()

    existing_dis = sorted(df_orders["di_no"].dropna().unique().tolist()) if not df_orders.empty else []
    di_mode = st.radio("DI", ["New DI", "Add product to existing DI"], horizontal=True, key="di_mode")

    # ── Header fields ─────────────────────────────────────────────────────────
    if di_mode == "Add product to existing DI" and existing_dis:
        sel_di = st.selectbox("Select DI", existing_dis, key="sel_existing_di")
        hdr_row = df_orders[df_orders["di_no"] == sel_di].iloc[0] if not df_orders.empty else None
        di_no_val = sel_di
    else:
        sel_di   = None
        hdr_row  = None
        di_no_val = None

    REQUIRED_PLACEHOLDER = "— Select —"

    def _hv(field):
        return str(hdr_row.get(field, "") or "") if hdr_row is not None else ""

    h1, h2, h3, h4 = st.columns(4)

    # Sale Type is rendered first (into its h4 slot) so its *return value* —
    # not a session_state read, which wouldn't reflect this run's pick until
    # the widget line itself executes — can drive the DI No. sequence below.
    _st_opts    = [REQUIRED_PLACEHOLDER] + SALE_TYPES
    _st_default = _hv("sale_type")
    sale_type    = h4.selectbox("Sale Type *", _st_opts,
                                index=_st_opts.index(_st_default) if _st_default in SALE_TYPES else 0,
                                key="ord_sale_type")

    if di_mode == "Add product to existing DI" and existing_dis:
        di_no_display = str(di_no_val or "")
    elif sale_type in SALE_TYPES:
        di_no_display = str(next_sequence_number(df_orders_raw, "di_no", sale_type))
    else:
        di_no_display = ""

    # Keyed to the value itself (not a fixed key) so Streamlit always shows
    # the freshly computed number — a fixed key would "stick" to whatever
    # was in session_state from the first render and ignore later `value=`.
    di_no_input  = h1.text_input("DI No.", value=di_no_display, key=f"ord_di_no_{di_no_display}",
                                 disabled=True,
                                 help="Auto-generated: next number for the selected Sale Type.")
    order_date   = h2.date_input("Order Date", value=date.today(), key="ord_date")

    _pm_opts    = [REQUIRED_PLACEHOLDER] + PAYMENT_MODES
    _pm_default = _hv("mode_of_payment")
    payment_mode = h3.selectbox("Payment Mode *", _pm_opts,
                                index=_pm_opts.index(_pm_default) if _pm_default in PAYMENT_MODES else 0,
                                key="ord_payment")

    known_clients = set()
    if not df_orders.empty and "client_name" in df_orders.columns:
        known_clients |= set(df_orders["client_name"].dropna().astype(str))
    if not df_disp.empty and "client_name" in df_disp.columns:
        known_clients |= set(df_disp["client_name"].dropna().astype(str))

    i1, i2, i3 = st.columns(3)
    client_name    = client_name_field(i1, known_clients, "ord_client", default=_hv("client_name"))

    # New DI for a repeat client: pull contact/site details from that client's
    # most recent order so they don't have to be retyped — still fully editable.
    # Keys are scoped to the client name so switching clients (or typing a new
    # one) refreshes these fields to the right defaults, while edits made
    # while a given client is selected persist across reruns as usual.
    repeat_row = None
    if hdr_row is None and client_name.strip() and not df_orders_raw.empty and "client_name" in df_orders_raw.columns:
        matches = df_orders_raw[
            df_orders_raw["client_name"].astype(str).str.strip().str.lower() == client_name.strip().lower()
        ]
        if not matches.empty:
            repeat_row = matches.sort_values("order_date", ascending=False).iloc[0]

    def _cv(field):
        if hdr_row is not None:
            return str(hdr_row.get(field, "") or "")
        if repeat_row is not None:
            return str(repeat_row.get(field, "") or "")
        return ""

    _ck = client_name.strip() or "new"
    if repeat_row is not None:
        i1.caption(f"↻ Repeat client — details prefilled from DI {repeat_row.get('di_no','')}")
    contact_person = i2.text_input("Contact Person Name", value=_cv("contact_person"), key=f"ord_contact_person_{_ck}")
    phone          = i3.text_input("Phone",               value=_cv("phone"),          key=f"ord_phone_{_ck}")

    j1, j2, j3 = st.columns(3)
    _ct_opts    = [REQUIRED_PLACEHOLDER] + CLIENT_TYPES
    _ct_default = _cv("client_type")
    client_type = j1.selectbox("Client Type *", _ct_opts,
                 index=_ct_opts.index(_ct_default) if _ct_default in CLIENT_TYPES else 0,
                 key=f"ord_client_type_{_ck}")
    office = j2.text_input("Office",  value=_cv("office"), key=f"ord_office_{_ck}")
    gstin  = j3.text_input("GSTIN",   value=_cv("gstin"),  key=f"ord_gstin_{_ck}")

    st.caption("*Client Name, Client Type, Payment Mode, and Sale Type are required.")

    delivery_addr = st.text_input("Site Address", value=_cv("delivery_address"), key=f"ord_addr_{_ck}")

    k1, k2 = st.columns(2)
    site_person = k1.text_input("Site Person",   value=_cv("site_person"), key=f"ord_site_person_{_ck}")
    site_phone  = k2.text_input("Site Phone No.", value=_cv("site_phone"),  key=f"ord_site_phone_{_ck}")

    remarks = st.text_input("Remarks", value=_hv("remarks"), key="ord_remarks")

    # ── Product lines ─────────────────────────────────────────────────────────
    st.markdown("**Product Lines**")
    st.caption("Add one line per product. Rate is all-inclusive.")

    n_lines = st.session_state.order_lines
    header_cols = st.columns([3, 2, 2, 2, 1])
    header_cols[0].markdown("**Product**")
    header_cols[1].markdown("**Qty Ordered**")
    header_cols[2].markdown("**Rate (₹/nos)**")
    header_cols[3].markdown("**Total (₹)**")

    for i in range(n_lines):
        cols = st.columns([3, 2, 2, 2, 1])
        line_prod = cols[0].selectbox("Product", ORDER_PRODUCTS, key=f"ord_prod_{i}", label_visibility="collapsed")
        cols[1].number_input("Qty", min_value=0.0, step=100.0,   key=f"ord_qty_{i}",   label_visibility="collapsed")
        cols[2].number_input("Rate", min_value=0.0, step=0.5, key=f"ord_rate_{i}", label_visibility="collapsed")
        # Live total — plain markdown (not a keyed widget) so it always reflects
        # the current Qty/Rate instead of a stale value from a previous rerun.
        qty_v  = st.session_state.get(f"ord_qty_{i}", 0) or 0
        rate_v = st.session_state.get(f"ord_rate_{i}", 0.0) or 0.0
        cols[3].markdown(
            f"<div style='padding:9px 0;font-weight:600;'>₹{float(qty_v) * float(rate_v):,.2f}</div>",
            unsafe_allow_html=True,
        )
        if n_lines > 1:
            if cols[4].button("✕", key=f"ord_rem_{i}"):
                # remove this line by shifting keys
                for j in range(i, n_lines - 1):
                    st.session_state[f"ord_prod_{j}"]  = st.session_state.get(f"ord_prod_{j+1}",  ORDER_PRODUCTS[0])
                    st.session_state[f"ord_qty_{j}"]   = st.session_state.get(f"ord_qty_{j+1}",   0)
                    st.session_state[f"ord_rate_{j}"]  = st.session_state.get(f"ord_rate_{j+1}",  0.0)
                    st.session_state[f"ord_joint_{j}"] = st.session_state.get(f"ord_joint_{j+1}", JOINT_TYPES[0])
                st.session_state.order_lines = n_lines - 1
                st.rerun()

        # Joint Type is a spec, not a price driver — only relevant for Hume Pipes,
        # and only the types actually manufactured for that diameter+class.
        if line_prod in HUME_PIPE_PRODUCTS:
            allowed_joints = HUME_PIPE_JOINT_TYPES.get(line_prod, JOINT_TYPES)
            jkey = f"ord_joint_{i}"
            if st.session_state.get(jkey) not in allowed_joints:
                st.session_state[jkey] = allowed_joints[0]
            jcols = st.columns([3, 4])
            jcols[0].selectbox("Joint Type", allowed_joints, key=jkey)

    ca, cb = st.columns([1, 5])
    if ca.button("➕ Add Product", key="ord_add_line"):
        st.session_state.order_lines += 1
        st.rerun()

    # ── Submit ────────────────────────────────────────────────────────────────
    st.markdown("")
    if st.button("✅ Save Order", type="primary", use_container_width=True, key="ord_submit"):
        di_no_final  = di_no_display.strip()
        _payment_val = st.session_state.get("ord_payment", REQUIRED_PLACEHOLDER)
        _sale_val    = st.session_state.get("ord_sale_type", REQUIRED_PLACEHOLDER)
        _ctype_val   = client_type

        missing = []
        if not di_no_final:            missing.append("DI No.")
        if not client_name.strip():    missing.append("Client Name")
        if _ctype_val == REQUIRED_PLACEHOLDER:   missing.append("Client Type")
        if _payment_val == REQUIRED_PLACEHOLDER: missing.append("Payment Mode")
        if _sale_val == REQUIRED_PLACEHOLDER:    missing.append("Sale Type")

        if missing:
            st.error(f"Required: {', '.join(missing)}.")
        elif di_mode == "New DI" and is_duplicate(df_orders_raw, "di_no", di_no_final):
            st.error(f"DI No. {di_no_final} already exists. Refresh the page and try again.")
        else:
            common_fields = {
                "order_date":       str(st.session_state.get("ord_date", date.today())),
                "client_name":      client_name,
                "contact_person":   contact_person,
                "phone":            phone,
                "office":           office,
                "gstin":            gstin,
                "client_type":      _ctype_val,
                "mode_of_payment":  _payment_val,
                "sale_type":        _sale_val,
                "delivery_address": delivery_addr,
                "site_person":      site_person,
                "site_phone":       site_phone,
                "remarks":          st.session_state.get("ord_remarks", ""),
            }
            saved = 0
            pdf_lines = []
            for i in range(st.session_state.order_lines):
                qty_v  = float(st.session_state.get(f"ord_qty_{i}", 0) or 0)
                rate_v = float(st.session_state.get(f"ord_rate_{i}", 0.0) or 0.0)
                if qty_v <= 0:
                    continue
                prod        = st.session_state.get(f"ord_prod_{i}", ORDER_PRODUCTS[0])
                total_final = round(qty_v * rate_v, 2)
                joint_type = st.session_state.get(f"ord_joint_{i}", "") if prod in HUME_PIPE_PRODUCTS else ""
                insert_order({
                    **common_fields,
                    "di_no":            di_no_final,
                    "factory":          FACTORIES[0],
                    "product":          prod,
                    "joint_type":       joint_type,
                    "qty_ordered":      qty_v,
                    "rate":             rate_v,
                    "total_amount":     total_final,
                })
                pdf_lines.append({"product": prod, "joint_type": joint_type, "qty_ordered": qty_v, "rate": rate_v, "total_amount": total_final})
                saved += 1
            if saved:
                pdf_header = dict(common_fields)
                st.session_state["last_di_pdf"] = generate_dispatch_instruction(di_no_final, pdf_header, pdf_lines)
                st.session_state["last_di_no"]  = di_no_final
                flash(f"✅ Order saved — DI {di_no_final}")
                # reset lines
                st.session_state.order_lines = 1
                for k in list(st.session_state.keys()):
                    if k.startswith(("ord_prod_","ord_qty_","ord_rate_","ord_total_")):
                        del st.session_state[k]
                st.rerun()
            else:
                st.error("Enter qty > 0 for at least one product line.")


    # ── Order Pipeline ────────────────────────────────────────────────────────
    if df_orders.empty:
        st.info("No orders yet.")
        return

    st.markdown("---")
    st.markdown('<div class="section-header">Order Pipeline</div>', unsafe_allow_html=True)

    df_orders = df_orders_kpi
    if df_orders.empty:
        st.info("No orders in this date range.")
        return

    _agg = dict(
        order_date  =("order_date","first"),
        client_name =("client_name","first"),
        payment_mode=("mode_of_payment","first"),
        products    =("product", lambda x: ", ".join(x.dropna().unique())),
        total_ordered=("total_amount","sum"),
        qty_ordered  =("qty_ordered","sum"),
    )
    if "client_type" in df_orders.columns:
        _agg["client_type"] = ("client_type", "first")
    if "sale_type" in df_orders.columns:
        _agg["sale_type"] = ("sale_type", "first")
    di_summary = df_orders.groupby("di_no").agg(**_agg).reset_index().sort_values("order_date", ascending=False)

    if not disp_summary.empty:
        disp_di = disp_summary.groupby("di_no").agg(
            dispatched_value=("dispatched_value","sum"),
            dispatched_qty  =("dispatched_qty","sum"),
            challans        =("challans","first"),
        ).reset_index()
        di_summary = di_summary.merge(disp_di, on="di_no", how="left")
    else:
        di_summary["dispatched_value"] = 0
        di_summary["dispatched_qty"]   = 0
        di_summary["challans"]         = ""

    di_summary["dispatched_value"] = di_summary["dispatched_value"].fillna(0)
    di_summary["dispatched_qty"]   = di_summary["dispatched_qty"].fillna(0)
    di_summary["pending_value"]    = di_summary["total_ordered"] - di_summary["dispatched_value"]
    di_summary["pending_qty"]      = di_summary["qty_ordered"]   - di_summary["dispatched_qty"]

    def _status(row):
        if row["dispatched_qty"] <= 0:  return "🔴 Pending"
        if row["pending_qty"] > 1:      return "🟡 Partial"
        return "🟢 Fulfilled"

    di_summary["Status"] = di_summary.apply(_status, axis=1)

    from core.ui import table_by_sale_type

    for col in ["total_ordered","dispatched_value","pending_value","qty_ordered","dispatched_qty","pending_qty"]:
        if col in di_summary.columns:
            di_summary[col] = di_summary[col].round(0).astype(int)

    show_cols = ["di_no","order_date","client_name","client_type","sale_type","products","Status",
                 "qty_ordered","dispatched_qty","pending_qty",
                 "total_ordered","dispatched_value","pending_value","challans"]
    show_cols = [c for c in show_cols if c in di_summary.columns]
    rename_map = {
        "di_no":"DI No.","order_date":"Date","client_name":"Client",
        "client_type":"Client Type","sale_type":"Sale Type",
        "products":"Products","qty_ordered":"Ord Qty","dispatched_qty":"Disp Qty",
        "pending_qty":"Pending Qty","total_ordered":"Order Val (₹)",
        "dispatched_value":"Disp Val (₹)","pending_value":"Pending Val (₹)",
        "challans":"Challans",
    }
    sum_cols = [c for c in ["qty_ordered","dispatched_qty","pending_qty",
                             "total_ordered","dispatched_value","pending_value"]
                if c in di_summary.columns] if role != "headoffice" else None
    col_cfg = {"order_date": st.column_config.DateColumn("Date", format="DD-MMM-YYYY")}

    table_by_sale_type(di_summary, key="ord_pipeline", sum_cols=sum_cols,
                       show_cols=show_cols, rename=rename_map, col_config=col_cfg,
                       date_col="order_date", show_export=(role != "headoffice"))

    # ── Per-DI detail ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown('<div class="section-header">Per-DI Detail</div>', unsafe_allow_html=True)

    all_dis = sorted(df_orders["di_no"].dropna().unique().tolist(), reverse=True)
    sel_detail_di = st.selectbox("Select DI to inspect", all_dis, key="detail_di_sel")

    di_rows  = df_orders[df_orders["di_no"] == sel_detail_di].copy()
    di_disps = disp_summary[disp_summary["di_no"] == sel_detail_di].copy() if not disp_summary.empty else pd.DataFrame()

    if not di_rows.empty:
        hdr = di_rows.iloc[0]
        st.markdown(
            f"**DI {sel_detail_di}** &nbsp;|&nbsp; {hdr.get('client_name','')} &nbsp;|&nbsp; "
            f"🏷️ {hdr.get('client_type','—')} &nbsp;|&nbsp; "
            f"📅 {pd.to_datetime(hdr['order_date']).strftime('%d-%b-%Y') if pd.notna(hdr['order_date']) else '—'} &nbsp;|&nbsp; "
            f"💳 {hdr.get('mode_of_payment','')}"
        )
        for _, prod_row in di_rows.iterrows():
            prod  = prod_row["product"]
            d_row = di_disps[di_disps["product"] == prod]
            d_qty = int(d_row["dispatched_qty"].sum()) if not d_row.empty else 0
            d_val = int(d_row["dispatched_value"].sum()) if not d_row.empty else 0
            challans_str = ", ".join(d_row["challans"].dropna().tolist()) if not d_row.empty else "—"
            o_qty = int(prod_row["qty_ordered"])
            pend  = o_qty - d_qty
            pct   = min(int(d_qty / o_qty * 100), 100) if o_qty > 0 else 0
            icon  = "🟢" if pend <= 0 else ("🟡" if d_qty > 0 else "🔴")

            st.markdown(
                f"**{icon} {prod}** &nbsp; Ordered `{o_qty:,}` | Dispatched `{d_qty:,}` | "
                f"Pending `{pend:,}` | {pct}% done | Value dispatched ₹`{d_val:,}` | Challans: `{challans_str}`"
            )
            st.progress(pct / 100)

        pdf_header2 = {
            "order_date":       pd.to_datetime(hdr["order_date"]).strftime('%d-%b-%Y') if pd.notna(hdr["order_date"]) else "—",
            "client_name":      hdr.get("client_name", ""),
            "contact_person":   hdr.get("contact_person", ""),
            "phone":            hdr.get("phone", ""),
            "office":           hdr.get("office", ""),
            "gstin":            hdr.get("gstin", ""),
            "client_type":      hdr.get("client_type", ""),
            "mode_of_payment":  hdr.get("mode_of_payment", ""),
            "sale_type":        hdr.get("sale_type", ""),
            "delivery_address": hdr.get("delivery_address", ""),
            "site_person":      hdr.get("site_person", ""),
            "site_phone":       hdr.get("site_phone", ""),
            "remarks":          hdr.get("remarks", ""),
        }
        pdf_lines2 = di_rows[["product", "qty_ordered", "rate", "total_amount"]].to_dict("records")
        dispatched_map = {
            row["product"]: {"qty": row["dispatched_qty"], "value": row["dispatched_value"]}
            for _, row in di_disps.iterrows()
        } if not di_disps.empty else None
        pdf_bytes2 = generate_dispatch_instruction(sel_detail_di, pdf_header2, pdf_lines2, dispatched_map)
        st.download_button(
            "🖨️ Download Dispatch Instruction PDF", data=pdf_bytes2,
            file_name=f"DI_{sel_detail_di}.pdf", mime="application/pdf",
            key=f"dl_di_{sel_detail_di}",
        )

    # ── Edit / Delete ─────────────────────────────────────────────────────────
    if role == "admin":
        st.markdown("---")
        with st.expander("✏️ Edit / Delete Order Line"):
            df_orders["label"] = (
                "DI " + df_orders["di_no"].astype(str) + " | " +
                df_orders["client_name"].fillna("").astype(str) + " | " +
                df_orders["product"].fillna("").astype(str) + " | " +
                df_orders["qty_ordered"].fillna(0).astype(int).astype(str) + " nos"
            )
            sel_ord = st.selectbox("Select order line", df_orders["label"].tolist(), key="ord_edit_sel")
            erow    = df_orders.loc[df_orders["label"] == sel_ord].iloc[0]

            with st.form("edit_ord_form"):
                ec1, ec2 = st.columns(2)
                e_di     = ec1.text_input("DI No.",   value=str(erow.get("di_no","") or ""))
                e_odate  = ec2.date_input("Order Date", value=pd.to_datetime(erow["order_date"]).date() if pd.notna(erow["order_date"]) else date.today())

                ec3, ec4, ec5 = st.columns(3)
                e_client = client_name_field(ec3, known_clients, "ord_edit_client",
                                             default=str(erow.get("client_name", "") or ""))
                e_contact = ec4.text_input("Contact Person Name", value=str(erow.get("contact_person","") or ""))
                e_phone   = ec5.text_input("Phone",               value=str(erow.get("phone","") or ""))

                ec6, ec7, ec8 = st.columns(3)
                _ect     = str(erow.get("client_type","") or "")
                e_ctype  = ec6.selectbox("Client Type", CLIENT_TYPES,
                                         index=CLIENT_TYPES.index(_ect) if _ect in CLIENT_TYPES else 0)
                e_office = ec7.text_input("Office", value=str(erow.get("office","") or ""))
                e_gstin  = ec8.text_input("GSTIN",  value=str(erow.get("gstin","") or ""))

                e_addr = st.text_input("Site Address", value=str(erow.get("delivery_address","") or ""))

                ec9, ec10 = st.columns(2)
                e_site_person = ec9.text_input("Site Person",    value=str(erow.get("site_person","") or ""))
                e_site_phone  = ec10.text_input("Site Phone No.", value=str(erow.get("site_phone","") or ""))

                epr1, epr2, epr3, epr4 = st.columns(4)
                e_prod   = epr1.selectbox("Product", ORDER_PRODUCTS,
                                         index=ORDER_PRODUCTS.index(erow["product"]) if erow.get("product") in ORDER_PRODUCTS else 0)
                e_qty    = epr2.number_input("Qty Ordered", value=float(erow.get("qty_ordered",0) or 0), min_value=0.0, step=100.0)
                e_rate   = epr3.number_input("Rate",        value=float(erow.get("rate",0) or 0), min_value=0.0, step=0.5)
                e_total  = epr4.number_input("Total Amt",   value=float(erow.get("total_amount",0) or 0), min_value=0.0, step=1000.0)
                epay1, epay2 = st.columns(2)
                e_pay    = epay1.selectbox("Payment Mode", PAYMENT_MODES,
                                        index=PAYMENT_MODES.index(erow["mode_of_payment"]) if erow.get("mode_of_payment") in PAYMENT_MODES else 0)
                _est     = str(erow.get("sale_type","") or "")
                e_stype  = epay2.selectbox("Sale Type", SALE_TYPES,
                                        index=SALE_TYPES.index(_est) if _est in SALE_TYPES else 0)
                e_rem    = st.text_input("Remarks", value=str(erow.get("remarks","") or ""))

                sc1, sc2 = st.columns(2)
                if sc1.form_submit_button("💾 Save Changes", type="primary", use_container_width=True):
                    update_order(int(erow["id"]), {
                        "di_no": e_di, "order_date": str(e_odate),
                        "client_name": e_client, "contact_person": e_contact, "phone": e_phone,
                        "office": e_office, "gstin": e_gstin, "client_type": e_ctype,
                        "delivery_address": e_addr, "site_person": e_site_person, "site_phone": e_site_phone,
                        "product": e_prod, "qty_ordered": e_qty, "rate": e_rate,
                        "total_amount": e_total if e_total > 0 else round(e_qty * e_rate, 2),
                        "mode_of_payment": e_pay, "sale_type": e_stype, "remarks": e_rem,
                    })
                    flash("✅ Order line updated!")
                    st.success("✅ Updated.")
                    st.rerun()
                if sc2.form_submit_button("🗑️ Delete this line", use_container_width=True):
                    delete_order(int(erow["id"]))
                    flash("🗑️ Order line deleted.")
                    st.success("✅ Deleted.")
                    st.rerun()
