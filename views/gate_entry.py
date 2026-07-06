import streamlit as st
import pandas as pd
from datetime import date
from core.config import GATE_CATEGORIES, GATE_DIRECTIONS, GATE_UNITS, GATE_RM_ITEMS
from core.db import insert_gate_entry, get_gate_entries, delete_gate_entry
from core.sequencing import is_duplicate
from core.ui import (interactive_table, flash, show_flashes, date_range_filter,
                     supplier_name_field, site_name_field, unit_field, item_name_field)


def show(PLOT):
    role = st.session_state.get("role", "dispatch")
    show_flashes()

    st.markdown("""
    <div class="page-title">🚧 Gate Entry</div>
    <div class="page-subtitle">Log raw material, equipment &amp; parts movement in / out of site</div>
    """, unsafe_allow_html=True)
    st.caption("Nothing here is required — fill in whatever's known. All raw materials, "
               "Plant Equipment & Misc Parts are tracked as running stock on the "
               "Inventory page.")

    df_known = get_gate_entries()
    known_suppliers = set(df_known["supplier_name"].dropna().astype(str)) \
        if not df_known.empty and "supplier_name" in df_known.columns else set()
    known_sites = set(df_known["site"].dropna().astype(str)) \
        if not df_known.empty and "site" in df_known.columns else set()
    known_units = set(GATE_UNITS) | (
        set(df_known["unit"].dropna().astype(str)) if not df_known.empty and "unit" in df_known.columns else set()
    )
    known_items = set(df_known.loc[df_known["category"] != "Raw Material", "item"].dropna().astype(str)) \
        if not df_known.empty and "item" in df_known.columns else set()

    # Category drives whether Item is a fixed dropdown or free text, so it's
    # rendered outside the form to react immediately to the operator's pick.
    category = st.selectbox("Category", GATE_CATEGORIES, key="gate_category")

    with st.form("gate_entry_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        entry_date = c1.date_input("Date", date.today())
        direction  = c2.selectbox("In / Out", GATE_DIRECTIONS)
        truck_no   = c3.text_input("Truck No.")

        if category == "Raw Material":
            item_pick  = st.selectbox("Item", GATE_RM_ITEMS)
            item_other = st.text_input("Item Name (only if \"Other\")") if item_pick == "Other" else ""
        else:
            item_pick  = item_name_field(st, known_items, "gate_item")
            item_other = ""

        c4, c5 = st.columns(2)
        challan_no = c4.text_input("Challan No.")
        invoice_no = c5.text_input("Invoice No.")

        c6, c7 = st.columns(2)
        qty  = c6.number_input("Quantity", min_value=0.0, step=1.0)
        unit = unit_field(c7, known_units, "gate_unit")

        c8, c9 = st.columns(2)
        supplier_name = supplier_name_field(c8, known_suppliers, "gate_supplier")
        site          = site_name_field(c9, known_sites, "gate_site")

        remarks = st.text_input("Remarks")

        submitted = st.form_submit_button("✅ Submit Entry", type="primary", use_container_width=True)

    if submitted:
        final_item = item_other.strip() if item_other.strip() else item_pick
        dup_no = challan_no.strip() or invoice_no.strip()
        is_dup = (
            (challan_no.strip() and is_duplicate(df_known, "challan_no", challan_no)) or
            (invoice_no.strip() and is_duplicate(df_known, "invoice_no", invoice_no))
        )
        if is_dup:
            st.error(f"An entry with Challan/Invoice No. \"{dup_no}\" already exists. "
                     f"Change it if this is a genuinely new entry.")
        else:
            insert_gate_entry({
                "date": str(entry_date), "category": category, "direction": direction,
                "item": final_item, "challan_no": challan_no, "invoice_no": invoice_no,
                "truck_no": truck_no, "qty": qty, "unit": unit,
                "supplier_name": supplier_name, "site": site, "remarks": remarks,
            })
            st.toast("✅ Gate entry saved!")

    # Only admins get to review the log — the operator's screen just clears.
    if role == "admin":
        st.markdown("---")
        st.markdown('<div class="section-header">Gate Entry Log</div>', unsafe_allow_html=True)
        df = get_gate_entries()
        if df.empty:
            st.info("No gate entries yet.")
        else:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            gate_start, gate_end = date_range_filter("gate", default_start=df["date"].min().date())
            df = df[(df["date"] >= pd.Timestamp(gate_start)) & (df["date"] <= pd.Timestamp(gate_end))]
            df = df.sort_values(["date", "id"], ascending=[False, False])
            if df.empty:
                st.info("No gate entries in this date range.")
                return
            show_cols = ["date", "category", "direction", "item", "challan_no", "invoice_no",
                         "truck_no", "qty", "unit", "supplier_name", "site", "remarks"]
            show_cols = [c for c in show_cols if c in df.columns]
            rename = {
                "date": "Date", "category": "Category", "direction": "In/Out", "item": "Item",
                "challan_no": "Challan", "invoice_no": "Invoice", "truck_no": "Truck",
                "qty": "Qty", "unit": "Unit", "supplier_name": "Supplier", "site": "Site",
                "remarks": "Remarks",
            }
            interactive_table(df, key="gate_log", show_cols=show_cols, rename=rename,
                              sum_cols=["qty"],
                              col_config={"date": st.column_config.DateColumn("Date", format="DD-MMM-YYYY")})

            df["label"] = (
                df["date"].dt.strftime("%d-%b-%Y") + " | " + df["category"].fillna("") + " | " +
                df["item"].fillna("").astype(str) + " | " + df["direction"].fillna("") +
                " | ID:" + df["id"].astype(str)
            )
            with st.expander("🗑️ Delete an entry"):
                sel = st.selectbox("Select entry", df["label"].tolist(), key="gate_del_sel")
                if st.button("Delete", key="gate_del_btn"):
                    rid = int(df.loc[df["label"] == sel, "id"].iloc[0])
                    delete_gate_entry(rid)
                    flash("🗑️ Gate entry deleted.")
                    st.rerun()
