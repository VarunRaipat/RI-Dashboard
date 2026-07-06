import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, timedelta
from core.db import get_production, get_dispatch, get_orders, get_quality
from core.config import RAW_MATERIALS, HUME_PIPE_PRODUCTS

LAKH = 100_000


def _render_production_section(df_prod, df_disp, label, banner_color, PLOT):
    """Renders the Production & Financial Summary KPIs, Production Overview,
    Monthly Trends, and Cost Analysis for a given (already product-filtered)
    slice of production/dispatch data. Called once per product category
    (Pipes vs. everything else) so their profit/cost numbers are never
    blended together."""
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{banner_color}1F,{banner_color}0A);
         border:1px solid {banner_color}33; border-left:4px solid {banner_color};
         border-radius:10px; padding:10px 18px; margin-bottom:16px;">
        <span style="font-size:0.68rem;font-weight:700;letter-spacing:0.14em;
              text-transform:uppercase;color:{banner_color};">
            {label} — Production &amp; Financial Summary
        </span>
    </div>
    """, unsafe_allow_html=True)

    if df_prod.empty:
        st.info(f"No {label} production data for the selected period.")
        return

    total_nos      = df_prod["nos"].sum()
    total_revenue  = df_prod["revenue"].sum()
    total_cost     = df_prod["total_cost"].sum()
    total_profit   = df_prod["profit"].sum()
    avg_profit_pct = (total_profit / total_revenue * 100) if total_revenue else 0
    total_dispatch = df_disp["dispatch_value"].sum() if not df_disp.empty else 0

    # Per-product nos breakdown
    prod_nos = (df_prod.groupby("product")["nos"].sum()
                .reset_index().sort_values("nos", ascending=False))
    n = len(prod_nos)
    p_cols = st.columns(min(n, 4))
    for i, (_, prow) in enumerate(prod_nos.iterrows()):
        p_cols[i % len(p_cols)].metric(prow["product"], f"{int(prow['nos']):,} nos")
    if n > 1:
        st.caption(f"Total production: **{total_nos:,.0f} nos** across {n} products")

    # Financial KPIs
    f1, f2, f3, f4, f5 = st.columns(5)
    f1.metric("Production Value", f"₹{total_revenue/LAKH:.2f}L")
    f2.metric("Total Cost",       f"₹{total_cost/LAKH:.2f}L")
    f3.metric("Profit",           f"₹{total_profit/LAKH:.2f}L")
    f4.metric("Avg Profit %",     f"{avg_profit_pct:.1f}%")
    f5.metric("Dispatch Value",   f"₹{total_dispatch/LAKH:.2f}L")

    st.markdown("---")

    df_prod = df_prod.copy()
    df_prod["date"] = pd.to_datetime(df_prod["date"])

    with st.expander(f"📈 {label} — Production Overview", expanded=True):
        col1, col2 = st.columns(2)

        with col1:
            st.markdown('<div class="section-header">Daily Production</div>', unsafe_allow_html=True)
            fig = go.Figure(go.Bar(
                x=df_prod["date"], y=df_prod["nos"],
                marker_color=[
                    "#8B2428" if p >= 0 else "#5A3A3A"
                    for p in df_prod["profit_pct"].fillna(0)
                ],
                text=df_prod["product"].apply(lambda x: x[:8]),
                textposition="inside",
            ))
            fig.update_layout(**PLOT, height=320, yaxis_title="Nos.")
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.markdown('<div class="section-header">Production Mix by Value</div>', unsafe_allow_html=True)
            mix = df_prod.groupby("product")["revenue"].sum().reset_index()
            fig = px.pie(mix, values="revenue", names="product",
                         hole=0.42,
                         color_discrete_sequence=px.colors.qualitative.Bold)
            fig.update_traces(texttemplate="%{label}<br>₹%{value:,.0f}<br>%{percent}")
            fig.update_layout(**PLOT, height=320, showlegend=True)
            st.plotly_chart(fig, use_container_width=True)

        st.markdown('<div class="section-header">Daily Profit % Trend</div>', unsafe_allow_html=True)
        daily = (df_prod.groupby(["date", "product"])
                 .agg(revenue=("revenue", "sum"), profit=("profit", "sum"))
                 .reset_index())
        daily["profit_pct"] = daily.apply(
            lambda r: (r["profit"] / r["revenue"] * 100) if r["revenue"] else 0, axis=1
        )
        fig2 = go.Figure()
        for prod in daily["product"].unique():
            sub = daily[daily["product"] == prod].sort_values("date")
            fig2.add_trace(go.Scatter(
                x=sub["date"], y=sub["profit_pct"],
                mode="lines+markers", name=prod,
            ))
        fig2.add_hline(y=0, line_dash="dash", line_color="#FB7185", opacity=0.5)
        fig2.update_layout(**PLOT, height=300, yaxis_title="Profit %")
        st.plotly_chart(fig2, use_container_width=True)

        st.markdown('<div class="section-header">Product-wise P&L Summary</div>', unsafe_allow_html=True)
        agg_dict = {
            "Days":           ("date",       "nunique"),
            "Total_Nos":      ("nos",        "sum"),
            "Revenue":        ("revenue",    "sum"),
            "RM_Cost":        ("rm_cost",    "sum"),
            "Production":     ("production_cost", "sum"),
            "Loading":        ("loading_unloading_cost", "sum"),
            "Power":          ("power_cost", "sum"),
            "Welding":        ("welding_cost", "sum"),
            "Jalli":          ("jalli_cost",  "sum"),
            "EMI":            ("emi_cost",   "sum"),
            "DG":             ("dg_cost",    "sum"),
            "Admin":          ("admin_cost", "sum"),
            "Misc":           ("misc_cost",  "sum"),
            "Total_Cost":     ("total_cost", "sum"),
            "Profit":         ("profit",     "sum"),
        }
        agg_dict = {k: v for k, v in agg_dict.items() if v[0] in df_prod.columns}
        summ = df_prod.groupby("product").agg(**agg_dict).reset_index()
        if "Revenue" in summ.columns and "Profit" in summ.columns:
            summ["Avg_Profit_Pct"] = summ.apply(
                lambda r: (r["Profit"] / r["Revenue"] * 100) if r["Revenue"] else 0, axis=1
            )

        money_cols = ["Revenue","RM_Cost","Production","Loading","Power","Welding","Jalli","EMI","DG","Admin","Misc","Total_Cost","Profit"]
        for mc in money_cols:
            if mc in summ.columns:
                summ[mc] = (summ[mc] / LAKH).round(3)
        if "Avg_Profit_Pct" in summ.columns:
            summ["Avg_Profit_Pct"] = summ["Avg_Profit_Pct"].round(1)

        rename_map = {
            "product":"Product","Days":"Days","Total_Nos":"Nos.",
            "Revenue":"Prod Value(L)","RM_Cost":"RM(L)","Production":"Production(L)",
            "Loading":"Loading/Unload(L)","Power":"Power(L)","Welding":"Welding(L)","Jalli":"Jalli(L)",
            "EMI":"EMI(L)","DG":"DG(L)","Admin":"Admin(L)","Misc":"Misc(L)",
            "Total_Cost":"Total Cost(L)","Profit":"Profit(L)","Avg_Profit_Pct":"Profit%",
        }
        summ = summ.rename(columns={k: v for k, v in rename_map.items() if k in summ.columns})
        st.dataframe(summ, use_container_width=True, hide_index=True)

    with st.expander(f"📅 {label} — Monthly Trends", expanded=False):
        df_all = df_prod
        m_all = df_all.groupby(df_all["date"].dt.to_period("M").dt.to_timestamp()).agg(
            Nos        =("nos",         "sum"),
            Revenue    =("revenue",     "sum"),
            RM         =("rm_cost",     "sum"),
            Production =("production_cost", "sum"),
            Loading    =("loading_unloading_cost", "sum"),
            Power      =("power_cost",  "sum"),
            Welding    =("welding_cost","sum"),
            Jalli      =("jalli_cost",  "sum"),
            EMI        =("emi_cost",    "sum"),
            DG         =("dg_cost",     "sum"),
            Admin      =("admin_cost",  "sum"),
            Misc       =("misc_cost",   "sum"),
            Total_Cost =("total_cost",  "sum"),
            Profit     =("profit",      "sum"),
            Days       =("date",        "nunique"),
        ).reset_index().rename(columns={"date": "month"}).sort_values("month")
        m_all["Profit_Pct"] = m_all.apply(
            lambda r: (r["Profit"] / r["Revenue"] * 100) if r["Revenue"] else 0, axis=1
        )

        st.markdown('<div class="section-header">Monthly Revenue vs Profit</div>', unsafe_allow_html=True)
        fig_mrev = go.Figure()
        fig_mrev.add_trace(go.Bar(
            x=m_all["month"].dt.strftime("%b %Y"),
            y=(m_all["Revenue"] / LAKH).round(2),
            marker_color=["#8B2428" if p >= 0 else "#4A2020" for p in m_all["Profit"]],
            text=(m_all["Revenue"] / LAKH).round(2).astype(str) + "L",
            textposition="outside",
            name="Production Value",
        ))
        fig_mrev.add_trace(go.Scatter(
            x=m_all["month"].dt.strftime("%b %Y"),
            y=(m_all["Profit"] / LAKH).round(2),
            mode="lines+markers+text",
            name="Profit",
            line=dict(color="#D4A011", width=2),
            marker=dict(size=7),
            text=(m_all["Profit"] / LAKH).round(2).astype(str) + "L",
            textposition="top center",
            yaxis="y2",
        ))
        fig_mrev.update_layout(
            **PLOT, height=360,
            yaxis=dict(title=dict(text="Prod Value (L)", font=dict(color="#00C49A"))),
            yaxis2=dict(title=dict(text="Profit (L)", font=dict(color="#FDBA44")),
                        overlaying="y", side="right"),
            legend=dict(orientation="h", y=1.08),
            barmode="group",
        )
        st.plotly_chart(fig_mrev, use_container_width=True)

        st.markdown('<div class="section-header">Monthly Profit by Product (L)</div>', unsafe_allow_html=True)
        m_prod = df_all.groupby([df_all["date"].dt.to_period("M").dt.to_timestamp(), "product"])["profit"].sum().reset_index()
        m_prod = m_prod.rename(columns={"date": "month"})
        m_prod["month_str"] = m_prod["month"].dt.strftime("%b %Y")
        m_prod["profit_L"]  = (m_prod["profit"] / LAKH).round(3)
        months_ordered = sorted(m_prod["month"].unique())
        month_labels   = [pd.Timestamp(m).strftime("%b %Y") for m in months_ordered]
        products       = sorted(m_prod["product"].unique())
        PROD_COLORS    = ["#8B2428","#3B82F6","#D4A011","#A78BFA","#27AE60","#22D3EE","#E05252","#E879F9"]
        fig_mprod = go.Figure()
        for i, prod in enumerate(products):
            sub = m_prod[m_prod["product"] == prod][["month_str","profit_L"]]
            sub = sub.set_index("month_str").reindex(month_labels, fill_value=0).reset_index()
            fig_mprod.add_trace(go.Bar(
                x=sub["month_str"],
                y=sub["profit_L"],
                name=prod,
                marker_color=PROD_COLORS[i % len(PROD_COLORS)],
                text=sub["profit_L"].apply(lambda v: f"{v:.2f}L" if v != 0 else ""),
                textposition="inside",
            ))
        fig_mprod.update_layout(
            **PLOT, height=380, barmode="stack",
            yaxis_title="Profit (L)",
            legend=dict(orientation="h", y=1.08),
        )
        st.plotly_chart(fig_mprod, use_container_width=True)

        st.markdown('<div class="section-header">Monthly Breakup — All Months</div>', unsafe_allow_html=True)
        tbl = m_all.copy()
        tbl["Month"] = tbl["month"].dt.strftime("%b %Y")
        for mc in ["Revenue","RM","Production","Loading","Power","Welding","Jalli","EMI","DG","Admin","Misc","Total_Cost","Profit"]:
            if mc in tbl.columns:
                tbl[mc] = (tbl[mc] / LAKH).round(3)
        tbl["Profit_Pct"] = tbl["Profit_Pct"].round(1)
        tbl["Nos"] = tbl["Nos"].astype(int)
        tbl = tbl.rename(columns={
            "Month":"Month","Days":"Days","Nos":"Nos.",
            "Revenue":"Prod Value(L)","RM":"RM(L)","Production":"Production(L)",
            "Loading":"Loading/Unload(L)","Power":"Power(L)","Welding":"Welding(L)","Jalli":"Jalli(L)",
            "EMI":"EMI(L)","DG":"DG(L)","Admin":"Admin(L)","Misc":"Misc(L)",
            "Total_Cost":"Total Cost(L)","Profit":"Profit(L)","Profit_Pct":"Profit%",
        })
        display_cols = ["Month","Days","Nos.","Prod Value(L)","RM(L)","Production(L)","Loading/Unload(L)",
                        "Power(L)","Welding(L)","Jalli(L)","EMI(L)","DG(L)",
                        "Admin(L)","Misc(L)","Total Cost(L)","Profit(L)","Profit%"]
        display_cols = [c for c in display_cols if c in tbl.columns]
        st.dataframe(tbl[display_cols], use_container_width=True, hide_index=True)

    with st.expander(f"💰 {label} — Cost Analysis", expanded=False):
        col3, col4 = st.columns(2)
        with col3:
            st.markdown('<div class="section-header">Cost Breakdown (Period)</div>', unsafe_allow_html=True)
            cost_labels = ["Raw Material","Production","Loading/Unload","Power","Welding","Jalli","EMI","DG","Admin","Misc"]
            cost_vals   = [
                df_prod["rm_cost"].sum(),
                df_prod["production_cost"].sum() if "production_cost" in df_prod.columns else 0,
                df_prod["loading_unloading_cost"].sum() if "loading_unloading_cost" in df_prod.columns else 0,
                df_prod["power_cost"].sum(),
                df_prod["welding_cost"].sum() if "welding_cost" in df_prod.columns else 0,
                df_prod["jalli_cost"].sum() if "jalli_cost" in df_prod.columns else 0,
                df_prod["emi_cost"].sum()   if "emi_cost"   in df_prod.columns else 0,
                df_prod["dg_cost"].sum()    if "dg_cost"    in df_prod.columns else 0,
                df_prod["admin_cost"].sum() if "admin_cost" in df_prod.columns else 0,
                df_prod["misc_cost"].sum()  if "misc_cost"  in df_prod.columns else 0,
            ]
            fig3 = go.Figure(go.Pie(
                labels=cost_labels, values=cost_vals, hole=0.42,
                textinfo="label+percent",
                marker_colors=["#8B2428","#3B82F6","#D4A011","#A78BFA","#27AE60","#22D3EE","#E05252","#E879F9","#F97316","#14B8A6"],
            ))
            fig3.update_layout(**PLOT, height=300, showlegend=False)
            st.plotly_chart(fig3, use_container_width=True)

        with col4:
            st.markdown('<div class="section-header">Avg Profit % by Product</div>', unsafe_allow_html=True)
            pp = df_prod.groupby("product").agg(revenue=("revenue","sum"), profit=("profit","sum")).reset_index()
            pp["profit_pct"] = pp.apply(
                lambda r: (r["profit"] / r["revenue"] * 100) if r["revenue"] else 0, axis=1
            )
            pp = pp.sort_values("profit_pct").reset_index(drop=True)
            fig4 = go.Figure(go.Bar(
                x=pp["profit_pct"], y=pp["product"], orientation="h",
                marker_color=[
                    "#27AE60" if v >= 25 else "#D4A011" if v >= 10 else "#8B2428"
                    for v in pp["profit_pct"]
                ],
                text=[f"{v:.1f}%" for v in pp["profit_pct"]],
                textposition="outside",
            ))
            fig4.update_layout(**PLOT, height=300, xaxis_title="%")
            st.plotly_chart(fig4, use_container_width=True)

        st.markdown('<div class="section-header">Raw Material Usage</div>', unsafe_allow_html=True)
        rm_cols  = [f"{m['key']}_qty" for m in RAW_MATERIALS]
        rm_labels = {f"{m['key']}_qty": f"{m['label']} ({m['unit']})" for m in RAW_MATERIALS}
        rm_avail = [c for c in rm_cols if c in df_prod.columns]
        if rm_avail:
            rm_df = df_prod[rm_avail].sum().reset_index()
            rm_df.columns = ["Material","Total"]
            rm_df["Material"] = rm_df["Material"].map(rm_labels)
            rm_df["Total"]    = rm_df["Total"].round(1)
            st.dataframe(rm_df, use_container_width=True, hide_index=True)


def show(PLOT):
    st.markdown("""
    <div class="page-title">📊 KPI Dashboard</div>
    <div class="page-subtitle">Production · Costs · Profit · Dispatch — all in one view</div>
    """, unsafe_allow_html=True)

    # ── Date filter ───────────────────────────────────────────────────────────
    today = date.today()

    if "dash_date_start" not in st.session_state:
        st.session_state["dash_date_start"] = today.replace(day=1)
    if "dash_date_end" not in st.session_state:
        st.session_state["dash_date_end"] = today

    qb1, qb2, qb3, qb4, qb5, _ = st.columns([1, 1, 1, 1, 1, 3])
    if qb1.button("Today", use_container_width=True):
        st.session_state["dash_date_start"] = today
        st.session_state["dash_date_end"]   = today
    if qb2.button("Yesterday", use_container_width=True):
        yesterday = today - timedelta(days=1)
        st.session_state["dash_date_start"] = yesterday
        st.session_state["dash_date_end"]   = yesterday
    if qb3.button("This Week", use_container_width=True):
        st.session_state["dash_date_start"] = today - timedelta(days=today.weekday())
        st.session_state["dash_date_end"]   = today
    if qb4.button("This Month", use_container_width=True):
        st.session_state["dash_date_start"] = today.replace(day=1)
        st.session_state["dash_date_end"]   = today
    if qb5.button("This Year", use_container_width=True):
        st.session_state["dash_date_start"] = today.replace(month=1, day=1)
        st.session_state["dash_date_end"]   = today

    c1, c2 = st.columns(2)
    start = c1.date_input("From", key="dash_date_start")
    end   = c2.date_input("To",   key="dash_date_end")

    df_prod = get_production(str(start), str(end))
    df_disp = get_dispatch(str(start), str(end))

    # ── Inventory Snapshot (point-in-time — not affected by the date filter) ──
    from core.inventory import finished_goods_summary, rm_summary

    fg_inv = finished_goods_summary()
    rm_inv = rm_summary()
    fg_value = fg_inv["Value (₹)"].sum() if not fg_inv.empty else 0
    rm_value = rm_inv["Value (₹)"].sum() if not rm_inv.empty else 0
    low_stock_ct = int((fg_inv["Current Stock"] < 0).sum()) if not fg_inv.empty else 0

    st.markdown("""
    <div style="background:linear-gradient(135deg,rgba(59,130,246,0.12),rgba(59,130,246,0.04));
         border:1px solid rgba(59,130,246,0.20); border-left:4px solid #3B82F6;
         border-radius:10px; padding:10px 18px; margin-bottom:16px;">
        <span style="font-size:0.68rem;font-weight:700;letter-spacing:0.14em;
              text-transform:uppercase;color:#7AA7E8;">
            📦 Inventory Snapshot
        </span>
    </div>
    """, unsafe_allow_html=True)

    i1, i2, i3, i4 = st.columns(4)
    i1.metric("Finished Goods Value",  f"₹{fg_value/LAKH:.2f}L")
    i2.metric("Cement + GGBS Value",   f"₹{rm_value/LAKH:.2f}L")
    i3.metric("Total Inventory Value", f"₹{(fg_value + rm_value)/LAKH:.2f}L")
    i4.metric("Products Low/Negative", f"{low_stock_ct}",
             delta=("check stock" if low_stock_ct else "all ok"),
             delta_color=("inverse" if low_stock_ct else "off"))

    if not fg_inv.empty and low_stock_ct:
        low_df = fg_inv[fg_inv["Current Stock"] < 0].sort_values("Current Stock").copy()
        low_df["Current Stock"] = low_df["Current Stock"].round(0).astype(int)
        st.markdown(
            f'<div class="warn-box">⚠️ <b>{low_stock_ct} product(s)</b> with negative stock — '
            f'production hasn\'t caught up with what\'s been dispatched</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(
            low_df[["Product", "Opening", "Produced", "Dispatched", "Current Stock"]],
            use_container_width=True, hide_index=True,
        )

    if not fg_inv.empty:
        top_stock = fg_inv[fg_inv["Current Stock"] > 0].sort_values("Value (₹)", ascending=False).head(8)
        if not top_stock.empty:
            fig_inv = go.Figure(go.Bar(
                x=(top_stock["Value (₹)"] / LAKH).round(2),
                y=top_stock["Product"],
                orientation="h",
                marker_color="#3B82F6",
                text=(top_stock["Value (₹)"] / LAKH).round(2).astype(str) + "L",
                textposition="outside",
            ))
            fig_inv.update_layout(**PLOT, height=280, xaxis_title="Value (L)",
                                  yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig_inv, use_container_width=True)

    st.markdown("---")

    if df_prod.empty and df_disp.empty:
        st.warning("No data for selected period. Enter some DPR or Dispatch records first.")
        return

    # -- Production financials, split by category so Pipe economics never
    # blend with Slab/Pillar/Fencing Pillar/PSC Pole economics --------------
    is_pipe = df_prod["product"].isin(HUME_PIPE_PRODUCTS) if not df_prod.empty else pd.Series(dtype=bool)
    df_prod_pipe  = df_prod[is_pipe]  if not df_prod.empty else df_prod
    df_prod_other = df_prod[~is_pipe] if not df_prod.empty else df_prod

    is_disp_pipe = df_disp["product"].isin(HUME_PIPE_PRODUCTS) if not df_disp.empty else pd.Series(dtype=bool)
    df_disp_pipe  = df_disp[is_disp_pipe]  if not df_disp.empty else df_disp
    df_disp_other = df_disp[~is_disp_pipe] if not df_disp.empty else df_disp

    _render_production_section(df_prod_pipe, df_disp_pipe, "Pipe Products", "#8B2428", PLOT)
    st.markdown("---")
    _render_production_section(df_prod_other, df_disp_other, "Other Precast Products", "#3B82F6", PLOT)


    # ── Dispatch & Sales ──────────────────────────────────────────────────────
    with st.expander("🚚 Dispatch & Sales", expanded=True):
        if df_disp.empty:
            st.info("No dispatch data for selected period.")
        else:
            df_disp["date"] = pd.to_datetime(df_disp["date"])

            if "sale_type" in df_disp.columns:
                a_disp = df_disp.loc[df_disp["sale_type"] == "Sale A", "dispatch_value"].sum()
                b_disp = df_disp.loc[df_disp["sale_type"] == "Sale B", "dispatch_value"].sum()
                dsp1, dsp2 = st.columns(2)
                dsp1.metric("Sale A Dispatch Value", f"₹{a_disp/LAKH:.2f}L")
                dsp2.metric("Sale B Dispatch Value", f"₹{b_disp/LAKH:.2f}L")
                st.markdown("---")

            col5, col6 = st.columns(2)
            with col5:
                cl_all   = df_disp.groupby("client_name")["dispatch_value"].sum().reset_index().sort_values("dispatch_value", ascending=False)
                top10_cl = cl_all.head(10)
                others_v = cl_all.iloc[10:]["dispatch_value"].sum()
                if others_v > 0:
                    top10_cl = pd.concat([top10_cl, pd.DataFrame([{"client_name":"Others","dispatch_value":others_v}])], ignore_index=True)
                DCOLORS = ["#8B2428","#3B82F6","#D4A011","#A78BFA","#27AE60","#22D3EE","#E05252","#E879F9","#F97316","#14B8A6","#888888"]
                fig5 = go.Figure(go.Pie(
                    labels=top10_cl["client_name"], values=top10_cl["dispatch_value"],
                    hole=0.4, textinfo="percent",
                    hovertemplate="%{label}<br>₹%{value:,.0f}<br>%{percent}<extra></extra>",
                    marker_colors=DCOLORS,
                ))
                fig5.update_layout(**PLOT, height=320, title="Top 10 Clients — Dispatch Value",
                                   showlegend=True, legend=dict(orientation="v", x=1.02, y=0.5, font=dict(size=10)))
                st.plotly_chart(fig5, use_container_width=True)

            with col6:
                prod_disp = df_disp.groupby("product")["dispatch_value"].sum().reset_index()
                prod_disp["Value (L)"] = (prod_disp["dispatch_value"] / LAKH).round(2)
                fig6 = px.bar(prod_disp, x="product", y="Value (L)",
                              title="Billed Value by Product (L)",
                              color="product",
                              color_discrete_sequence=px.colors.qualitative.Bold,
                              text="Value (L)")
                fig6.update_traces(texttemplate="%{text}L", textposition="outside")
                fig6.update_layout(**PLOT, height=300, showlegend=False, yaxis_title="Value (L)")
                st.plotly_chart(fig6, use_container_width=True)

            dr1, dr2 = st.columns(2)
            with dr1:
                st.markdown("**Driver-wise Trips & Value**")
                drv = df_disp.groupby("driver_name").agg(
                    Trips=("id","count"),
                    Value=("dispatch_value","sum"),
                ).reset_index().rename(columns={"driver_name":"Driver"})
                drv["Value (L)"] = (drv["Value"] / LAKH).round(2)
                st.dataframe(drv[["Driver","Trips","Value (L)"]].sort_values("Value (L)", ascending=False),
                             use_container_width=True, hide_index=True)

            with dr2:
                st.markdown("**Truck-wise Trips & Value**")
                trk = df_disp.groupby("truck_no").agg(
                    Trips   =("id",            "count"),
                    Total_km=("trip_distance",  "sum"),
                    Value   =("dispatch_value", "sum"),
                ).reset_index().rename(columns={"truck_no":"Truck"})
                trk["Value (L)"] = (trk["Value"] / LAKH).round(2)
                st.dataframe(trk[["Truck","Trips","Total_km","Value (L)"]].sort_values("Value (L)", ascending=False),
                             use_container_width=True, hide_index=True)

    # ── Sales Orders Pipeline ─────────────────────────────────────────────────
    with st.expander("📦 Sales Orders Pipeline", expanded=True):
        df_ord = get_orders()
        if df_ord.empty:
            st.info("No sales orders yet.")
        else:
            df_ord["order_date"] = pd.to_datetime(df_ord["order_date"], errors="coerce")

            if not df_disp.empty and "di_no" in df_disp.columns:
                disp_di = df_disp.groupby("di_no").agg(
                    dispatched_value=("dispatch_value","sum"),
                    dispatched_qty  =("qty_dispatched","sum"),
                ).reset_index()
            else:
                disp_di = pd.DataFrame(columns=["di_no","dispatched_value","dispatched_qty"])

            di_sum = df_ord.groupby("di_no").agg(
                order_date   =("order_date",   "first"),
                client_name  =("client_name",  "first"),
                products     =("product",       lambda x: ", ".join(x.dropna().unique())),
                total_ordered=("total_amount", "sum"),
                qty_ordered  =("qty_ordered",  "sum"),
            ).reset_index()

            di_sum = di_sum.merge(disp_di, on="di_no", how="left")
            di_sum["dispatched_value"] = di_sum["dispatched_value"].fillna(0)
            di_sum["dispatched_qty"]   = di_sum["dispatched_qty"].fillna(0)
            di_sum["pending_value"]    = di_sum["total_ordered"] - di_sum["dispatched_value"]
            di_sum["pending_qty"]      = di_sum["qty_ordered"]   - di_sum["dispatched_qty"]

            def _status(row):
                if row["dispatched_qty"] <= 0: return "🔴 Pending"
                if row["pending_qty"] > 1:     return "🟡 Partial"
                return "🟢 Fulfilled"
            di_sum["Status"] = di_sum.apply(_status, axis=1)

            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Total DIs",        f"{di_sum['di_no'].nunique()}")
            s2.metric("Total Order Value", f"₹{di_sum['total_ordered'].sum()/LAKH:.2f}L")
            s3.metric("Dispatched Value",  f"₹{di_sum['dispatched_value'].sum()/LAKH:.2f}L")
            s4.metric("Pending Value",     f"₹{di_sum['pending_value'].sum()/LAKH:.2f}L")

            if "sale_type" in df_ord.columns:
                a_ord = df_ord.loc[df_ord["sale_type"] == "Sale A", "total_amount"].sum()
                b_ord = df_ord.loc[df_ord["sale_type"] == "Sale B", "total_amount"].sum()
                so1, so2 = st.columns(2)
                so1.metric("Sale A Order Value", f"₹{a_ord/LAKH:.2f}L")
                so2.metric("Sale B Order Value", f"₹{b_ord/LAKH:.2f}L")

            st.markdown("---")

            status_counts = di_sum["Status"].value_counts().reset_index()
            status_counts.columns = ["Status","Count"]
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown('<div class="section-header">Orders by Status</div>', unsafe_allow_html=True)
                status_color = {"🔴 Pending":"#8B2428","🟡 Partial":"#D4A011","🟢 Fulfilled":"#27AE60"}
                fig_s = go.Figure(go.Bar(
                    x=status_counts["Status"], y=status_counts["Count"],
                    marker_color=[status_color.get(s,"#888") for s in status_counts["Status"]],
                    text=status_counts["Count"], textposition="outside",
                ))
                fig_s.update_layout(**PLOT, height=260, showlegend=False)
                st.plotly_chart(fig_s, use_container_width=True)

            with col_b:
                st.markdown('<div class="section-header">Ordered vs Dispatched (L)</div>', unsafe_allow_html=True)
                top_clients = di_sum.groupby("client_name").agg(
                    Ordered   =("total_ordered",    "sum"),
                    Dispatched=("dispatched_value", "sum"),
                ).reset_index().sort_values("Ordered", ascending=False).head(8)
                fig_c = go.Figure()
                fig_c.add_trace(go.Bar(name="Ordered",    x=top_clients["client_name"], y=(top_clients["Ordered"]/LAKH).round(2),    marker_color="#A78BFA"))
                fig_c.add_trace(go.Bar(name="Dispatched", x=top_clients["client_name"], y=(top_clients["Dispatched"]/LAKH).round(2), marker_color="#27AE60"))
                fig_c.update_layout(**PLOT, height=260, barmode="group", yaxis_title="Value (L)", legend=dict(orientation="h", y=1.1))
                st.plotly_chart(fig_c, use_container_width=True)

            st.markdown('<div class="section-header">DI Pipeline</div>', unsafe_allow_html=True)
            tbl = di_sum.sort_values("order_date", ascending=False).copy()
            tbl["order_date"] = tbl["order_date"].dt.strftime("%d-%b-%Y")
            for mc in ["total_ordered","dispatched_value","pending_value"]:
                tbl[mc] = (tbl[mc] / LAKH).round(3)
            tbl = tbl.rename(columns={
                "di_no":"DI No.","order_date":"Date","client_name":"Client",
                "products":"Products","Status":"Status",
                "total_ordered":"Order (L)","dispatched_value":"Dispatched (L)","pending_value":"Pending (L)",
            })
            st.dataframe(
                tbl[["DI No.","Date","Client","Products","Status","Order (L)","Dispatched (L)","Pending (L)"]],
                use_container_width=True, hide_index=True,
            )

            if "mode_of_payment" in df_ord.columns or "client_type" in df_ord.columns:
                st.markdown("---")

            if "client_type" in df_ord.columns:
                st.markdown('<div class="section-header">Client Type Mix</div>', unsafe_allow_html=True)
                type_mix = df_ord.groupby("client_type")["total_amount"].sum().reset_index()
                type_mix = type_mix[type_mix["total_amount"] > 0]
                type_mix["Value (L)"] = (type_mix["total_amount"] / LAKH).round(2)
                ct1, ct2 = st.columns(2)
                with ct1:
                    fig_ctype = go.Figure(go.Pie(
                        labels=type_mix["client_type"],
                        values=type_mix["total_amount"],
                        hole=0.45, textinfo="label+percent",
                        marker_colors=["#D4A011","#3B82F6","#8B2428","#27AE60","#A78BFA"],
                    ))
                    fig_ctype.update_layout(**PLOT, height=280, showlegend=False)
                    st.plotly_chart(fig_ctype, use_container_width=True)
                with ct2:
                    st.dataframe(
                        type_mix[["client_type","Value (L)"]].rename(columns={"client_type":"Client Type"})
                        .sort_values("Value (L)", ascending=False),
                        use_container_width=True, hide_index=True,
                    )
                st.markdown("---")

            if "mode_of_payment" in df_ord.columns:
                st.markdown('<div class="section-header">Payment Mode Mix</div>', unsafe_allow_html=True)
                pay_mix = df_ord.groupby("mode_of_payment")["total_amount"].sum().reset_index()
                pay_mix = pay_mix[pay_mix["total_amount"] > 0]
                pay_mix["Value (L)"] = (pay_mix["total_amount"] / LAKH).round(2)
                pm1, pm2 = st.columns(2)
                with pm1:
                    fig_pay = go.Figure(go.Pie(
                        labels=pay_mix["mode_of_payment"],
                        values=pay_mix["total_amount"],
                        hole=0.45, textinfo="label+percent",
                        marker_colors=["#8B2428","#3B82F6","#D4A011","#A78BFA","#27AE60","#22D3EE"],
                    ))
                    fig_pay.update_layout(**PLOT, height=280, showlegend=False)
                    st.plotly_chart(fig_pay, use_container_width=True)
                with pm2:
                    st.dataframe(
                        pay_mix[["mode_of_payment","Value (L)"]].rename(columns={"mode_of_payment":"Payment Mode"})
                        .sort_values("Value (L)", ascending=False),
                        use_container_width=True, hide_index=True,
                    )

            st.markdown("---")
            st.markdown('<div class="section-header">Repeat Clients & Avg Order Size</div>', unsafe_allow_html=True)
            client_stats = df_ord.groupby("client_name").agg(
                Orders     =("di_no",       "nunique"),
                Total_Value=("total_amount","sum"),
                Avg_Order  =("total_amount","mean"),
            ).reset_index().sort_values("Total_Value", ascending=False)
            client_stats["Total (L)"]     = (client_stats["Total_Value"] / LAKH).round(2)
            client_stats["Avg Order (₹)"] = client_stats["Avg_Order"].round(0).astype(int)
            client_stats["Type"]          = client_stats["Orders"].apply(lambda x: "🔄 Repeat" if x > 1 else "1️⃣ One-time")
            st.dataframe(
                client_stats[["client_name","Orders","Type","Total (L)","Avg Order (₹)"]]
                .rename(columns={"client_name":"Client"}),
                use_container_width=True, hide_index=True,
            )

    # ── Quality Control ───────────────────────────────────────────────────────
    with st.expander("🧪 Quality Control — Compressive Strength", expanded=True):
        df_qc = get_quality()
        if df_qc.empty:
            st.info("No quality test records yet.")
        else:
            df_qc["test_date"]    = pd.to_datetime(df_qc["test_date"],    errors="coerce")
            df_qc["casting_date"] = pd.to_datetime(df_qc["casting_date"], errors="coerce")
            df_qc["curing_days"]  = (df_qc["test_date"] - df_qc["casting_date"]).dt.days

            qc_mask = (df_qc["test_date"] >= pd.Timestamp(start)) & (df_qc["test_date"] <= pd.Timestamp(end))
            df_qc_p = df_qc[qc_mask].copy()

            if df_qc_p.empty:
                st.info("No quality test records for the selected period.")
            else:
                q1, q2, q3, q4 = st.columns(4)
                q1.metric("Tests in Period",   f"{len(df_qc_p)}")
                q2.metric("Avg Strength",      f"{df_qc_p['average'].mean():.2f} N/mm²")
                q3.metric("Min Strength",      f"{df_qc_p['average'].min():.2f} N/mm²")
                q4.metric("Max Strength",      f"{df_qc_p['average'].max():.2f} N/mm²")

                # Bucket each test by curing age — standard concrete testing ages.
                def _curing_bucket(d):
                    if d <= 10:  return "7-Day"
                    if d <= 20:  return "14-Day"
                    return "28-Day"
                df_qc_p["age_group"] = df_qc_p["curing_days"].apply(_curing_bucket)

                QC_COLORS = ["#00C49A", "#3B82F6", "#FDBA44", "#A78BFA", "#FB7185",
                             "#34D399", "#F97316", "#22D3EE"]

                st.markdown('<div class="section-header">Strength by Curing Age</div>', unsafe_allow_html=True)
                age_tabs = st.tabs(["7-Day", "14-Day", "28-Day"])
                for age_label, age_tab in zip(["7-Day", "14-Day", "28-Day"], age_tabs):
                    with age_tab:
                        df_age = df_qc_p[df_qc_p["age_group"] == age_label]
                        if df_age.empty:
                            st.info(f"No {age_label} results in the selected period.")
                            continue

                        a1, a2, a3, a4 = st.columns(4)
                        a1.metric("Tests",        f"{len(df_age)}")
                        a2.metric("Avg Strength", f"{df_age['average'].mean():.2f} N/mm²")
                        a3.metric("Min Strength", f"{df_age['average'].min():.2f} N/mm²")
                        a4.metric("Max Strength", f"{df_age['average'].max():.2f} N/mm²")

                        fig_age = go.Figure()
                        for i, prod in enumerate(sorted(df_age["product"].dropna().unique())):
                            pdata = df_age[df_age["product"] == prod].sort_values("test_date")
                            fig_age.add_trace(go.Scatter(
                                x=pdata["test_date"], y=pdata["average"],
                                mode="lines+markers", name=prod,
                                line=dict(color=QC_COLORS[i % len(QC_COLORS)], width=2),
                                marker=dict(size=7),
                                hovertemplate="<b>%{x|%d %b %Y}</b><br>Avg: %{y:.2f} N/mm²<extra>" + prod + "</extra>",
                            ))
                        fig_age.update_layout(**PLOT, height=300, xaxis_title="Test Date",
                                              yaxis_title="Avg Compressive Strength (N/mm²)",
                                              hovermode="x unified")
                        st.plotly_chart(fig_age, use_container_width=True)

                        age_summary = (
                            df_age.groupby("product")
                            .agg(Tests=("id","count"), Avg=("average","mean"),
                                 Min=("average","min"), Max=("average","max"))
                            .round(2).reset_index()
                            .rename(columns={"product":"Product","Avg":"Avg (N/mm²)",
                                             "Min":"Min (N/mm²)","Max":"Max (N/mm²)"})
                        )
                        st.dataframe(age_summary, use_container_width=True, hide_index=True)

                st.markdown('<div class="section-header">Summary by Product — Period (All Ages)</div>', unsafe_allow_html=True)
                qc_summary = (
                    df_qc_p.groupby("product")
                    .agg(Tests=("id","count"), Avg=("average","mean"),
                         Min=("average","min"), Max=("average","max"),
                         Avg_Curing=("curing_days","mean"))
                    .round(2).reset_index()
                    .rename(columns={"product":"Product","Avg":"Avg (N/mm²)",
                                     "Min":"Min (N/mm²)","Max":"Max (N/mm²)",
                                     "Avg_Curing":"Avg Curing (days)"})
                )
                st.dataframe(qc_summary, use_container_width=True, hide_index=True)

    # ── Advanced Analytics ────────────────────────────────────────────────────
    with st.expander("🔍 Advanced Analytics", expanded=False):
        df_prod_all = get_production()
        df_disp_all = get_dispatch()

        if not df_prod_all.empty:
            df_prod_all["date"] = pd.to_datetime(df_prod_all["date"], errors="coerce")
        if not df_disp_all.empty:
            df_disp_all["date"] = pd.to_datetime(df_disp_all["date"], errors="coerce")

        if not df_prod_all.empty and "profit_pct" in df_prod_all.columns:
            st.markdown('<div class="section-header">Profit % Trend by Product (Monthly)</div>', unsafe_allow_html=True)
            df_prod_all["month"] = df_prod_all["date"].dt.to_period("M").dt.to_timestamp()
            trend = df_prod_all.groupby(["month","product"]).agg(
                revenue=("revenue", "sum"), profit=("profit", "sum")
            ).reset_index()
            trend["profit_pct"] = trend.apply(
                lambda r: (r["profit"] / r["revenue"] * 100) if r["revenue"] else 0, axis=1
            )
            trend["month_str"] = trend["month"].dt.strftime("%b %Y")
            fig_trend = go.Figure()
            TCOLORS = ["#8B2428","#3B82F6","#D4A011","#A78BFA","#27AE60","#22D3EE","#E05252","#E879F9"]
            for i, prod in enumerate(sorted(trend["product"].unique())):
                sub = trend[trend["product"] == prod].sort_values("month")
                fig_trend.add_trace(go.Scatter(
                    x=sub["month_str"], y=sub["profit_pct"].round(1),
                    mode="lines+markers+text",
                    name=prod,
                    line=dict(color=TCOLORS[i % len(TCOLORS)], width=2),
                    marker=dict(size=7),
                    text=sub["profit_pct"].round(1).astype(str) + "%",
                    textposition="top center",
                ))
            fig_trend.add_hline(y=10, line_dash="dash", line_color="#FB7185",
                                annotation_text="10% threshold", annotation_position="bottom right")
            fig_trend.update_layout(**PLOT, height=340, yaxis_title="Profit %",
                                    legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig_trend, use_container_width=True)

        if not df_prod_all.empty and "profit_pct" in df_prod_all.columns:
            low = df_prod_all[df_prod_all["profit_pct"] < 10].copy()
            if not low.empty:
                low_sorted = low.sort_values("profit_pct").head(20)
                low_sorted["Date"]     = low_sorted["date"].dt.strftime("%d-%b-%Y")
                low_sorted["Profit %"] = low_sorted["profit_pct"].round(1)
                low_sorted["Profit ₹"] = low_sorted["profit"].round(0).astype(int)
                st.markdown(
                    f'<div class="warn-box">⚠️ <b>{len(low)} entries</b> with Profit % below 10%</div>',
                    unsafe_allow_html=True,
                )
                st.dataframe(
                    low_sorted[["Date","product","nos","Profit %","Profit ₹"]]
                    .rename(columns={"product":"Product","nos":"Nos."}),
                    use_container_width=True, hide_index=True,
                )

        if not df_prod_all.empty and "plant" in df_prod_all.columns:
            st.markdown("---")
            st.markdown('<div class="section-header">Plant Performance</div>', unsafe_allow_html=True)
            plant = df_prod_all.groupby("plant").agg(
                Entries =("id",         "count"),
                Nos     =("nos",        "sum"),
                Revenue =("revenue",    "sum"),
                Cost    =("total_cost", "sum"),
                Profit  =("profit",     "sum"),
            ).reset_index()
            plant["Revenue (L)"]  = (plant["Revenue"] / LAKH).round(2)
            plant["Cost (L)"]     = (plant["Cost"]    / LAKH).round(2)
            plant["Profit (L)"]   = (plant["Profit"]  / LAKH).round(2)
            plant["Avg Profit %"] = plant.apply(
                lambda r: round(r["Profit"] / r["Revenue"] * 100, 1) if r["Revenue"] else 0, axis=1
            )
            pv1, pv2 = st.columns(2)
            with pv1:
                st.dataframe(
                    plant[["plant","Entries","Nos","Revenue (L)","Cost (L)","Profit (L)","Avg Profit %"]]
                    .rename(columns={"plant":"Plant"}),
                    use_container_width=True, hide_index=True,
                )
            with pv2:
                fig_plant = go.Figure()
                fig_plant.add_trace(go.Bar(name="Revenue (L)", x=plant["plant"], y=plant["Revenue (L)"], marker_color="#3B82F6"))
                fig_plant.add_trace(go.Bar(name="Profit (L)",  x=plant["plant"], y=plant["Profit (L)"],  marker_color="#27AE60"))
                fig_plant.update_layout(**PLOT, height=260, barmode="group",
                                        legend=dict(orientation="h", y=1.1))
                st.plotly_chart(fig_plant, use_container_width=True)

        if not df_prod_all.empty:
            st.markdown("---")
            st.markdown('<div class="section-header">Idle Days (No Production)</div>', unsafe_allow_html=True)
            prod_dates = set(df_prod_all["date"].dt.date)
            all_days   = pd.date_range(start=start, end=end, freq="D")
            idle       = [d.date() for d in all_days if d.date() not in prod_dates]
            if idle:
                st.markdown(
                    f'<div class="warn-box">🏭 <b>{len(idle)} idle day(s)</b> in selected period — no production recorded</div>',
                    unsafe_allow_html=True,
                )
                idle_df = pd.DataFrame({
                    "Date": [d.strftime("%d-%b-%Y") for d in idle],
                    "Day":  [d.strftime("%A")       for d in idle],
                })
                st.dataframe(idle_df, use_container_width=True, hide_index=True)
            else:
                st.success("✅ No idle days in selected period — production recorded every day.")

        if not df_disp_all.empty and "trip_distance" in df_disp_all.columns:
            st.markdown("---")
            st.markdown('<div class="section-header">Truck Diesel Cost (4 km/L · ₹100/L)</div>', unsafe_allow_html=True)
            COST_PER_KM = 100 / 4  # ₹25/km

            trk_cost = df_disp_all.groupby("truck_no").agg(
                Trips     =("id",            "count"),
                Total_km  =("trip_distance", "sum"),
                Disp_Value=("dispatch_value","sum"),
            ).reset_index()
            trk_cost["Diesel Cost (₹)"] = (trk_cost["Total_km"] * COST_PER_KM).round(0).astype(int)
            trk_cost["Cost/Trip (₹)"]   = (trk_cost["Diesel Cost (₹)"] / trk_cost["Trips"]).round(0).astype(int)
            trk_cost["Disp Value (L)"]  = (trk_cost["Disp_Value"] / LAKH).round(2)
            trk_cost["Diesel % of Rev"] = ((trk_cost["Diesel Cost (₹)"] / trk_cost["Disp_Value"]) * 100).round(1)

            tc1, tc2 = st.columns(2)
            with tc1:
                st.dataframe(
                    trk_cost[["truck_no","Trips","Total_km","Diesel Cost (₹)","Cost/Trip (₹)","Disp Value (L)","Diesel % of Rev"]]
                    .rename(columns={"truck_no":"Truck","Total_km":"Total KM"})
                    .sort_values("Diesel Cost (₹)", ascending=False),
                    use_container_width=True, hide_index=True,
                )
            with tc2:
                srt = trk_cost.sort_values("Diesel Cost (₹)", ascending=False)
                fig_trk = go.Figure(go.Bar(
                    x=srt["truck_no"],
                    y=srt["Diesel Cost (₹)"],
                    marker_color="#D4A011",
                    text=srt["Diesel Cost (₹)"].apply(lambda v: f"₹{v:,.0f}"),
                    textposition="outside",
                ))
                fig_trk.update_layout(**PLOT, height=300, yaxis_title="₹", showlegend=False)
                st.plotly_chart(fig_trk, use_container_width=True)
