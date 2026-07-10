import streamlit as st
import pandas as pd
from datetime import date
from core.config import GATE_CATEGORIES, GATE_DIRECTIONS, GATE_UNITS, GATE_RM_ITEMS
from core.db import insert_gate_entry, get_gate_entries, delete_gate_entry
from core.sequencing import is_duplicate
from core.ui import (interactive_table, flash, show_flashes, date_range_filter,
                     supplier_name_field, site_name_field, unit_field, item_name_field)

# Widget key templates for one item line — used to shift values down when a
# line is removed (Streamlit widgets keep state by key, so removing line i
# means copying line i+1's values into i, same pattern as Dispatch's
# multi-product lines).
_LINE_KEY_TEMPLATES = (
    "{p}_cat_{i}", "{p}_rm_item_{i}", "{p}_rm_other_{i}",
    "{p}_item_{i}_pick", "{p}_item_{i}_new",
    "{p}_qty_{i}",
    "{p}_unit_{i}_pick", "{p}_unit_{i}_new",
)


def _init_lines(key):
    if key not in st.session_state:
        st.session_state[key] = 1


def _shift_lines_up(prefix, removed_i, n_lines):
    for j in range(removed_i, n_lines - 1):
        for tmpl in _LINE_KEY_TEMPLATES:
            src, dst = tmpl.format(p=prefix, i=j + 1), tmpl.format(p=prefix, i=j)
            st.session_state[dst] = st.session_state.get(src)


def _reset_lines(prefix, n_lines):
    for i in range(n_lines):
        for tmpl in _LINE_KEY_TEMPLATES:
            st.session_state.pop(tmpl.format(p=prefix, i=i), None)
    st.session_state[f"{prefix}_lines"] = 1


def _item_lines(prefix, n_lines, known_items, known_units):
    """Renders `n_lines` Category/Item/Quantity/Unit blocks (plain widgets,
    not inside a form, so Add/Remove can rerun immediately). Returns a list
    of (category, item, qty, unit) tuples in render order."""
    lines = []
    for i in range(n_lines):
        with st.container(border=True):
            top = st.columns([3, 1])
            top[0].markdown(f"**Item {i + 1}**")
            if n_lines > 1:
                if top[1].button("✕ Remove", key=f"{prefix}_rem_{i}"):
                    _shift_lines_up(prefix, i, n_lines)
                    st.session_state[f"{prefix}_lines"] = n_lines - 1
                    st.rerun()

            cat = st.selectbox("Category", GATE_CATEGORIES, key=f"{prefix}_cat_{i}")
            if cat == "Raw Material":
                item_pick  = st.selectbox("Item", GATE_RM_ITEMS, key=f"{prefix}_rm_item_{i}")
                item_other = st.text_input("Item Name (only if \"Other\")", key=f"{prefix}_rm_other_{i}") \
                    if item_pick == "Other" else ""
                item = item_other.strip() if item_other.strip() else item_pick
            else:
                item = item_name_field(st, known_items, f"{prefix}_item_{i}")

            c1, c2 = st.columns(2)
            qty  = c1.number_input("Quantity", min_value=0.0, step=1.0, key=f"{prefix}_qty_{i}")
            unit = unit_field(c2, known_units, f"{prefix}_unit_{i}")

            lines.append((cat, item, qty, unit))

    if st.button("➕ Add Item", key=f"{prefix}_add_line"):
        st.session_state[f"{prefix}_lines"] += 1
        st.rerun()

    return lines


def show(PLOT):
    role = st.session_state.get("role", "dispatch")
    show_flashes()

    st.markdown("""
    <div class="page-title">🚧 Gate Entry</div>
    <div class="page-subtitle">Log raw material, equipment &amp; parts movement in / out of site</div>
    """, unsafe_allow_html=True)
    st.caption("Add one block per item — one truck/challan can carry more than one. Everything "
               "except Quantity is optional; a line only saves if its Quantity is > 0. All raw "
               "materials, Plant Equipment & Misc Parts are tracked as running stock on the "
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

    _init_lines("gate_lines")

    c1, c2, c3 = st.columns(3)
    entry_date = c1.date_input("Date", date.today(), key="gate_date")
    direction  = c2.selectbox("In / Out", GATE_DIRECTIONS, key="gate_direction")
    truck_no   = c3.text_input("Truck No.", key="gate_truck")

    c4, c5 = st.columns(2)
    challan_no = c4.text_input("Challan No.", key="gate_challan")
    invoice_no = c5.text_input("Invoice No.", key="gate_invoice")

    c8, c9 = st.columns(2)
    supplier_name = supplier_name_field(c8, known_suppliers, "gate_supplier")
    site          = site_name_field(c9, known_sites, "gate_site")

    remarks = st.text_input("Remarks", key="gate_remarks")

    st.markdown("**Items in this Entry**")
    lines = _item_lines("gate", st.session_state["gate_lines"], known_items, known_units)

    if st.button("✅ Submit Entry", type="primary", use_container_width=True, key="gate_submit"):
        valid_lines = [l for l in lines if l[2] > 0]
        dup_no = challan_no.strip() or invoice_no.strip()
        is_dup = (
            (challan_no.strip() and is_duplicate(df_known, "challan_no", challan_no)) or
            (invoice_no.strip() and is_duplicate(df_known, "invoice_no", invoice_no))
        )
        if is_dup:
            st.error(f"An entry with Challan/Invoice No. \"{dup_no}\" already exists. "
                     f"Change it if this is a genuinely new entry.")
        elif not valid_lines:
            st.error("Add at least one item with Quantity > 0.")
        else:
            n_lines = st.session_state["gate_lines"]
            for category, item, qty, unit in valid_lines:
                insert_gate_entry({
                    "date": str(entry_date), "category": category, "direction": direction,
                    "item": item, "challan_no": challan_no, "invoice_no": invoice_no,
                    "truck_no": truck_no, "qty": qty, "unit": unit,
                    "supplier_name": supplier_name, "site": site, "remarks": remarks,
                })
            st.toast(f"✅ Gate entry saved — {len(valid_lines)} item(s)!")
            _reset_lines("gate", n_lines)
            for k in ("gate_date", "gate_direction", "gate_truck", "gate_challan", "gate_invoice",
                      "gate_supplier_pick", "gate_supplier_new", "gate_site_pick", "gate_site_new",
                      "gate_remarks"):
                st.session_state.pop(k, None)
            st.rerun()

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
