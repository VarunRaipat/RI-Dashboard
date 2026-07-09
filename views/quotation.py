import streamlit as st
import pandas as pd
from datetime import date, timedelta
from core.config import (ORDER_PRODUCTS, CLIENT_TYPES, QUOTATION_UNITS, QUOTATION_STATUS,
                         QUOTATION_VALIDITY_DAYS, PAYMENT_MODES, SALE_TYPES, GST_PCT)
from core.db import (insert_quotation, get_quotations, update_quotation, delete_quotation,
                     get_orders, insert_order)
from core.calculations import gst_split
from core.pdf import generate_quotation, generate_dispatch_instruction
from core.ui import client_name_field, flash, show_flashes, interactive_table, date_range_filter
from core.sequencing import fy_start, next_sequence_number

LAKH = 100_000
REQUIRED_PLACEHOLDER = "— Select —"


def _fy_label(on_date=None):
    start = fy_start(on_date)
    return f"{start.year % 100:02d}-{(start.year + 1) % 100:02d}"


def _next_quote_no(df, on_date=None):
    """Next 'QTN/25-26/0001'-style number, resetting each financial year."""
    prefix = f"QTN/{_fy_label(on_date)}/"
    seq = 1
    if df is not None and not df.empty and "quote_no" in df.columns:
        subset = df
        if "quote_date" in df.columns:
            row_dates = pd.to_datetime(df["quote_date"], errors="coerce").dt.date
            subset = df[row_dates >= fy_start(on_date)]
        nums = []
        for v in subset["quote_no"].dropna().astype(str):
            if v.startswith(prefix) and v[len(prefix):].isdigit():
                nums.append(int(v[len(prefix):]))
        if nums:
            seq = max(nums) + 1
    return f"{prefix}{seq:04d}"


def _init_lines():
    if "quote_line_ids" not in st.session_state:
        st.session_state.quote_line_ids = [0]
    if "quote_next_line_id" not in st.session_state:
        st.session_state.quote_next_line_id = 1


def _auto_expire_quotes(df):
    """Flip Draft/Sent quotes past their Valid Until date to Expired.

    Runs once per page load; only the first load after a quote lapses does
    any writing, since subsequent loads no longer match the Draft/Sent filter.
    """
    if df.empty or "valid_until" not in df.columns or "status" not in df.columns:
        return df
    valid_dates = pd.to_datetime(df["valid_until"], errors="coerce").dt.date
    lapsed = df["status"].isin(["Draft", "Sent"]) & valid_dates.notna() & (valid_dates < date.today())
    if lapsed.any():
        for _, row in df[lapsed].iterrows():
            update_quotation(int(row["id"]), {"status": "Expired"})
        df = df.copy()
        df.loc[lapsed, "status"] = "Expired"
    return df


