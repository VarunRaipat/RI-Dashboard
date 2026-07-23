import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from core.tz import today_ist
from core.config import ORDER_PRODUCTS, PAYMENT_MODES, CLIENT_TYPES, SALE_TYPES, FACTORIES, GST_PCT, DI_NO_START, PRODUCT_TYPES, selling_price_unit
from core.db import insert_order, get_orders, get_order_by_di, update_order, delete_order, get_dispatch, create_edit_request, get_edit_requests
from core.calculations import gst_split, transport_charge
from core.pdf import generate_dispatch_instruction
from core.ui import client_name_field, flash, show_flashes, transport_fields
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
        di_no_display = str(next_sequence_number(df_orders_raw, "di_no", sale_type,
                                                  start=DI_NO_START.get(sale_type, 1)))
    else:
        di_no_display = ""

    # Keyed to the value itself (not a fixed key) so Streamlit always shows
    # the freshly computed number — a fixed key would "stick" to whatever
    # was in session_state from the first render and ignore later `value=`.
    di_no_input  = h1.text_input("DI No.", value=di_no_display, key=f"ord_di_no_{di_no_display}",
                                 disabled=(di_mode == "Add product to existing DI" and existing_dis),
                                 help="Pre-filled with the next number for the selected Sale Type — edit if your paper DI differs."
                                 if not (di_mode == "Add product to existing DI" and existing_dis)
                                 else "Adding to an existing DI — number is fixed.")
    order_date   = h2.date_input("Order Date", value=today_ist(), key="ord_date")

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

    j1, j2, j3, j4 = st.columns(4)
    _ct_opts    = [REQUIRED_PLACEHOLDER] + CLIENT_TYPES
    _ct_default = _cv("client_type")
    client_type = j1.selectbox("Client Type *", _ct_opts,
                 index=_ct_opts.index(_ct_default) if _ct_default in CLIENT_TYPES else 0,
                 key=f"ord_client_type_{_ck}")
    _pt_opts    = [REQUIRED_PLACEHOLDER] + PRODUCT_TYPES
    _pt_default = _hv("product_type")
    product_type = j2.selectbox("Product Type *", _pt_opts,
                 index=_pt_opts.index(_pt_default) if _pt_default in PRODUCT_TYPES else 0,
                 key="ord_product_type")
    office = j3.text_input("Office",  value=_cv("office"), key=f"ord_office_{_ck}")
    gstin  = j4.text_input("GSTIN",   value=_cv("gstin"),  key=f"ord_gstin_{_ck}")

    _gst_default = str(hdr_row.get("gst_applicable", False)).lower() in ("true", "1") if hdr_row is not None else False
    gst_applicable = st.checkbox(f"Include GST (@{GST_PCT:.0f}%) — added on top of Rate for every line below",
                                  value=_gst_default, key="ord_gst_applicable")

    st.caption("*Client Name, Client Type, Product Type, Payment Mode, and Sale Type are required.")

    delivery_addr = st.text_input("Site Address", value=_cv("delivery_address"), key=f"ord_addr_{_ck}")

    k1, k2 = st.columns(2)
    site_person = k1.text_input("Site Person",   value=_cv("site_person"), key=f"ord_site_person_{_ck}")
    site_phone  = k2.text_input("Site Phone No.", value=_cv("site_phone"),  key=f"ord_site_phone_{_ck}")

    remarks = st.text_input("Remarks", value=_hv("remarks"), key="ord_remarks")

    # ── Product lines ─────────────────────────────────────────────────────────
    st.markdown("**Product Lines**")
    if gst_applicable:
        st.caption(f"Add one line per product. Rate is GST-exclusive — GST @{GST_PCT:.0f}% is added to each line's total.")
    else:
        st.caption("Add one line per product. Rate is all-inclusive.")

    n_lines = st.session_state.order_lines
    header_cols = st.columns([3, 2, 2, 2, 1])
    header_cols[0].markdown("**Product**")
    header_cols[1].markdown("**Qty Ordered**")
    header_cols[2].markdown("**Rate (₹/nos)**")
    header_cols[3].markdown(f"**Total incl. GST (₹)**" if gst_applicable else "**Total (₹)**")

    for i in range(n_lines):
        cols = st.columns([3, 2, 2, 2, 1])
        # Joint Type is baked into the product name itself for Hume Pipes
        # (e.g. "Hume Pipe 300mm NP2 (Collar)") since Collar/M-F/Socket&Spigot
        # variants of the same diameter+class are separate physical stock,
        # even though they share one price (see SKU_TO_PRICING_KEY).
        cols[0].selectbox("Product", ORDER_PRODUCTS, key=f"ord_prod_{i}", label_visibility="collapsed")
        cols[1].number_input("Qty", min_value=0.0, step=100.0,   key=f"ord_qty_{i}",   label_visibility="collapsed")
        cols[2].number_input("Rate", min_value=0.0, step=0.5, key=f"ord_rate_{i}", label_visibility="collapsed")
        _row_unit = selling_price_unit(st.session_state.get(f"ord_prod_{i}", ""))
        if _row_unit != "nos":
            cols[2].caption(f"₹/{_row_unit} for this product")
        # Live total — plain markdown (not a keyed widget) so it always reflects
        # the current Qty/Rate instead of a stale value from a previous rerun.
        qty_v  = st.session_state.get(f"ord_qty_{i}", 0) or 0
        rate_v = st.session_state.get(f"ord_rate_{i}", 0.0) or 0.0
        _, _line_total = gst_split(float(qty_v) * float(rate_v), gst_applicable)
        cols[3].markdown(
            f"<div style='padding:9px 0;font-weight:600;'>₹{_line_total:,.2f}</div>",
            unsafe_allow_html=True,
        )
        if n_lines > 1:
            if cols[4].button("✕", key=f"ord_rem_{i}"):
                # remove this line by shifting keys
                for j in range(i, n_lines - 1):
                    st.session_state[f"ord_prod_{j}"]  = st.session_state.get(f"ord_prod_{j+1}",  ORDER_PRODUCTS[0])
                    st.session_state[f"ord_qty_{j}"]   = st.session_state.get(f"ord_qty_{j+1}",   0)
                    st.session_state[f"ord_rate_{j}"]  = st.session_state.get(f"ord_rate_{j+1}",  0.0)
                st.session_state.order_lines = n_lines - 1
                st.rerun()

    ca, cb = st.columns([1, 5])
    if ca.button("➕ Add Product", key="ord_add_line"):
        st.session_state.order_lines += 1
        st.rerun()

    transport_mode, transport_rate, transport_gst_applicable = transport_fields("ord")
    if di_mode == "Add product to existing DI" and existing_dis:
        st.caption("Adding to an existing DI: a Flat transport amount is only charged once, on the "
                   "DI's very first product line — new lines added here won't re-charge it.")

    # ── Submit ────────────────────────────────────────────────────────────────
    st.markdown("")
    if st.button("✅ Save Order", type="primary", use_container_width=True, key="ord_submit"):
        di_no_final  = di_no_input.strip()
        _payment_val = st.session_state.get("ord_payment", REQUIRED_PLACEHOLDER)
        _sale_val    = st.session_state.get("ord_sale_type", REQUIRED_PLACEHOLDER)
        _ctype_val   = client_type
        _ptype_val   = product_type

        missing = []
        if not di_no_final:            missing.append("DI No.")
        if not client_name.strip():    missing.append("Client Name")
        if _ctype_val == REQUIRED_PLACEHOLDER:   missing.append("Client Type")
        if _ptype_val == REQUIRED_PLACEHOLDER:   missing.append("Product Type")
        if _payment_val == REQUIRED_PLACEHOLDER: missing.append("Payment Mode")
        if _sale_val == REQUIRED_PLACEHOLDER:    missing.append("Sale Type")

        if missing:
            st.error(f"Required: {', '.join(missing)}.")
        elif di_mode == "New DI" and is_duplicate(df_orders_raw, "di_no", di_no_final):
            st.error(f"DI No. {di_no_final} already exists. Refresh the page and try again.")
        else:
            common_fields = {
                "order_date":       str(st.session_state.get("ord_date", today_ist())),
                "client_name":      client_name,
                "contact_person":   contact_person,
                "phone":            phone,
                "office":           office,
                "gstin":            gstin,
                "client_type":      _ctype_val,
                "product_type":     _ptype_val,
                "mode_of_payment":  _payment_val,
                "sale_type":        _sale_val,
                "delivery_address": delivery_addr,
                "site_person":      site_person,
                "site_phone":       site_phone,
                "remarks":          st.session_state.get("ord_remarks", ""),
                "gst_applicable":   gst_applicable,
            }
            saved = 0
            pdf_lines = []
            for i in range(st.session_state.order_lines):
                qty_v  = float(st.session_state.get(f"ord_qty_{i}", 0) or 0)
                rate_v = float(st.session_state.get(f"ord_rate_{i}", 0.0) or 0.0)
                if qty_v <= 0:
                    continue
                prod = st.session_state.get(f"ord_prod_{i}", ORDER_PRODUCTS[0])
                gst_amt, total_final = gst_split(qty_v * rate_v, gst_applicable)
                # Per-unit transport applies to every line; a Flat amount is
                # billed once per DI, so it only goes on the first line of a
                # brand-new DI — adding lines to an existing DI never
                # re-charges it (that DI's flat charge already went out with
                # its original first line).
                if transport_mode == "per_unit":
                    t_rate = transport_rate
                elif di_mode == "New DI" and saved == 0:
                    t_rate = transport_rate
                else:
                    t_rate = 0
                t_value, t_gst_amt = transport_charge(transport_mode, t_rate, qty_v, transport_gst_applicable)
                insert_order({
                    **common_fields,
                    "di_no":            di_no_final,
                    "factory":          FACTORIES[0],
                    "product":          prod,
                    "qty_ordered":      qty_v,
                    "rate":             rate_v,
                    "total_amount":     total_final,
                    "gst_amount":       gst_amt,
                    "transport_mode":   transport_mode, "transport_rate": t_rate,
                    "transport_value":  t_value, "transport_gst_applicable": transport_gst_applicable,
                    "transport_gst_amount": t_gst_amt,
                })
                pdf_lines.append({"product": prod, "qty_ordered": qty_v, "rate": rate_v,
                                   "total_amount": total_final, "gst_amount": gst_amt,
                                   "transport_value": t_value, "transport_gst_amount": t_gst_amt})
                saved += 1
            if saved:
                pdf_header = dict(common_fields)
                st.session_state["last_di_pdf"] = generate_dispatch_instruction(di_no_final, pdf_header, pdf_lines)
                st.session_state["last_di_no"]  = di_no_final
                flash(f"✅ Order saved — DI {di_no_final}")
                # Reset lines plus every header/client field so the next order
                # starts from a clean form instead of silently reusing this
                # one's client/GST/remarks — same reasoning as dispatch's
                # _reset_challan_fields. Date and Sale Type are left as-is so
                # entering several orders in a row doesn't require reselecting
                # them each time.
                st.session_state.order_lines = 1
                for k in list(st.session_state.keys()):
                    if k.startswith(("ord_prod_", "ord_qty_", "ord_rate_", "ord_total_",
                                      "ord_contact_person_", "ord_phone_", "ord_office_",
                                      "ord_client_type_", "ord_addr_", "ord_site_person_",
                                      "ord_site_phone_")):
                        del st.session_state[k]
                for k in ("ord_transport_mode", "ord_transport_rate", "ord_transport_gst",
                          "ord_payment", "ord_product_type", "ord_gst_applicable", "ord_remarks",
                          "ord_client_pick", "ord_client_new"):
                    st.session_state.pop(k, None)
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
    if "gst_amount" in df_orders.columns:
        _agg["gst_amount"] = ("gst_amount", "sum")
    if "transport_value" in df_orders.columns:
        _agg["transport_value"] = ("transport_value", "sum")
    if "transport_gst_amount" in df_orders.columns:
        _agg["transport_gst_amount"] = ("transport_gst_amount", "sum")
    if "client_type" in df_orders.columns:
        _agg["client_type"] = ("client_type", "first")
    if "product_type" in df_orders.columns:
        _agg["product_type"] = ("product_type", "first")
    if "sale_type" in df_orders.columns:
        _agg["sale_type"] = ("sale_type", "first")
    if "created_at" in df_orders.columns:
        _agg["created_at"] = ("created_at", "first")
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

    from core.ui import table_by_sale_type, add_ist_timestamp, timestamp_col_config

    di_summary = add_ist_timestamp(di_summary)

    for col in ["total_ordered","dispatched_value","pending_value","qty_ordered","dispatched_qty",
                "pending_qty","gst_amount","transport_value","transport_gst_amount"]:
        if col in di_summary.columns:
            di_summary[col] = di_summary[col].round(0).astype(int)

    show_cols = ["di_no","order_date","client_name","client_type","product_type","sale_type","products","Status",
                 "qty_ordered","dispatched_qty","pending_qty",
                 "total_ordered","gst_amount","transport_value","transport_gst_amount",
                 "dispatched_value","pending_value","challans","created_at"]
    show_cols = [c for c in show_cols if c in di_summary.columns]
    rename_map = {
        "di_no":"DI No.","order_date":"Date","client_name":"Client",
        "client_type":"Client Type","product_type":"Product Type","sale_type":"Sale Type",
        "products":"Products","qty_ordered":"Ord Qty","dispatched_qty":"Disp Qty",
        "pending_qty":"Pending Qty","total_ordered":"Material Val (₹)","gst_amount":"Material GST (₹)",
        "transport_value":"Transport (₹)","transport_gst_amount":"Transport GST (₹)",
        "dispatched_value":"Disp Val (₹)","pending_value":"Pending Val (₹)",
        "challans":"Challans","created_at":"Entered At",
    }
    sum_cols = [c for c in ["qty_ordered","dispatched_qty","pending_qty",
                             "total_ordered","gst_amount","transport_value","transport_gst_amount",
                             "dispatched_value","pending_value"]
                if c in di_summary.columns] if role != "headoffice" else None
    col_cfg = {"order_date": st.column_config.DateColumn("Date", format="DD-MMM-YYYY"),
               "created_at": timestamp_col_config()}

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
            f"📦 {hdr.get('product_type','—')} &nbsp;|&nbsp; "
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
            "product_type":     hdr.get("product_type", ""),
            "mode_of_payment":  hdr.get("mode_of_payment", ""),
            "sale_type":        hdr.get("sale_type", ""),
            "delivery_address": hdr.get("delivery_address", ""),
            "site_person":      hdr.get("site_person", ""),
            "site_phone":       hdr.get("site_phone", ""),
            "remarks":          hdr.get("remarks", ""),
        }
        _pdf_cols2 = ["product", "qty_ordered", "rate", "total_amount"] + (["gst_amount"] if "gst_amount" in di_rows.columns else [])
        pdf_lines2 = di_rows[_pdf_cols2].to_dict("records")
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
                e_odate  = ec2.date_input("Order Date", value=pd.to_datetime(erow["order_date"]).date() if pd.notna(erow["order_date"]) else today_ist())

                ec3, ec4, ec5 = st.columns(3)
                e_client = client_name_field(ec3, known_clients, "ord_edit_client",
                                             default=str(erow.get("client_name", "") or ""))
                e_contact = ec4.text_input("Contact Person Name", value=str(erow.get("contact_person","") or ""))
                e_phone   = ec5.text_input("Phone",               value=str(erow.get("phone","") or ""))

                ec6, ec7, ec8, ec8b = st.columns(4)
                _ect     = str(erow.get("client_type","") or "")
                e_ctype  = ec6.selectbox("Client Type", CLIENT_TYPES,
                                         index=CLIENT_TYPES.index(_ect) if _ect in CLIENT_TYPES else 0)
                _ept     = str(erow.get("product_type","") or "")
                e_ptype  = ec7.selectbox("Product Type", PRODUCT_TYPES,
                                         index=PRODUCT_TYPES.index(_ept) if _ept in PRODUCT_TYPES else 0)
                e_office = ec8.text_input("Office", value=str(erow.get("office","") or ""))
                e_gstin  = ec8b.text_input("GSTIN",  value=str(erow.get("gstin","") or ""))

                e_addr = st.text_input("Site Address", value=str(erow.get("delivery_address","") or ""))

                ec9, ec10 = st.columns(2)
                e_site_person = ec9.text_input("Site Person",    value=str(erow.get("site_person","") or ""))
                e_site_phone  = ec10.text_input("Site Phone No.", value=str(erow.get("site_phone","") or ""))

                epr1, epr2, epr3, epr4 = st.columns(4)
                e_prod   = epr1.selectbox("Product", ORDER_PRODUCTS,
                                         index=ORDER_PRODUCTS.index(erow["product"]) if erow.get("product") in ORDER_PRODUCTS else 0)
                e_qty    = epr2.number_input("Qty Ordered", value=float(erow.get("qty_ordered",0) or 0), min_value=0.0, step=100.0)
                e_rate   = epr3.number_input("Rate",        value=float(erow.get("rate",0) or 0), min_value=0.0, step=0.5)
                e_total  = epr4.number_input("Total Amt (incl. GST if any)", value=float(erow.get("total_amount",0) or 0), min_value=0.0, step=1000.0)

                egst1, egst2 = st.columns(2)
                _e_gst_default = str(erow.get("gst_applicable", False)).lower() in ("true", "1")
                e_gst_applicable = egst1.checkbox(f"Include GST (@{GST_PCT:.0f}%)", value=_e_gst_default)
                e_gst_amount     = egst2.number_input("GST Amount (₹)", value=float(erow.get("gst_amount", 0) or 0), min_value=0.0, step=100.0)

                st.markdown("**Transport**")
                _e_tmode_default = str(erow.get("transport_mode", "per_unit") or "per_unit")
                etm1, etm2, etm3, etm4 = st.columns(4)
                e_tmode_label = etm1.radio("Mode", ["Per Unit (₹/nos.)", "Flat (₹ total)"],
                                          index=1 if _e_tmode_default == "flat" else 0)
                e_tmode = "per_unit" if e_tmode_label.startswith("Per Unit") else "flat"
                e_trate = etm2.number_input("Transport Rate (₹)", value=float(erow.get("transport_rate", 0) or 0), min_value=0.0, step=0.5)
                e_tvalue = etm3.number_input("Transport Value (₹)", value=float(erow.get("transport_value", 0) or 0), min_value=0.0, step=100.0)
                _e_tgst_default = str(erow.get("transport_gst_applicable", False)).lower() in ("true", "1")
                e_tgst_applicable = etm4.checkbox("GST on Transport", value=_e_tgst_default)
                e_tgst_amount = st.number_input("Transport GST Amount (₹)", value=float(erow.get("transport_gst_amount", 0) or 0), min_value=0.0, step=50.0)

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
                        "product_type": e_ptype,
                        "delivery_address": e_addr, "site_person": e_site_person, "site_phone": e_site_phone,
                        "product": e_prod, "qty_ordered": e_qty, "rate": e_rate,
                        "total_amount": e_total if e_total > 0 else round(e_qty * e_rate, 2),
                        "gst_applicable": e_gst_applicable, "gst_amount": e_gst_amount,
                        "transport_mode": e_tmode, "transport_rate": e_trate,
                        "transport_value": e_tvalue, "transport_gst_applicable": e_tgst_applicable,
                        "transport_gst_amount": e_tgst_amount,
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

    elif role == "headoffice":
        st.markdown("---")
        with st.expander("🔧 Spotted a mistake? Request an edit"):
            st.caption("Pick the order line, enter the corrected values, and submit — an admin reviews "
                       "and approves before it changes the live record.")
            if df_orders.empty:
                st.info("No order lines to request an edit for.")
            else:
                df_req = df_orders.copy()
                df_req["label"] = (
                    "DI " + df_req["di_no"].astype(str) + " | " +
                    df_req["client_name"].fillna("").astype(str) + " | " +
                    df_req["product"].fillna("").astype(str) + " | " +
                    df_req["qty_ordered"].fillna(0).astype(int).astype(str) + " nos | ID:" + df_req["id"].astype(str)
                )
                sel_req = st.selectbox("Select order line", df_req["label"].tolist(), key="ord_req_sel")
                rrow    = df_req.loc[df_req["label"] == sel_req].iloc[0]
                rrow_id = int(rrow["id"])

                with st.form(f"ord_req_form_{rrow_id}"):
                    qc1, qc2 = st.columns(2)
                    r_di    = qc1.text_input("DI No.", value=str(rrow.get("di_no", "") or ""))
                    r_odate = qc2.date_input("Order Date",
                                             value=pd.to_datetime(rrow["order_date"]).date()
                                             if pd.notna(rrow["order_date"]) else today_ist())

                    qc3, qc4 = st.columns(2)
                    r_client = client_name_field(qc3, known_clients, "ord_req_client",
                                                 default=str(rrow.get("client_name", "") or ""))
                    r_addr   = qc4.text_input("Site Address", value=str(rrow.get("delivery_address", "") or ""))

                    qc5, qc6, qc7 = st.columns(3)
                    r_prod = qc5.selectbox("Product", ORDER_PRODUCTS,
                                           index=ORDER_PRODUCTS.index(rrow["product"]) if rrow.get("product") in ORDER_PRODUCTS else 0)
                    r_qty  = qc6.number_input("Qty Ordered", value=float(rrow.get("qty_ordered", 0) or 0), min_value=0.0, step=100.0)
                    r_rate = qc7.number_input("Rate", value=float(rrow.get("rate", 0) or 0), min_value=0.0, step=0.5)

                    r_rem  = st.text_input("Remarks", value=str(rrow.get("remarks", "") or ""))

                    submit_req = st.form_submit_button("📨 Submit Edit Request", type="primary", use_container_width=True)

                if submit_req:
                    new_data = {
                        "di_no": r_di, "order_date": str(r_odate),
                        "client_name": r_client, "delivery_address": r_addr,
                        "product": r_prod, "qty_ordered": r_qty, "rate": r_rate,
                        "total_amount": round(r_qty * r_rate, 2), "remarks": r_rem,
                    }
                    old_data = {k: rrow.get(k) for k in new_data}
                    create_edit_request(
                        "orders", "Sales Orders", rrow_id,
                        f"DI {rrow.get('di_no','')} · {rrow.get('product','')} · {rrow.get('client_name','')}",
                        old_data, new_data,
                    )
                    flash("📨 Edit request submitted — pending admin approval.")
                    st.success("✅ Request submitted. An admin will review it.")
                    st.rerun()

        my_reqs = get_edit_requests()
        if not my_reqs.empty:
            mine = my_reqs[(my_reqs["table_name"] == "orders") &
                          (my_reqs["requested_by"] == st.session_state.get("username"))]
            if not mine.empty:
                st.markdown('<div class="section-header">My Edit Requests</div>', unsafe_allow_html=True)
                mine_disp = mine[["created_at", "summary", "status", "review_note"]].rename(columns={
                    "created_at": "Submitted", "summary": "Order Line", "status": "Status", "review_note": "Admin Note",
                })
                st.dataframe(mine_disp, use_container_width=True, hide_index=True)
