"""
Reusable interactive table component: column filters + totals row + Excel export.
"""
import io
from datetime import date, timedelta
import streamlit as st
import pandas as pd

_CURRENCY_KW = {"cost", "value", "revenue", "profit", "amount", "pay"}


def date_range_filter(key_prefix, default_start=None, default_end=None):
    """From/To date inputs, keyed per page (key_prefix) so each module's
    filter is independent — picking a range on one page never affects
    another page's filter."""
    c1, c2 = st.columns(2)
    start = c1.date_input("From", value=default_start or date.today().replace(day=1),
                          key=f"{key_prefix}_f_start")
    end   = c2.date_input("To",   value=default_end or date.today(),
                          key=f"{key_prefix}_f_end")
    return start, end


def quick_date_range_filter(key_prefix, default_start=None, default_end=None):
    """Today / Yesterday / This Week / This Month / This Year quick buttons
    plus From/To date inputs, keyed per page (key_prefix) so each module's
    filter is independent."""
    today = date.today()
    start_key, end_key = f"{key_prefix}_qf_start", f"{key_prefix}_qf_end"

    if start_key not in st.session_state:
        st.session_state[start_key] = default_start or today.replace(day=1)
    if end_key not in st.session_state:
        st.session_state[end_key] = default_end or today

    qb1, qb2, qb3, qb4, qb5, _ = st.columns([1, 1, 1, 1, 1, 3])
    if qb1.button("Today", key=f"{key_prefix}_qf_today", use_container_width=True):
        st.session_state[start_key] = today
        st.session_state[end_key]   = today
    if qb2.button("Yesterday", key=f"{key_prefix}_qf_yesterday", use_container_width=True):
        yesterday = today - timedelta(days=1)
        st.session_state[start_key] = yesterday
        st.session_state[end_key]   = yesterday
    if qb3.button("This Week", key=f"{key_prefix}_qf_week", use_container_width=True):
        st.session_state[start_key] = today - timedelta(days=today.weekday())
        st.session_state[end_key]   = today
    if qb4.button("This Month", key=f"{key_prefix}_qf_month", use_container_width=True):
        st.session_state[start_key] = today.replace(day=1)
        st.session_state[end_key]   = today
    if qb5.button("This Year", key=f"{key_prefix}_qf_year", use_container_width=True):
        st.session_state[start_key] = today.replace(month=1, day=1)
        st.session_state[end_key]   = today

    c1, c2 = st.columns(2)
    start = c1.date_input("From", key=start_key)
    end   = c2.date_input("To",   key=end_key)
    return start, end


def flash(message):
    """Queue a toast to show after the next rerun.

    st.toast() doesn't survive a st.rerun() called in the same run — the
    rerun cuts the script off before the toast delta reaches the frontend.
    Call this instead of st.toast() right before st.rerun(); show_flashes()
    (called once near the top of each page) displays it on the next run.
    """
    st.session_state.setdefault("_pending_toasts", []).append(message)


def show_flashes():
    """Show and clear any toasts queued via flash() on a prior run."""
    for message in st.session_state.pop("_pending_toasts", []):
        st.toast(message)


def _fmt(col, val):
    lower = col.lower()
    if any(k in lower for k in _CURRENCY_KW):
        return f"₹{val/100_000:.2f}L" if abs(val) >= 100_000 else f"₹{val:,.0f}"
    if isinstance(val, float) and val % 1 != 0:
        return f"{val:,.2f}"
    return f"{int(val):,}"


def _to_excel_bytes(df, sheet_name="Data"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31] or "Data")
    return buf.getvalue()


def _name_picker_field(container, label, noun, known_values, key, default=""):
    """
    Dropdown of previously-used values (so people pick "Frontage" instead of
    retyping a variant like "Frontage Construction", which otherwise
    fragments name-wise analytics) plus a free-text fallback for new values.

    Both widgets are always rendered (never conditionally hidden) because this
    is used inside st.form blocks, where widget visibility can't react to
    other widgets until the form is submitted — only their submitted values
    can be combined, which is what this function does.
    """
    placeholder = f"— Select existing {noun} —"
    options = sorted({str(v).strip() for v in known_values if str(v).strip() and v != default})
    options = ([default] if default else [placeholder]) + [o for o in options if o != default]

    picked  = container.selectbox(label, options, key=f"{key}_pick")
    new_val = container.text_input(f"New {label} (only if not in list above)", key=f"{key}_new")
    new_val = new_val.strip()
    if new_val:
        return new_val
    return picked if picked != placeholder else ""


def client_name_field(container, known_clients, key, default=""):
    return _name_picker_field(container, "Client Name", "client", known_clients, key, default)