def show(PLOT):
    role = st.session_state.get("role", "admin")
    show_flashes()

    st.markdown("""
    <div class="page-title">🧾 Quotation</div>
    <div class="page-subtitle">Create client quotations · Auto-numbered · PDF</div>
    """, unsafe_allow_html=True)

    if st.session_state.get("last_quote_pdf"):
        st.markdown(
            f'<div class="success-box">📄 Quotation ready — '
            f'<b>{st.session_state["last_quote_no"]}</b></div>',
            unsafe_allow_html=True,
        )
        pcol1, pcol2, _ = st.columns([1.4, 1, 3])
        pcol1.download_button(
            "⬇️ Download Quotation PDF",
            data=st.session_state["last_quote_pdf"],
            file_name=f"Quotation_{st.session_state['last_quote_no'].replace('/', '_')}.pdf",
            mime="application/pdf", key="dl_last_quote", use_container_width=True,
        )
        if pcol2.button("✕ Dismiss", key="dismiss_last_quote_pdf", use_container_width=True):
            del st.session_state["last_quote_pdf"]
            del st.session_state["last_quote_no"]
            st.rerun()
        st.markdown("---")

    if st.session_state.get("last_converted_di_pdf"):
        st.markdown(
            f'<div class="success-box">🔄 Quotation converted — Sales Order DI '
            f'<b>{st.session_state["last_converted_di_no"]}</b> created</div>',
            unsafe_allow_html=True,
        )
        ccol1, ccol2, _ = st.columns([1.4, 1, 3])
        ccol1.download_button(
            "⬇️ Download Dispatch Instruction PDF",
            data=st.session_state["last_converted_di_pdf"],
            file_name=f"DI_{st.session_state['last_converted_di_no']}.pdf",
            mime="application/pdf", key="dl_last_converted_di", use_container_width=True,
        )
        if ccol2.button("✕ Dismiss", key="dismiss_last_converted_di", use_container_width=True):
            del st.session_state["last_converted_di_pdf"]
            del st.session_state["last_converted_di_no"]
            st.rerun()
        st.markdown("---")

    df_quotes = get_quotations()
    df_quotes = _auto_expire_quotes(df_quotes)
    df_orders = get_orders()

    known_clients = set()
    for src in (df_quotes, df_orders):
        if not src.empty and "client_name" in src.columns:
            known_clients |= set(src["client_name"].dropna().astype(str))

    # ── KPIs ───────────────────────────────────────────────────────────────────
    if not df_quotes.empty:
        total_quotes = df_quotes["quote_no"].nunique()
        total_value  = df_quotes["amount"].sum() + df_quotes.get("gst_amount", pd.Series(dtype=float)).sum()
        accepted_val = df_quotes.loc[df_quotes.get("status", "") == "Accepted", "amount"].sum()
        pending_val  = df_quotes.loc[df_quotes.get("status", "").isin(["Draft", "Sent"]), "amount"].sum()
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Quotations", f"{total_quotes}")
        k2.metric("Total Quoted Value", f"₹{total_value/LAKH:.2f}L")
        k3.metric("Accepted Value", f"₹{accepted_val/LAKH:.2f}L")
        k4.metric("Awaiting Response", f"₹{pending_val/LAKH:.2f}L")
        st.markdown("---")

    # ── New Quotation Entry ──────────────────────────────────────────────────
    st.markdown('<div class="section-header">New Quotation</div>', unsafe_allow_html=True)

    _init_lines()

    h1, h2, h3, h4 = st.columns(4)
    quote_date  = h1.date_input("Quote Date", value=date.today(), key="quo_date")
    valid_until = h2.date_input(
        "Valid Until", value=st.session_state.get("quo_date", date.today()) + timedelta(days=QUOTATION_VALIDITY_DAYS),
        key="quo_valid_until",
    )
    sales_person = h3.text_input("Sales Person Name", key="quo_sales_person")
    discount_pct = h4.number_input("Discount %", min_value=0.0, max_value=100.0, step=1.0, key="quo_discount")

    quote_no_display = _next_quote_no(df_quotes, quote_date)
    st.text_input("Quote No.", value=quote_no_display, key=f"quo_no_{quote_no_display}",
                 disabled=True, help="Auto-generated: next number for this financial year.")

    i1, i2, i3 = st.columns(3)
    client_name = client_name_field(i1, known_clients, "quo_client")

    repeat_row = None
    if client_name.strip() and not df_quotes.empty and "client_name" in df_quotes.columns:
        matches = df_quotes[
            df_quotes["client_name"].astype(str).str.strip().str.lower() == client_name.strip().lower()
        ]
        if not matches.empty:
            repeat_row = matches.sort_values("quote_date", ascending=False).iloc[0]

    def _cv(field):
        return str(repeat_row.get(field, "") or "") if repeat_row is not None else ""

    _ck = client_name.strip() or "new"
    if repeat_row is not None:
        i1.caption(f"↻ Repeat client — details prefilled from {repeat_row.get('quote_no','')}")
    contact_person = i2.text_input("Contact Person Name", value=_cv("contact_person"), key=f"quo_contact_{_ck}")
    phone          = i3.text_input("Phone",               value=_cv("phone"),          key=f"quo_phone_{_ck}")

    j1, j2, j3, j4 = st.columns(4)
    _ct_opts    = [REQUIRED_PLACEHOLDER] + CLIENT_TYPES
    _ct_default = _cv("client_type")
    client_type = j1.selectbox("Client Type *", _ct_opts,
                 index=_ct_opts.index(_ct_default) if _ct_default in CLIENT_TYPES else 0,
                 key=f"quo_ctype_{_ck}")
    office = j2.text_input("Address", value=_cv("office"), key=f"quo_office_{_ck}")
    gstin  = j3.text_input("GSTIN",   value=_cv("gstin"),  key=f"quo_gstin_{_ck}")
    _stype_default = _cv("sale_type")
    sale_type = j4.selectbox("Sale Type", SALE_TYPES,
                 index=SALE_TYPES.index(_stype_default) if _stype_default in SALE_TYPES else 0,
                 key=f"quo_saletype_{_ck}",
                 help="Sale B is off-book — GST is not applied on this quotation.")

    st.caption("*Client Name and Client Type are required.")

    gst_applicable = st.checkbox(
        f"Include GST (@{GST_PCT:.0f}%) — added on top of Rate",
        value=(sale_type != "Sale B"), key="quo_gst",
        disabled=(sale_type == "Sale B"),
    )
    remarks = st.text_input(
        "Additional Note (appears as an extra term on the PDF)", key="quo_remarks",
    )

    # ── Product lines ─────────────────────────────────────────────────────────
    st.markdown("**Product Lines**")
    st.caption("Add one line per product. GST (if enabled above) is added on top of Rate x Qty.")

    line_ids = st.session_state.quote_line_ids
    header_cols = st.columns([3, 1.5, 1.5, 2, 2, 1])
    header_cols[0].markdown("**Product**")
    header_cols[1].markdown("**Qty**")
    header_cols[2].markdown("**Unit**")
    header_cols[3].markdown("**Rate (₹)**")
    header_cols[4].markdown("**Amount (₹)**")

    quote_total = 0.0
    for n, lid in enumerate(line_ids):
        cols = st.columns([3, 1.5, 1.5, 2, 2, 1])
        cols[0].selectbox("Product", ORDER_PRODUCTS, key=f"quo_prod_{lid}", label_visibility="collapsed")
        cols[1].number_input("Qty", min_value=0.0, step=1.0, key=f"quo_qty_{lid}", label_visibility="collapsed")
        cols[2].selectbox("Unit", QUOTATION_UNITS, key=f"quo_unit_{lid}", label_visibility="collapsed")
        cols[3].number_input("Rate", min_value=0.0, step=0.5, key=f"quo_rate_{lid}", label_visibility="collapsed")
        qty_v  = st.session_state.get(f"quo_qty_{lid}", 0) or 0
        rate_v = st.session_state.get(f"quo_rate_{lid}", 0.0) or 0.0
        line_base = float(qty_v) * float(rate_v)
        line_gst, line_amt = gst_split(line_base, gst_applicable)
        quote_total += line_amt
        cols[4].markdown(
            f"<div style='padding:9px 0;font-weight:600;'>₹{line_amt:,.2f}</div>",
            unsafe_allow_html=True,
        )
        if len(line_ids) > 1:
            if cols[5].button("✕", key=f"quo_rem_{lid}"):
                st.session_state.quote_line_ids.remove(lid)
                st.rerun()

    if st.button("➕ Add Product", key="quo_add_line"):
        st.session_state.quote_line_ids.append(st.session_state.quote_next_line_id)
        st.session_state.quote_next_line_id += 1
        st.rerun()

    _discount_amt  = quote_total * discount_pct / 100
    _net_total     = quote_total - _discount_amt
    _total_lines = [f"Subtotal (incl. GST): ₹{quote_total:,.2f}"]
    if discount_pct:
        _total_lines.append(f"Discount ({discount_pct:g}%): -₹{_discount_amt:,.2f}")
    _total_lines.append(f"Grand Total: ₹{_net_total:,.2f}")
    st.markdown(
        "<div style='text-align:right;margin-top:6px'>"
        + "".join(f"<div>{t}</div>" for t in _total_lines[:-1])
        + f"<div style='font-size:1.1rem;font-weight:700;'>{_total_lines[-1]}</div>"
        + "</div>",
        unsafe_allow_html=True,
    )

    # ── Submit ────────────────────────────────────────────────────────────────
    st.markdown("")
    if st.button("✅ Generate Quotation", type="primary", use_container_width=True, key="quo_submit"):
        missing = []
        if not client_name.strip():             missing.append("Client Name")
        if client_type == REQUIRED_PLACEHOLDER:  missing.append("Client Type")

        if missing:
            st.error(f"Required: {', '.join(missing)}.")
        else:
            common_fields = {
                "quote_no":       quote_no_display,
                "quote_date":     str(quote_date),
                "valid_until":    str(valid_until),
                "client_name":    client_name,
                "contact_person": contact_person,
                "phone":          phone,
                "office":         office,
                "gstin":          gstin,
                "client_type":    client_type,
                "sales_person":   sales_person,
                "status":         "Sent",
                "discount_pct":   discount_pct,
                "sale_type":      sale_type,
                "remarks":        remarks,
            }
            saved = 0
            pdf_lines = []
            for lid in st.session_state.quote_line_ids:
                qty_v  = float(st.session_state.get(f"quo_qty_{lid}", 0) or 0)
                rate_v = float(st.session_state.get(f"quo_rate_{lid}", 0.0) or 0.0)
                if qty_v <= 0:
                    continue
                prod = st.session_state.get(f"quo_prod_{lid}", ORDER_PRODUCTS[0])
                unit = st.session_state.get(f"quo_unit_{lid}", QUOTATION_UNITS[0])
                base = round(qty_v * rate_v, 2)
                gst_amt, _ = gst_split(base, gst_applicable)
                insert_quotation({
                    **common_fields,
                    "product": prod, "qty": qty_v, "unit": unit, "rate": rate_v,
                    "amount": base, "gst_applicable": gst_applicable, "gst_amount": gst_amt,
                })
                pdf_lines.append({"product": prod, "qty": qty_v, "unit": unit, "rate": rate_v,
                                   "amount": base, "gst_amount": gst_amt})
                saved += 1

            if saved:
                pdf_header = {**common_fields}
                st.session_state["last_quote_pdf"] = generate_quotation(quote_no_display, pdf_header, pdf_lines)
                st.session_state["last_quote_no"]  = quote_no_display
                flash(f"✅ Quotation {quote_no_display} created")
                st.session_state.quote_line_ids = [st.session_state.quote_next_line_id]
                st.session_state.quote_next_line_id += 1
                for k in list(st.session_state.keys()):
                    if k.startswith(("quo_prod_", "quo_qty_", "quo_unit_", "quo_rate_")):
                        del st.session_state[k]
                st.rerun()
            else:
                st.error("Enter qty > 0 for at least one product line.")

    # ── Quotation History ────────────────────────────────────────────────────
    if df_quotes.empty:
        st.info("No quotations yet.")
        return

    st.markdown("---")
    st.markdown('<div class="section-header">Quotation History</div>', unsafe_allow_html=True)

    df_quotes = df_quotes.copy()
    df_quotes["quote_date"] = pd.to_datetime(df_quotes["quote_date"], errors="coerce")
    hist_start, hist_end = date_range_filter(
        "quo_hist", default_start=df_quotes["quote_date"].min().date()
    )
    df_hist = df_quotes[
        (df_quotes["quote_date"] >= pd.Timestamp(hist_start)) &
        (df_quotes["quote_date"] <= pd.Timestamp(hist_end))
    ]

    show_cols = ["quote_no", "quote_date", "valid_until", "client_name", "client_type", "sale_type",
                "product", "qty", "unit", "rate", "amount", "gst_amount", "status", "sales_person"]
    show_cols = [c for c in show_cols if c in df_hist.columns]
    rename = {
        "quote_no": "Quote No.", "quote_date": "Date", "valid_until": "Valid Until",
        "client_name": "Client", "client_type": "Client Type", "sale_type": "Sale Type", "product": "Product",
        "qty": "Qty", "unit": "Unit", "rate": "Rate (₹)", "amount": "Amount (₹)", "gst_amount": "GST (₹)",
        "status": "Status", "sales_person": "Sales Person",
    }
    col_cfg = {
        "quote_date": st.column_config.DateColumn("Date", format="DD-MMM-YYYY"),
    }
    interactive_table(df_hist, key="quo_history", sum_cols=["amount", "gst_amount"],
                      show_cols=show_cols, rename=rename, col_config=col_cfg)

    # ── Re-download a past quotation PDF ─────────────────────────────────────
    st.markdown("**Re-download a Quotation**")
    all_qnos = sorted(df_quotes["quote_no"].dropna().unique().tolist(), reverse=True)
    sel_qno = st.selectbox("Select Quote No.", all_qnos, key="quo_redownload_sel")
    q_rows = df_quotes[df_quotes["quote_no"] == sel_qno]
    if not q_rows.empty:
        qhdr = q_rows.iloc[0]
        redl_header = {
            "quote_date":     pd.to_datetime(qhdr["quote_date"]).strftime("%d-%b-%Y") if pd.notna(qhdr["quote_date"]) else "—",
            "valid_until":    qhdr.get("valid_until", "—"),
            "client_name":    qhdr.get("client_name", ""),
            "contact_person": qhdr.get("contact_person", ""),
            "phone":          qhdr.get("phone", ""),
            "office":         qhdr.get("office", ""),
            "gstin":          qhdr.get("gstin", ""),
            "client_type":    qhdr.get("client_type", ""),
            "sales_person":   qhdr.get("sales_person", ""),
            "discount_pct":   qhdr.get("discount_pct", 0),
            "sale_type":      qhdr.get("sale_type", "Sale A"),
            "remarks":        qhdr.get("remarks", ""),
        }
        redl_lines = q_rows[["product", "qty", "unit", "rate", "amount", "gst_amount"]].to_dict("records")
        redl_pdf = generate_quotation(sel_qno, redl_header, redl_lines)
        st.download_button(
            "🖨️ Download Quotation PDF", data=redl_pdf,
            file_name=f"Quotation_{sel_qno.replace('/', '_')}.pdf", mime="application/pdf",
            key=f"dl_quo_{sel_qno}",
        )

        if role == "admin":
            st.markdown("**Update Status**")
            sc1, sc2 = st.columns([2, 1])
            _cur_status = qhdr.get("status", "Sent")
            new_status = sc1.selectbox(
                "Status", QUOTATION_STATUS,
                index=QUOTATION_STATUS.index(_cur_status) if _cur_status in QUOTATION_STATUS else 0,
                key=f"quo_status_sel_{sel_qno}",
            )
            if sc2.button("💾 Update Status", key=f"quo_status_btn_{sel_qno}", use_container_width=True):
                for _, r in q_rows.iterrows():
                    update_quotation(int(r["id"]), {"status": new_status})
                flash(f"✅ {sel_qno} marked {new_status}")
                st.rerun()

        # ── Convert to Sales Order ────────────────────────────────────────────
        if role in ("admin", "headoffice"):
            st.markdown("**Convert to Sales Order**")
            if qhdr.get("status") == "Converted":
                st.info(f"Already converted → DI {qhdr.get('converted_di_no', '—')}. "
                       f"See Sales Orders to track dispatch.")
            else:
                cvc1, cvc2, cvc3 = st.columns([1.5, 1.5, 1])
                cv_payment = cvc1.selectbox("Payment Mode", PAYMENT_MODES, key=f"quo_cv_pay_{sel_qno}")
                _cv_st_default = qhdr.get("sale_type", "Sale A")
                cv_saletype = cvc2.selectbox(
                    "Sale Type", SALE_TYPES,
                    index=SALE_TYPES.index(_cv_st_default) if _cv_st_default in SALE_TYPES else 0,
                    key=f"quo_cv_sale_{sel_qno}",
                )
                if cvc3.button("🔄 Convert", key=f"quo_cv_btn_{sel_qno}", use_container_width=True):
                    df_orders_raw = get_orders()
                    new_di = str(next_sequence_number(df_orders_raw, "di_no", cv_saletype))
                    common_ord = {
                        "order_date":       str(date.today()),
                        "factory":          "Rameshwaram Industries",
                        "client_name":      qhdr.get("client_name", ""),
                        "contact_person":   qhdr.get("contact_person", ""),
                        "phone":            qhdr.get("phone", ""),
                        "office":           qhdr.get("office", ""),
                        "gstin":            qhdr.get("gstin", ""),
                        "client_type":      qhdr.get("client_type", ""),
                        "mode_of_payment":  cv_payment,
                        "sale_type":        cv_saletype,
                        "delivery_address": "",
                        "site_person":      "",
                        "site_phone":       "",
                        "remarks":          f"Converted from Quotation {sel_qno}",
                    }
                    pdf_lines_cv = []
                    for _, r in q_rows.iterrows():
                        gst_amt = float(r.get("gst_amount", 0) or 0)
                        insert_order({
                            **common_ord, "di_no": new_di,
                            "product": r["product"], "qty_ordered": r["qty"],
                            "rate": r["rate"], "total_amount": r["amount"],
                            "gst_applicable": gst_amt > 0, "gst_amount": gst_amt,
                        })
                        pdf_lines_cv.append({
                            "product": r["product"], "qty_ordered": r["qty"],
                            "rate": r["rate"], "total_amount": r["amount"], "gst_amount": gst_amt,
                        })
                    for _, r in q_rows.iterrows():
                        update_quotation(int(r["id"]), {"status": "Converted", "converted_di_no": new_di})
                    st.session_state["last_converted_di_pdf"] = generate_dispatch_instruction(
                        new_di, common_ord, pdf_lines_cv)
                    st.session_state["last_converted_di_no"] = new_di
                    flash(f"✅ {sel_qno} converted → DI {new_di}")
                    st.rerun()

    # ── Edit / Delete (admin only) ────────────────────────────────────────────
    if role == "admin":
        st.markdown("---")
        with st.expander("✏️ Edit / Delete Quotation Line"):
            df_quotes["label"] = (
                df_quotes["quote_no"].astype(str) + " | " +
                df_quotes["client_name"].fillna("").astype(str) + " | " +
                df_quotes["product"].fillna("").astype(str) + " | " +
                df_quotes["qty"].fillna(0).astype(int).astype(str) + " " +
                df_quotes["unit"].fillna("").astype(str)
            )
            sel_line = st.selectbox("Select quotation line", df_quotes["label"].tolist(), key="quo_edit_sel")
            erow = df_quotes.loc[df_quotes["label"] == sel_line].iloc[0]

            with st.form("edit_quo_form"):
                ec1, ec2, ec3 = st.columns(3)
                e_prod = ec1.selectbox("Product", ORDER_PRODUCTS,
                                       index=ORDER_PRODUCTS.index(erow["product"]) if erow.get("product") in ORDER_PRODUCTS else 0)
                e_qty  = ec2.number_input("Qty", value=float(erow.get("qty", 0) or 0), min_value=0.0, step=1.0)
                e_rate = ec3.number_input("Rate", value=float(erow.get("rate", 0) or 0), min_value=0.0, step=0.5)

                ec4, ec5 = st.columns(2)
                _eu = str(erow.get("unit", "") or "")
                e_unit = ec4.selectbox("Unit", QUOTATION_UNITS,
                                       index=QUOTATION_UNITS.index(_eu) if _eu in QUOTATION_UNITS else 0)
                _est = str(erow.get("status", "") or "")
                e_status = ec5.selectbox("Status", QUOTATION_STATUS,
                                         index=QUOTATION_STATUS.index(_est) if _est in QUOTATION_STATUS else 0)

                e_gst_applicable = bool(erow.get("gst_applicable", False))

                sc1, sc2 = st.columns(2)
                if sc1.form_submit_button("💾 Save Changes", type="primary", use_container_width=True):
                    e_base = round(e_qty * e_rate, 2)
                    e_gst_amt, _ = gst_split(e_base, e_gst_applicable)
                    update_quotation(int(erow["id"]), {
                        "product": e_prod, "qty": e_qty, "unit": e_unit, "rate": e_rate,
                        "amount": e_base, "gst_amount": e_gst_amt, "status": e_status,
                    })
                    flash("✅ Quotation line updated!")
                    st.rerun()
                if sc2.form_submit_button("🗑️ Delete this line", use_container_width=True):
                    delete_quotation(int(erow["id"]))
                    flash("🗑️ Quotation line deleted.")
                    st.rerun()
