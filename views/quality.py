import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date
from core.config import PRODUCTION_PRODUCTS
from core.db import insert_quality, get_quality, update_quality, delete_row


def show(PLOT):
    role = st.session_state.get("role", "viewer")

    st.markdown("""
    <div class="page-title">🧪 Quality Control</div>
    <div class="page-subtitle">Test results (3-sample average) · track product performance over time — TODO: confirm actual QC test/unit for Hume Pipes (e.g. hydrostatic pressure, three-edge bearing load) and relabel</div>
    """, unsafe_allow_html=True)

    # ── Entry Form ────────────────────────────────────────────────────────────
    if role in ("admin", "quality", "dispatch"):
        with st.form("qc_form", clear_on_submit=True):
            st.markdown('<div class="section-header">Test Details</div>', unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            test_date    = c1.date_input("Date of Test", date.today())
            casting_date = c2.date_input("Date of Casting", date.today())
            product      = c3.selectbox("Product", PRODUCTION_PRODUCTS)

            st.markdown('<div class="section-header">Test Result (confirm unit)</div>', unsafe_allow_html=True)
            s1, s2, s3 = st.columns(3)
            sample_1 = s1.number_input("Sample 1", min_value=0.0, step=0.1)
            sample_2 = s2.number_input("Sample 2", min_value=0.0, step=0.1)
            sample_3 = s3.number_input("Sample 3", min_value=0.0, step=0.1)

            remarks = st.text_input("Remarks (optional)")
            submitted = st.form_submit_button("✅ Save Test Result", type="primary", use_container_width=True)

        if submitted:
            if test_date < casting_date:
                st.error("Test date cannot be before casting date.")
            elif sample_1 == 0 and sample_2 == 0 and sample_3 == 0:
                st.error("Enter at least one sample strength value.")
            else:
                nonzero = [s for s in [sample_1, sample_2, sample_3] if s > 0]
                avg = round(sum(nonzero) / len(nonzero), 2)
                curing_days = (test_date - casting_date).days
                record = {
                    "test_date":    str(test_date),
                    "casting_date": str(casting_date),
                    "product":      product,
                    "sample_1":     sample_1,
                    "sample_2":     sample_2,
                    "sample_3":     sample_3,
                    "average":      avg,
                    "remarks":      remarks.strip(),
                }
                insert_quality(record)
                st.markdown(
                    f'<div class="success-box">✅ Test saved — Average: <b>{avg:.2f}</b>'
                    f' · Curing age: <b>{curing_days} days</b></div>',
                    unsafe_allow_html=True,
                )

    if role in ("quality", "dispatch"):
        return  # form only

    # ── Load data ─────────────────────────────────────────────────────────────
    df = get_quality()
    if df.empty:
        st.info("No quality test records yet. Submit the first test above.")
        return

    df["test_date"]    = pd.to_datetime(df["test_date"],    errors="coerce")
    df["casting_date"] = pd.to_datetime(df["casting_date"], errors="coerce")
    df["curing_days"]  = (df["test_date"] - df["casting_date"]).dt.days
    df = df.sort_values(["test_date", "id"], ascending=[False, False]).reset_index(drop=True)

    from core.ui import date_range_filter
    qc_start, qc_end = date_range_filter("qc", default_start=df["test_date"].min().date())
    df = df[(df["test_date"] >= pd.Timestamp(qc_start)) & (df["test_date"] <= pd.Timestamp(qc_end))]
    if df.empty:
        st.info("No quality test records in this date range.")
        return

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab1, tab2 = st.tabs(["📋  Records", "📈  Performance"])

    with tab1:
        from core.ui import interactive_table
        st.markdown('<div class="section-header">All Test Records</div>', unsafe_allow_html=True)
        show_cols = ["test_date", "casting_date", "curing_days", "product",
                     "sample_1", "sample_2", "sample_3", "average", "remarks"]
        show_cols = [c for c in show_cols if c in df.columns]
        rename = {
            "test_date":    "Test Date",
            "casting_date": "Casting Date",
            "curing_days":  "Curing (days)",
            "product":      "Product",
            "sample_1":     "S1",
            "sample_2":     "S2",
            "sample_3":     "S3",
            "average":      "Avg",
            "remarks":      "Remarks",
        }
        col_cfg = {
            "test_date":    st.column_config.DateColumn("Test Date",    format="DD-MMM-YYYY"),
            "casting_date": st.column_config.DateColumn("Casting Date", format="DD-MMM-YYYY"),
        }
        interactive_table(df, key="qc_rec", sum_cols=["average"],
                          show_cols=show_cols, rename=rename, col_config=col_cfg,
                          date_col="test_date")

    with tab2:
        st.markdown('<div class="section-header">Strength Trend by Product</div>', unsafe_allow_html=True)
        COLORS = ["#00C49A", "#3B82F6", "#FDBA44", "#A78BFA", "#FB7185",
                  "#34D399", "#F97316", "#22D3EE"]
        products = df["product"].unique().tolist()

        fig = go.Figure()
        for i, prod in enumerate(products):
            pdata = df[df["product"] == prod].sort_values("test_date")
            fig.add_trace(go.Scatter(
                x=pdata["test_date"],
                y=pdata["average"],
                mode="lines+markers",
                name=prod,
                line=dict(color=COLORS[i % len(COLORS)], width=2),
                marker=dict(size=7),
                hovertemplate=(
                    "<b>%{x|%d %b %Y}</b><br>"
                    "Avg: %{y:.2f}<br>"
                    "<extra>" + prod + "</extra>"
                ),
            ))
        fig.update_layout(
            **PLOT,
            height=380,
            xaxis_title="Test Date",
            yaxis_title="Avg Test Result (confirm unit)",
            legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown('<div class="section-header">Summary by Product</div>', unsafe_allow_html=True)
        summary = (
            df.groupby("product")
            .agg(
                tests=("id", "count"),
                avg_strength=("average", "mean"),
                min_strength=("average", "min"),
                max_strength=("average", "max"),
                avg_curing=("curing_days", "mean"),
            )
            .round(2)
            .reset_index()
            .rename(columns={
                "product":      "Product",
                "tests":        "Tests",
                "avg_strength": "Avg",
                "min_strength": "Min",
                "max_strength": "Max",
                "avg_curing":   "Avg Curing (days)",
            })
        )
        st.dataframe(summary, use_container_width=True, hide_index=True)

    # ── Edit (admin only) ─────────────────────────────────────────────────────
    if role != "admin":
        return

    st.markdown("---")

    with st.expander("✏️ Edit a QC Record"):
        df_edit = get_quality()
        if df_edit.empty:
            st.info("No records to edit.")
        else:
            df_edit["test_date"] = pd.to_datetime(df_edit["test_date"], errors="coerce")
            df_edit = df_edit.sort_values(["test_date", "id"], ascending=[False, False]).reset_index(drop=True)
            df_edit["label"] = (
                df_edit["test_date"].dt.strftime("%d-%b-%Y") + " | " +
                df_edit["product"].astype(str) + " | Avg: " +
                df_edit["average"].astype(str) + " | ID:" +
                df_edit["id"].astype(str)
            )
            sel    = st.selectbox("Select record to edit", df_edit["label"].tolist(), key="edit_qc_sel")
            row    = df_edit.loc[df_edit["label"] == sel].iloc[0]
            row_id = int(row["id"])

            with st.form(f"edit_qc_form_{row_id}"):
                ec1, ec2, ec3 = st.columns(3)
                e_test_date    = ec1.date_input("Test Date",    pd.to_datetime(row["test_date"]))
                e_casting_date = ec2.date_input("Casting Date", pd.to_datetime(row["casting_date"]))
                e_product      = ec3.selectbox(
                    "Product", PRODUCTION_PRODUCTS,
                    index=PRODUCTION_PRODUCTS.index(row["product"])
                    if row["product"] in PRODUCTION_PRODUCTS else 0,
                )
                es1, es2, es3 = st.columns(3)
                e_s1 = es1.number_input("Sample 1", min_value=0.0, value=float(row.get("sample_1", 0) or 0), step=0.1)
                e_s2 = es2.number_input("Sample 2", min_value=0.0, value=float(row.get("sample_2", 0) or 0), step=0.1)
                e_s3 = es3.number_input("Sample 3", min_value=0.0, value=float(row.get("sample_3", 0) or 0), step=0.1)
                e_remarks = st.text_input("Remarks", value=str(row.get("remarks", "") or ""))
                save = st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True)

            if save:
                nonzero = [s for s in [e_s1, e_s2, e_s3] if s > 0]
                avg = round(sum(nonzero) / len(nonzero), 2) if nonzero else 0.0
                update_quality(row_id, {
                    "test_date":    str(e_test_date),
                    "casting_date": str(e_casting_date),
                    "product":      e_product,
                    "sample_1":     e_s1,
                    "sample_2":     e_s2,
                    "sample_3":     e_s3,
                    "average":      avg,
                    "remarks":      e_remarks.strip(),
                })
                st.success(f"✅ Record ID {row_id} updated.")
                st.rerun()

    with st.expander("🗑️ Delete QC Records"):
        df_del = get_quality()
        if df_del.empty:
            st.info("No records to delete.")
        else:
            df_del["test_date"] = pd.to_datetime(df_del["test_date"], errors="coerce")
            df_del = df_del.sort_values(["test_date", "id"], ascending=[False, False]).reset_index(drop=True)
            df_del["label"] = (
                df_del["test_date"].dt.strftime("%d-%b-%Y") + " | " +
                df_del["product"].astype(str) + " | Avg: " +
                df_del["average"].astype(str) + " | ID:" +
                df_del["id"].astype(str)
            )
            all_labels = df_del["label"].tolist()

            def _qc_select_all():
                st.session_state.del_qc_select = all_labels if st.session_state.del_qc_all else []

            st.checkbox("Select All", key="del_qc_all", on_change=_qc_select_all)
            selected_labels = st.multiselect(
                "Select records to delete",
                all_labels,
                key="del_qc_select",
            )
            if selected_labels:
                ids_to_delete = df_del.loc[df_del["label"].isin(selected_labels), "id"].tolist()
                st.warning(f"You are about to delete **{len(ids_to_delete)} record(s)**.")
                if st.button(f"🗑️ Confirm Delete ({len(ids_to_delete)})", type="primary", key="del_qc_btn"):
                    for rid in ids_to_delete:
                        delete_row("quality_control", int(rid))
                    st.success(f"✅ {len(ids_to_delete)} record(s) deleted.")
                    st.rerun()