def supplier_name_field(container, known_suppliers, key, default=""):
    return _name_picker_field(container, "Supplier Name", "supplier", known_suppliers, key, default)


def site_name_field(container, known_sites, key, default=""):
    return _name_picker_field(container, "Site", "site", known_sites, key, default)


def unit_field(container, known_units, key, default=""):
    return _name_picker_field(container, "Unit", "unit", known_units, key, default)


def item_name_field(container, known_items, key, default=""):
    return _name_picker_field(container, "Item / Part Description", "item", known_items, key, default)


def interactive_table(df, key, sum_cols=None, show_cols=None,
                      rename=None, col_config=None, date_col="date", show_export=True):
    """
    Render a filterable dataframe with auto totals row.

    Args:
        df        : Source DataFrame (caller handles date range filtering).
        key       : Unique widget key prefix.
        sum_cols  : Original column names whose sums to show below the table.
        show_cols : Ordered list of original column names to display.
        rename    : {original: display_label} dict.
        col_config: st.column_config dict (use display names from rename).
        date_col  : Unused (kept for backward-compatible call sites).
        show_export: Set False to hide the Export to Excel button (e.g. for
                     roles that shouldn't be able to pull data out of the app).
    """
    if df.empty:
        st.info("No records found.")
        return df

    # ── Filterable columns — every displayed column, type-to-search ────────────
    skip = {"id", "created_at", "label"}
    filter_cols = [c for c in (show_cols or df.columns) if c not in skip and c in df.columns]

    # ── Filter widgets ────────────────────────────────────────────────────────
    filtered = df.copy()
    if filter_cols:
        rn = rename or {}
        with st.expander("🔍 Filter by column (type to search)", expanded=False):
            per_row = 4
            for chunk in [filter_cols[i:i+per_row] for i in range(0, len(filter_cols), per_row)]:
                cols = st.columns(len(chunk))
                for i, col in enumerate(chunk):
                    label = rn.get(col, col.replace("_", " ").title())
                    query = cols[i].text_input(label, key=f"{key}_flt_{col}", placeholder="Type to filter…")
                    query = query.strip()
                    if query:
                        filtered = filtered[
                            filtered[col].astype(str).str.contains(query, case=False, na=False, regex=False)
                        ]

    n_shown = len(filtered)
    n_total = len(df)
    st.caption(f"**{n_shown:,}** of **{n_total:,}** records")

    # ── Display ───────────────────────────────────────────────────────────────
    disp = filtered[show_cols].copy() if show_cols else filtered.copy()
    if rename:
        disp = disp.rename(columns=rename)
    st.dataframe(disp, use_container_width=True, hide_index=True,
                 column_config=col_config or {})

    # ── Totals bar ────────────────────────────────────────────────────────────
    if sum_cols and n_shown > 0:
        avail  = [c for c in sum_cols if c in filtered.columns]
        if avail:
            rn     = rename or {}
            totals = filtered[avail].sum()
            parts  = [
                f"<b>{rn.get(c, c.replace('_',' ').title())}:</b> {_fmt(c, totals[c])}"
                for c in avail
            ]
            st.markdown(
                "<div style='background:rgba(139,36,40,0.07);"
                "border:1px solid rgba(139,36,40,0.18);"
                "border-left:4px solid #8B2428;border-radius:8px;"
                "padding:9px 16px;font-size:0.82rem;color:#C4AEAE;margin-top:4px'>"
                "Σ &nbsp; " + " &nbsp;·&nbsp; ".join(parts) +
                "</div>",
                unsafe_allow_html=True,
            )

    # ── Excel export (respects active filters) ─────────────────────────────────
    if show_export:
        st.download_button(
            "📥 Export to Excel",
            data=_to_excel_bytes(disp, sheet_name=key),
            file_name=f"{key}_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key}_xlsx_export",
        )

    return filtered


def table_by_sale_type(df, key, sum_cols=None, show_cols=None,
                       rename=None, col_config=None, date_col="date", show_export=True):
    """
    Same as interactive_table, but rendered as two separate tables — one
    per Sale Type — so Sale A and Sale B rows (and their totals) are never
    pooled together. Falls back to a single table if there's no sale_type
    column to split on.
    """
    if df is None or "sale_type" not in df.columns:
        return interactive_table(df, key=key, sum_cols=sum_cols, show_cols=show_cols,
                                 rename=rename, col_config=col_config, date_col=date_col,
                                 show_export=show_export)

    for sale_type in ("Sale A", "Sale B"):
        st.markdown(f"**{sale_type}**")
        interactive_table(df[df["sale_type"] == sale_type], key=f"{key}_{sale_type[-1].lower()}",
                          sum_cols=sum_cols, show_cols=show_cols, rename=rename,
                          col_config=col_config, date_col=date_col, show_export=show_export)
