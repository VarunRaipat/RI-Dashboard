import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, timedelta
from core.db import get_production, get_dispatch, get_orders
from core.config import RAW_MATERIALS, HUME_PIPE_PRODUCTS, SKU_TO_PRICING_KEY, PRODUCT_CONFIG, parse_pipe_sku
from core.calculations import daily_fixed_costs

LAKH = 100_000

# ── Shared chart palette ────────────────────────────────────────────────────
# One consistent set of meanings used across every chart on this page, so
# color always means the same thing: blue = neutral value/volume, green =
# healthy profit, gold = thin margin, red = loss/danger. QUAL_COLORS is for
# genuinely-categorical breakdowns (clients, payment modes, ...) capped at
# 8 distinct hues + OTHER_COLOR for the "everything else" bucket.
ACCENT       = "#246A8B"   # brand blue — neutral value/volume
ACCENT_OTHER = "#14B8A6"   # teal — "Other Precast Products" category accent
GOOD         = "#27AE60"
WARN         = "#D4A011"
BAD          = "#E05252"
QUAL_COLORS  = ["#246A8B", "#27AE60", "#D4A011", "#A78BFA", "#22D3EE", "#E879F9", "#F97316", "#14B8A6"]
OTHER_COLOR  = "#64748B"


def _top_n_others(df, group_col, value_col, n=8, other_label="Other"):
    """Collapse a long-tail categorical breakdown to the top n rows by
    value_col plus one 'Other' row summing the rest. Hume Pipes alone have
    30+ diameter/class/joint SKUs — grouping a pie or legend by raw SKU
    turns unreadable past ~8 slices, so every chart that breaks down by
    product/client/etc. should go through this first."""
    if df.empty:
        return pd.DataFrame(columns=[group_col, value_col])
    s = df.groupby(group_col)[value_col].sum().sort_values(ascending=False)
    if len(s) <= n:
        return s.reset_index()
    top = s.head(n)
    other_sum = s.iloc[n:].sum()
    out = top.reset_index()
    if other_sum > 0:
        out = pd.concat(
            [out, pd.DataFrame({group_col: [other_label], value_col: [other_sum]})],
            ignore_index=True,
        )
    return out


def _profit_tier_color(pct):
    return GOOD if pct >= 25 else WARN if pct >= 10 else BAD


def _render_production_section(df_prod, df_disp, label, accent, PLOT):
    """Renders the Production & Financial Summary KPIs, Production Overview,
    Monthly Trends, and Cost Analysis for a given (already product-filtered)
    slice of production/dispatch data. Called once per product category
    (Pipes vs. everything else) so their profit/cost numbers are never
    blended together. Meant to be called inside its own tab — the caller's
    tab label already identifies the category, so this only renders a plain
    subheader, not a repeated colored banner."""
    if df_prod.empty:
        st.info(f"No {label} production data for the selected period.")
        return

    total_nos      = df_prod["nos"].sum()
    total_revenue  = df_prod["revenue"].sum()
    total_cost     = df_prod["total_cost"].sum()
    total_profit   = df_prod["profit"].sum()
    avg_profit_pct = (total_profit / total_revenue * 100) if total_revenue else 0
    total_dispatch = df_disp["dispatch_value"].sum() if not df_disp.empty else 0

    f1, f2, f3, f4, f5 = st.columns(5)
    f1.metric("Production Value", f"₹{total_revenue/LAKH:.2f}L")
    f2.metric("Total Cost",       f"₹{total_cost/LAKH:.2f}L")
    f3.metric("Profit",           f"₹{total_profit/LAKH:.2f}L")
    f4.metric("Avg Profit %",     f"{avg_profit_pct:.1f}%")
    f5.metric("Dispatch Value",   f"₹{total_dispatch/LAKH:.2f}L")

    prod_nos = (df_prod.groupby("product")["nos"].sum()
                .reset_index().sort_values("nos", ascending=False))
    st.caption(f"**{total_nos:,.0f} nos** across {len(prod_nos)} product(s) — "
               + ", ".join(f"{r['product']}: {int(r['nos']):,}" for _, r in prod_nos.head(6).iterrows())
               + (" …" if len(prod_nos) > 6 else ""))

    st.markdown("---")

    df_prod = df_prod.copy()
    df_prod["date"] = pd.to_datetime(df_prod["date"])

    with st.expander("📈 Production Overview", expanded=True):
        col1, col2 = st.columns(2)

        with col1:
            st.markdown('<div class="section-header">Daily Production</div>', unsafe_allow_html=True)
            daily_tot = df_prod.groupby("date").agg(
                nos=("nos", "sum"), revenue=("revenue", "sum"), profit=("profit", "sum"),
            ).reset_index()
            daily_tot["profit_pct"] = daily_tot.apply(
                lambda r: (r["profit"] / r["revenue"] * 100) if r["revenue"] else 0, axis=1
            )
            fig = go.Figure(go.Bar(
                x=daily_tot["date"], y=daily_tot["nos"],
                marker_color=[_profit_tier_color(p) for p in daily_tot["profit_pct"]],
                customdata=daily_tot["profit_pct"].round(1),
                hovertemplate="%{x|%d %b %Y}<br>%{y:,.0f} nos · %{customdata}% profit<extra></extra>",
            ))
            fig.update_layout(**PLOT, height=320, yaxis_title="Nos.")
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Bar color = that day's profit %: 🟢 ≥25% · 🟡 ≥10% · 🔴 below 10%")

        with col2:
            st.markdown('<div class="section-header">Production Mix by Value</div>', unsafe_allow_html=True)
            mix = _top_n_others(df_prod, "product", "revenue", n=8)
            colors = (QUAL_COLORS + [OTHER_COLOR])[:len(mix)]
            fig = go.Figure(go.Pie(
                labels=mix["product"], values=mix["revenue"], hole=0.42,
                textinfo="percent", marker_colors=colors,
            ))
            fig.update_layout(
                **PLOT, height=320, showlegend=True,
                legend=dict(orientation="v", x=1.0, y=0.5, font=dict(size=10)),
            )
            st.plotly_chart(fig, use_container_width=True)
            if len(df_prod["product"].unique()) > 8:
                st.caption(f"Top 8 of {df_prod['product'].unique().size} products shown — rest grouped as \"Other\".")

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
            summ = summ.sort_values("Revenue", ascending=False)

        money_cols = ["Revenue","RM_Cost","Production","Loading","Power","Welding","Jalli","EMI","Admin","Misc","Total_Cost","Profit"]
        for mc in money_cols:
            if mc in summ.columns:
                summ[mc] = (summ[mc] / LAKH).round(3)
        if "Avg_Profit_Pct" in summ.columns:
            summ["Avg_Profit_Pct"] = summ["Avg_Profit_Pct"].round(1)

        rename_map = {
            "product":"Product","Days":"Days","Total_Nos":"Nos.",
            "Revenue":"Prod Value(L)","RM_Cost":"RM(L)","Production":"Production(L)",
            "Loading":"Loading/Unload(L)","Power":"Power(L)","Welding":"Welding(L)","Jalli":"Jalli(L)",
            "EMI":"EMI(L)","Admin":"Admin(L)","Misc":"Misc(L)",
            "Total_Cost":"Total Cost(L)","Profit":"Profit(L)","Avg_Profit_Pct":"Profit%",
        }
        summ = summ.rename(columns={k: v for k, v in rename_map.items() if k in summ.columns})
        st.dataframe(summ, use_container_width=True, hide_index=True)

    with st.expander("📅 Monthly Trends", expanded=False):
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
            Admin      =("admin_cost",  "sum"),
            Misc       =("misc_cost",   "sum"),
            Total_Cost =("total_cost",  "sum"),
            Profit     =("profit",      "sum"),
            Days       =("date",        "nunique"),
        ).reset_index().rename(columns={"date": "month"}).sort_values("month")
        m_all["Profit_Pct"] = m_all.apply(
            lambda r: (r["Profit"] / r["Revenue"] * 100) if r["Revenue"] else 0, axis=1
        )

        st.markdown('<div class="section-header">Monthly Production Value vs Profit</div>', unsafe_allow_html=True)
        fig_mrev = go.Figure()
        fig_mrev.add_trace(go.Bar(
            x=m_all["month"].dt.strftime("%b %Y"),
            y=(m_all["Revenue"] / LAKH).round(2),
            marker_color=accent,
            text=(m_all["Revenue"] / LAKH).round(2).astype(str) + "L",
            textposition="outside",
            name="Production Value",
        ))
        fig_mrev.add_trace(go.Scatter(
            x=m_all["month"].dt.strftime("%b %Y"),
            y=(m_all["Profit"] / LAKH).round(2),
            mode="lines+markers+text",
            name="Profit",
            line=dict(color=WARN, width=2),
            marker=dict(size=7),
            text=(m_all["Profit"] / LAKH).round(2).astype(str) + "L",
            textposition="top center",
            yaxis="y2",
        ))
        fig_mrev.update_layout(
            **PLOT, height=360,
            yaxis=dict(title=dict(text="Prod Value (L)", font=dict(color=accent))),
            yaxis2=dict(title=dict(text="Profit (L)", font=dict(color=WARN)),
                        overlaying="y", side="right"),
            legend=dict(orientation="h", y=1.08),
            barmode="group",
        )
        st.plotly_chart(fig_mrev, use_container_width=True)

        st.markdown('<div class="section-header">Monthly Profit by Product (L)</div>', unsafe_allow_html=True)
        top_by_profit = _top_n_others(df_all, "product", "profit", n=7)
        keep_products = [p for p in top_by_profit["product"] if p != "Other"]
        m_prod = df_all.copy()
        m_prod["product_grp"] = m_prod["product"].where(m_prod["product"].isin(keep_products), "Other")
        m_prod = m_prod.groupby([m_prod["date"].dt.to_period("M").dt.to_timestamp(), "product_grp"])["profit"].sum().reset_index()
        m_prod = m_prod.rename(columns={"date": "month", "product_grp": "product"})
        m_prod["month_str"] = m_prod["month"].dt.strftime("%b %Y")
        m_prod["profit_L"]  = (m_prod["profit"] / LAKH).round(3)
        months_ordered = sorted(m_prod["month"].unique())
        month_labels   = [pd.Timestamp(m).strftime("%b %Y") for m in months_ordered]
        products       = keep_products + (["Other"] if "Other" in m_prod["product"].values else [])
        fig_mprod = go.Figure()
        for i, prod in enumerate(products):
            sub = m_prod[m_prod["product"] == prod][["month_str","profit_L"]]
            sub = sub.set_index("month_str").reindex(month_labels, fill_value=0).reset_index()
            fig_mprod.add_trace(go.Bar(
                x=sub["month_str"],
                y=sub["profit_L"],
                name=prod,
                marker_color=OTHER_COLOR if prod == "Other" else QUAL_COLORS[i % len(QUAL_COLORS)],
                text=sub["profit_L"].apply(lambda v: f"{v:.2f}L" if v != 0 else ""),
                textposition="inside",
            ))
        fig_mprod.update_layout(
            **PLOT, height=380, barmode="stack",
            yaxis_title="Profit (L)",
            legend=dict(orientation="h", y=1.08, font=dict(size=10)),
        )
        st.plotly_chart(fig_mprod, use_container_width=True)
        if len(df_all["product"].unique()) > 7:
            st.caption(f"Top 7 of {df_all['product'].unique().size} products by profit shown — rest grouped as \"Other\".")

        st.markdown('<div class="section-header">Monthly Breakup — All Months</div>', unsafe_allow_html=True)
        tbl = m_all.copy()
        tbl["Month"] = tbl["month"].dt.strftime("%b %Y")
        for mc in ["Revenue","RM","Production","Loading","Power","Welding","Jalli","EMI","Admin","Misc","Total_Cost","Profit"]:
            if mc in tbl.columns:
                tbl[mc] = (tbl[mc] / LAKH).round(3)
        tbl["Profit_Pct"] = tbl["Profit_Pct"].round(1)
        tbl["Nos"] = tbl["Nos"].astype(int)
        tbl = tbl.rename(columns={
            "Month":"Month","Days":"Days","Nos":"Nos.",
            "Revenue":"Prod Value(L)","RM":"RM(L)","Production":"Production(L)",
            "Loading":"Loading/Unload(L)","Power":"Power(L)","Welding":"Welding(L)","Jalli":"Jalli(L)",
            "EMI":"EMI(L)","Admin":"Admin(L)","Misc":"Misc(L)",
            "Total_Cost":"Total Cost(L)","Profit":"Profit(L)","Profit_Pct":"Profit%",
        })
        display_cols = ["Month","Days","Nos.","Prod Value(L)","RM(L)","Production(L)","Loading/Unload(L)",
                        "Power(L)","Welding(L)","Jalli(L)","EMI(L)",
                        "Admin(L)","Misc(L)","Total Cost(L)","Profit(L)","Profit%"]
        display_cols = [c for c in display_cols if c in tbl.columns]
        st.dataframe(tbl[display_cols], use_container_width=True, hide_index=True)

    with st.expander("💰 Cost Analysis", expanded=False):
        col3, col4 = st.columns(2)
        with col3:
            st.markdown('<div class="section-header">Cost Breakdown (Period)</div>', unsafe_allow_html=True)
            cost_labels = ["Raw Material","Production","Loading/Unload","Power","Welding","Jalli","EMI","Admin","Misc"]
            cost_vals   = [
                df_prod["rm_cost"].sum(),
                df_prod["production_cost"].sum() if "production_cost" in df_prod.columns else 0,
                df_prod["loading_unloading_cost"].sum() if "loading_unloading_cost" in df_prod.columns else 0,
                df_prod["power_cost"].sum(),
                df_prod["welding_cost"].sum() if "welding_cost" in df_prod.columns else 0,
                df_prod["jalli_cost"].sum() if "jalli_cost" in df_prod.columns else 0,
                df_prod["emi_cost"].sum()   if "emi_cost"   in df_prod.columns else 0,
                df_prod["admin_cost"].sum() if "admin_cost" in df_prod.columns else 0,
                df_prod["misc_cost"].sum()  if "misc_cost"  in df_prod.columns else 0,
            ]
            fig3 = go.Figure(go.Pie(
                labels=cost_labels, values=cost_vals, hole=0.42,
                textinfo="label+percent",
                marker_colors=QUAL_COLORS + [OTHER_COLOR],
            ))
            fig3.update_layout(**PLOT, height=300, showlegend=False)
            st.plotly_chart(fig3, use_container_width=True)

        with col4:
            st.markdown('<div class="section-header">Avg Profit % by Product</div>', unsafe_allow_html=True)
            pp_all = df_prod.groupby("product").agg(revenue=("revenue","sum"), profit=("profit","sum")).reset_index()
            pp_all["profit_pct"] = pp_all.apply(
                lambda r: (r["profit"] / r["revenue"] * 100) if r["revenue"] else 0, axis=1
            )
            pp = pp_all.sort_values("revenue", ascending=False).head(15).sort_values("profit_pct").reset_index(drop=True)
            fig4 = go.Figure(go.Bar(
                x=pp["profit_pct"], y=pp["product"], orientation="h",
                marker_color=[_profit_tier_color(v) for v in pp["profit_pct"]],
                text=[f"{v:.1f}%" for v in pp["profit_pct"]],
                textposition="outside",
            ))
            fig4.update_layout(**PLOT, height=max(300, 26 * len(pp)), xaxis_title="%")
            st.plotly_chart(fig4, use_container_width=True)
            if len(pp_all) > 15:
                st.caption(f"Top 15 of {len(pp_all)} products by revenue shown.")

        st.markdown('<div class="section-header">Raw Material Usage — Produced vs Dispatched</div>', unsafe_allow_html=True)
        rm_cols  = [f"{m['key']}_qty" for m in RAW_MATERIALS]
        rm_labels = {f"{m['key']}_qty": f"{m['label']} ({m['unit']})" for m in RAW_MATERIALS}
        rm_avail = [c for c in rm_cols if c in df_prod.columns]
        if rm_avail:
            rm_df = df_prod[rm_avail].sum().reset_index()
            rm_df.columns = ["Material","Produced"]
            rm_df["Material"] = rm_df["Material"].map(rm_labels)
            rm_df["Produced"] = rm_df["Produced"].round(1)

            # Dispatch side has no stored m³/Kg — derive it from qty_dispatched x
            # each product's fixed per-unit figure (same source production uses).
            dispatched_totals = {}
            if df_disp is not None and not df_disp.empty:
                for m in RAW_MATERIALS:
                    field = m["product_field"]
                    per_unit = df_disp["product"].map(
                        lambda p: PRODUCT_CONFIG.get(SKU_TO_PRICING_KEY.get(p, p), {}).get(field, 0)
                    )
                    dispatched_totals[f"{m['key']}_qty"] = (df_disp["qty_dispatched"] * per_unit).sum()

            rm_df["Dispatched"] = [round(dispatched_totals.get(c, 0), 1) for c in rm_avail]
            st.dataframe(rm_df, use_container_width=True, hide_index=True)


def _tag_pipe_skus(df):
    """Attach Diameter/Class/Joint columns parsed from each row's SKU (see
    core.config.parse_pipe_sku). df is expected to already be pipe-only
    (see HUME_PIPE_PRODUCTS filtering in show()); any row whose SKU still
    doesn't parse is dropped rather than crashing the chart."""
    if df is None or df.empty:
        return pd.DataFrame()
    parsed = df["product"].map(parse_pipe_sku)
    out = df[parsed.notna()].copy()
    if out.empty:
        return out
    out["Diameter"] = parsed[parsed.notna()].map(lambda t: t[0])
    out["Class"]    = parsed[parsed.notna()].map(lambda t: t[1])
    out["Joint"]    = parsed[parsed.notna()].map(lambda t: t[2])
    return out


def _agg_or_empty(df, group_cols, agg_map):
    if df.empty:
        return pd.DataFrame(columns=group_cols + list(agg_map.keys()))
    return df.groupby(group_cols).agg(**agg_map).reset_index()


def _render_pipe_demand_section(df_prod_pipe, df_disp_pipe, df_ord_pipe, PLOT):
    """m³ Produced vs Dispatched, and demand (Qty Ordered) broken down by
    pipe Diameter/Class/Joint Type — answers "which size/class/joint is
    selling" rather than just overall pipe revenue. Diameter/Class/Joint are
    all inherently small, fixed vocabularies (10/2-3/3 values), so none of
    these charts need top-N collapsing."""
    prod_t = _tag_pipe_skus(df_prod_pipe)
    disp_t = _tag_pipe_skus(df_disp_pipe)
    ord_t  = _tag_pipe_skus(df_ord_pipe)

    if prod_t.empty and disp_t.empty and ord_t.empty:
        st.info("No pipe production, dispatch, or order data for the selected period.")
        return

    # Dispatch/Orders don't store m³ — derive it the same way the Raw
    # Material Usage table above does: qty x that SKU's concrete_volume_m3.
    if not disp_t.empty:
        per_unit = disp_t["product"].map(
            lambda p: PRODUCT_CONFIG.get(SKU_TO_PRICING_KEY.get(p, p), {}).get("concrete_volume_m3", 0)
        )
        disp_t["concrete_m3"] = disp_t["qty_dispatched"] * per_unit

    total_produced_m3   = prod_t["concrete_qty"].sum() if "concrete_qty" in prod_t.columns else 0
    total_dispatched_m3 = disp_t["concrete_m3"].sum()  if "concrete_m3"  in disp_t.columns else 0
    total_dispatched_nos = disp_t["qty_dispatched"].sum() if "qty_dispatched" in disp_t.columns else 0
    total_ordered_nos   = ord_t["qty_ordered"].sum()   if "qty_ordered"  in ord_t.columns  else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("m³ Produced",             f"{total_produced_m3:,.1f} m³")
    k2.metric("m³ Dispatched",           f"{total_dispatched_m3:,.1f} m³")
    k3.metric("Nos. Dispatched (Demand)", f"{total_dispatched_nos:,.0f}")
    k4.metric("Nos. Ordered",            f"{total_ordered_nos:,.0f}")
    if total_ordered_nos == 0:
        st.caption("Demand below is based on dispatched quantity — the Sales Orders module has no rows yet, "
                   "so ordered-quantity demand will appear here automatically once orders start getting logged.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**m³ Produced vs Dispatched by Diameter**")
        p_by_d = prod_t.groupby("Diameter")["concrete_qty"].sum() if "concrete_qty" in prod_t.columns else pd.Series(dtype=float)
        d_by_d = disp_t.groupby("Diameter")["concrete_m3"].sum()  if "concrete_m3"  in disp_t.columns else pd.Series(dtype=float)
        diam_idx = sorted(set(p_by_d.index) | set(d_by_d.index))
        if diam_idx:
            fig = go.Figure()
            fig.add_trace(go.Bar(name="Produced", x=[f"{d}mm" for d in diam_idx],
                                  y=[round(p_by_d.get(d, 0), 2) for d in diam_idx], marker_color=ACCENT))
            fig.add_trace(go.Bar(name="Dispatched", x=[f"{d}mm" for d in diam_idx],
                                  y=[round(d_by_d.get(d, 0), 2) for d in diam_idx], marker_color=GOOD))
            fig.update_layout(**PLOT, height=320, barmode="group", yaxis_title="m³",
                               legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No data.")

    # Demand runs off whichever of Orders/Dispatch actually has data for this
    # SKU — Orders is the truer "customer wants this" signal, but the Sales
    # Orders module isn't in regular use yet, so Dispatch (what's actually
    # left the factory) is what's populated today.
    demand_df, demand_col, demand_label = (
        (ord_t, "qty_ordered", "Qty Ordered") if total_ordered_nos > 0
        else (disp_t, "qty_dispatched", "Qty Dispatched")
    )

    with c2:
        st.markdown(f"**Demand ({demand_label}) by Diameter**")
        if demand_col in demand_df.columns and not demand_df.empty:
            dem_d = demand_df.groupby("Diameter")[demand_col].sum().sort_values(ascending=False)
            fig2 = go.Figure(go.Bar(
                x=[f"{d}mm" for d in dem_d.index], y=dem_d.values,
                marker_color=WARN, text=dem_d.values.astype(int), textposition="outside",
            ))
            fig2.update_layout(**PLOT, height=320, yaxis_title=demand_label)
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.caption("No dispatch or order data for pipes in this period.")

    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**Demand by Class**")
        if demand_col in demand_df.columns and not demand_df.empty:
            dem_c = demand_df.groupby("Class")[demand_col].sum()
            fig3 = go.Figure(go.Pie(labels=dem_c.index, values=dem_c.values, hole=0.45,
                                     marker_colors=QUAL_COLORS))
            fig3.update_layout(**PLOT, height=280)
            st.plotly_chart(fig3, use_container_width=True)
        else:
            st.caption("No dispatch or order data for pipes in this period.")

    with c4:
        st.markdown("**Demand by Joint Type**")
        if demand_col in demand_df.columns and not demand_df.empty:
            dem_j = demand_df.groupby("Joint")[demand_col].sum()
            fig4 = go.Figure(go.Pie(labels=dem_j.index, values=dem_j.values, hole=0.45,
                                     marker_colors=QUAL_COLORS[2:]))
            fig4.update_layout(**PLOT, height=280)
            st.plotly_chart(fig4, use_container_width=True)
        else:
            st.caption("No dispatch or order data for pipes in this period.")

    st.markdown("**Monthly Trend — m³ Produced vs Dispatched**")
    prod_m, disp_m = pd.Series(dtype=float), pd.Series(dtype=float)
    prod_month, disp_month = None, None
    if not prod_t.empty:
        prod_month = pd.to_datetime(prod_t["date"]).dt.to_period("M").dt.to_timestamp()
        if "concrete_qty" in prod_t.columns:
            prod_m = prod_t.groupby(prod_month)["concrete_qty"].sum()
    if not disp_t.empty:
        disp_month = pd.to_datetime(disp_t["date"]).dt.to_period("M").dt.to_timestamp()
        if "concrete_m3" in disp_t.columns:
            disp_m = disp_t.groupby(disp_month)["concrete_m3"].sum()
    months = sorted(set(prod_m.index) | set(disp_m.index))
    if months:
        month_labels = [m.strftime("%b %Y") for m in months]
        fig_m = go.Figure()
        fig_m.add_trace(go.Bar(name="Produced", x=month_labels,
                                y=[round(prod_m.get(m, 0), 2) for m in months], marker_color=ACCENT))
        fig_m.add_trace(go.Bar(name="Dispatched", x=month_labels,
                                y=[round(disp_m.get(m, 0), 2) for m in months], marker_color=GOOD))
        fig_m.update_layout(**PLOT, height=320, barmode="group", yaxis_title="m³",
                             legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig_m, use_container_width=True)

        month_tbl = pd.DataFrame({"Month": month_labels}, index=months)
        month_tbl["m³ Produced"]   = [round(prod_m.get(m, 0), 1) for m in months]
        month_tbl["m³ Dispatched"] = [round(disp_m.get(m, 0), 1) for m in months]
        if "nos" in prod_t.columns and not prod_t.empty:
            month_tbl["Nos Produced"] = [int(prod_t.groupby(prod_month)["nos"].sum().get(m, 0)) for m in months]
        if "qty_dispatched" in disp_t.columns and not disp_t.empty:
            month_tbl["Nos Dispatched"] = [int(disp_t.groupby(disp_month)["qty_dispatched"].sum().get(m, 0)) for m in months]
        st.dataframe(month_tbl.reset_index(drop=True), use_container_width=True, hide_index=True)
    else:
        st.caption("No data.")

    with st.expander("Full Breakdown — Diameter x Class x Joint", expanded=False):
        group_cols = ["Diameter", "Class", "Joint"]
        prod_g = _agg_or_empty(prod_t, group_cols, dict(Nos_Produced=("nos", "sum"), M3_Produced=("concrete_qty", "sum")))
        disp_g = _agg_or_empty(disp_t, group_cols, dict(Nos_Dispatched=("qty_dispatched", "sum"), M3_Dispatched=("concrete_m3", "sum")))
        ord_g  = _agg_or_empty(ord_t,  group_cols, dict(Nos_Ordered=("qty_ordered", "sum")))

        breakdown = prod_g.merge(disp_g, on=group_cols, how="outer").merge(ord_g, on=group_cols, how="outer")
        if not breakdown.empty:
            breakdown = breakdown.fillna(0).sort_values(group_cols)
            for c in ["Nos_Produced", "M3_Produced", "Nos_Dispatched", "M3_Dispatched", "Nos_Ordered"]:
                if c in breakdown.columns:
                    breakdown[c] = breakdown[c].round(1)
            breakdown["Diameter"] = breakdown["Diameter"].astype(int).astype(str) + "mm"
            breakdown = breakdown.rename(columns={
                "Nos_Produced": "Nos Produced", "M3_Produced": "m³ Produced",
                "Nos_Dispatched": "Nos Dispatched", "M3_Dispatched": "m³ Dispatched",
                "Nos_Ordered": "Nos Ordered (Demand)",
            })
            st.dataframe(breakdown, use_container_width=True, hide_index=True)
        else:
            st.caption("No data.")


def _render_dispatch_sales_tab(df_disp, PLOT):
    if df_disp.empty:
        st.info("No dispatch data for selected period.")
        return

    df_disp = df_disp.copy()
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
        top10_cl = _top_n_others(df_disp, "client_name", "dispatch_value", n=10)
        if "Other" in top10_cl["client_name"].values:
            colors = (QUAL_COLORS * 2)[:len(top10_cl) - 1] + [OTHER_COLOR]
        else:
            colors = (QUAL_COLORS * 2)[:len(top10_cl)]
        fig5 = go.Figure(go.Pie(
            labels=top10_cl["client_name"], values=top10_cl["dispatch_value"],
            hole=0.4, textinfo="percent",
            hovertemplate="%{label}<br>₹%{value:,.0f}<br>%{percent}<extra></extra>",
            marker_colors=colors,
        ))
        fig5.update_layout(**PLOT, height=340, title="Top 10 Clients — Dispatch Value",
                           showlegend=True, legend=dict(orientation="v", x=1.02, y=0.5, font=dict(size=10)))
        st.plotly_chart(fig5, use_container_width=True)

    with col6:
        prod_disp = _top_n_others(df_disp, "product", "dispatch_value", n=10)
        prod_disp["Value (L)"] = (prod_disp["dispatch_value"] / LAKH).round(2)
        colors = [OTHER_COLOR if p == "Other" else QUAL_COLORS[i % len(QUAL_COLORS)] for i, p in enumerate(prod_disp["product"])]
        fig6 = go.Figure(go.Bar(
            x=prod_disp["product"], y=prod_disp["Value (L)"],
            marker_color=colors,
            text=prod_disp["Value (L)"].astype(str) + "L", textposition="outside",
        ))
        fig6.update_layout(**PLOT, height=340, title="Billed Value by Product (L)",
                           showlegend=False, yaxis_title="Value (L)")
        st.plotly_chart(fig6, use_container_width=True)
        if len(df_disp["product"].unique()) > 10:
            st.caption(f"Top 10 of {df_disp['product'].unique().size} products by value shown.")

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


def _render_sales_orders_tab(df_disp, start, end, PLOT):
    df_ord = get_orders()
    if df_ord.empty:
        st.info("No sales orders yet.")
        return

    df_ord["order_date"] = pd.to_datetime(df_ord["order_date"], errors="coerce")

    if not df_disp.empty and "di_no" in df_disp.columns:
        disp_di = df_disp.groupby("di_no").agg(
            dispatched_value=("dispatch_value","sum"),
            dispatched_qty  =("qty_dispatched","sum"),
        ).reset_index()
    else:
        disp_di = pd.DataFrame(columns=["di_no","dispatched_value","dispatched_qty"])

    _ord_agg = dict(
        order_date   =("order_date",   "first"),
        client_name  =("client_name",  "first"),
        products     =("product",       lambda x: ", ".join(x.dropna().unique())),
        total_ordered=("total_amount", "sum"),
        qty_ordered  =("qty_ordered",  "sum"),
    )
    if "gst_amount" in df_ord.columns:
        _ord_agg["gst_amount"] = ("gst_amount", "sum")
    di_sum = df_ord.groupby("di_no").agg(**_ord_agg).reset_index()

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

    has_gst = "gst_amount" in di_sum.columns
    s_cols = st.columns(5 if has_gst else 4)
    s_cols[0].metric("Total DIs",        f"{di_sum['di_no'].nunique()}")
    s_cols[1].metric("Total Order Value", f"₹{di_sum['total_ordered'].sum()/LAKH:.2f}L")
    s_cols[2].metric("Dispatched Value",  f"₹{di_sum['dispatched_value'].sum()/LAKH:.2f}L")
    s_cols[3].metric("Pending Value",     f"₹{di_sum['pending_value'].sum()/LAKH:.2f}L")
    if has_gst:
        s_cols[4].metric("Total GST",     f"₹{di_sum['gst_amount'].sum()/LAKH:.2f}L")

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
        status_color = {"🔴 Pending": BAD, "🟡 Partial": WARN, "🟢 Fulfilled": GOOD}
        fig_s = go.Figure(go.Bar(
            x=status_counts["Status"], y=status_counts["Count"],
            marker_color=[status_color.get(s, OTHER_COLOR) for s in status_counts["Status"]],
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
        fig_c.add_trace(go.Bar(name="Dispatched", x=top_clients["client_name"], y=(top_clients["Dispatched"]/LAKH).round(2), marker_color=GOOD))
        fig_c.update_layout(**PLOT, height=260, barmode="group", yaxis_title="Value (L)", legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig_c, use_container_width=True)

    with st.expander("📋 DI Pipeline (full table)", expanded=False):
        tbl = di_sum.sort_values("order_date", ascending=False).copy()
        tbl["order_date"] = tbl["order_date"].dt.strftime("%d-%b-%Y")
        money_cols = ["total_ordered","dispatched_value","pending_value"] + (["gst_amount"] if has_gst else [])
        for mc in money_cols:
            tbl[mc] = (tbl[mc] / LAKH).round(3)
        tbl = tbl.rename(columns={
            "di_no":"DI No.","order_date":"Date","client_name":"Client",
            "products":"Products","Status":"Status",
            "total_ordered":"Order (L)","dispatched_value":"Dispatched (L)","pending_value":"Pending (L)",
            "gst_amount":"GST (L)",
        })
        di_pipeline_cols = ["DI No.","Date","Client","Products","Status","Order (L)"] + \
            (["GST (L)"] if has_gst else []) + ["Dispatched (L)","Pending (L)"]
        st.dataframe(
            tbl[di_pipeline_cols],
            use_container_width=True, hide_index=True,
        )

    if "client_type" in df_ord.columns:
        st.markdown("---")
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
                marker_colors=QUAL_COLORS,
            ))
            fig_ctype.update_layout(**PLOT, height=280, showlegend=False)
            st.plotly_chart(fig_ctype, use_container_width=True)
        with ct2:
            st.dataframe(
                type_mix[["client_type","Value (L)"]].rename(columns={"client_type":"Client Type"})
                .sort_values("Value (L)", ascending=False),
                use_container_width=True, hide_index=True,
            )

    if "mode_of_payment" in df_ord.columns:
        st.markdown("---")
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
                marker_colors=QUAL_COLORS,
            ))
            fig_pay.update_layout(**PLOT, height=280, showlegend=False)
            st.plotly_chart(fig_pay, use_container_width=True)
        with pm2:
            st.dataframe(
                pay_mix[["mode_of_payment","Value (L)"]].rename(columns={"mode_of_payment":"Payment Mode"})
                .sort_values("Value (L)", ascending=False),
                use_container_width=True, hide_index=True,
            )

    with st.expander("🔁 Repeat Clients & Avg Order Size", expanded=False):
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


def _render_advanced_analytics_tab(df_prod, df_disp, start, end, PLOT):
    """Uses the same date-filtered df_prod/df_disp as the rest of the
    dashboard (previously this pulled fresh all-time data via get_production()
    with no args, so this tab silently ignored the date filter everyone else
    respects — showing different totals here than everywhere else)."""
    df_prod = df_prod.copy()
    df_disp = df_disp.copy() if not df_disp.empty else df_disp
    if not df_prod.empty:
        df_prod["date"] = pd.to_datetime(df_prod["date"], errors="coerce")
    if not df_disp.empty:
        df_disp["date"] = pd.to_datetime(df_disp["date"], errors="coerce")

    if not df_prod.empty and "profit_pct" in df_prod.columns:
        st.markdown('<div class="section-header">Profit % Trend by Product (Monthly)</div>', unsafe_allow_html=True)
        df_prod["month"] = df_prod["date"].dt.to_period("M").dt.to_timestamp()
        top_by_rev = _top_n_others(df_prod, "product", "revenue", n=8)
        keep = [p for p in top_by_rev["product"] if p != "Other"]
        trend = df_prod[df_prod["product"].isin(keep)].groupby(["month","product"]).agg(
            revenue=("revenue", "sum"), profit=("profit", "sum")
        ).reset_index()
        trend["profit_pct"] = trend.apply(
            lambda r: (r["profit"] / r["revenue"] * 100) if r["revenue"] else 0, axis=1
        )
        trend["month_str"] = trend["month"].dt.strftime("%b %Y")
        fig_trend = go.Figure()
        for i, prod in enumerate(sorted(trend["product"].unique())):
            sub = trend[trend["product"] == prod].sort_values("month")
            fig_trend.add_trace(go.Scatter(
                x=sub["month_str"], y=sub["profit_pct"].round(1),
                mode="lines+markers",
                name=prod,
                line=dict(color=QUAL_COLORS[i % len(QUAL_COLORS)], width=2),
                marker=dict(size=7),
            ))
        fig_trend.add_hline(y=10, line_dash="dash", line_color=BAD,
                            annotation_text="10% threshold", annotation_position="bottom right")
        fig_trend.update_layout(**PLOT, height=340, yaxis_title="Profit %",
                                legend=dict(orientation="h", y=-0.3, font=dict(size=10)))
        st.plotly_chart(fig_trend, use_container_width=True)
        if df_prod["product"].unique().size > 8:
            st.caption(f"Top 8 of {df_prod['product'].unique().size} products by revenue shown.")

    if not df_prod.empty and "profit_pct" in df_prod.columns:
        low = df_prod[df_prod["profit_pct"] < 10].copy()
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

    if not df_prod.empty and "plant" in df_prod.columns:
        st.markdown("---")
        st.markdown('<div class="section-header">Plant Performance</div>', unsafe_allow_html=True)
        plant = df_prod.groupby("plant").agg(
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
            fig_plant.add_trace(go.Bar(name="Revenue (L)", x=plant["plant"], y=plant["Revenue (L)"], marker_color=ACCENT))
            fig_plant.add_trace(go.Bar(name="Profit (L)",  x=plant["plant"], y=plant["Profit (L)"],  marker_color=GOOD))
            fig_plant.update_layout(**PLOT, height=260, barmode="group",
                                    legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig_plant, use_container_width=True)

    if not df_prod.empty:
        st.markdown("---")
        st.markdown('<div class="section-header">Idle Days (No Production)</div>', unsafe_allow_html=True)
        prod_dates = set(df_prod["date"].dt.date)
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

    if not df_disp.empty and "trip_distance" in df_disp.columns:
        st.markdown("---")
        st.markdown('<div class="section-header">Truck Diesel Cost (4 km/L · ₹100/L)</div>', unsafe_allow_html=True)
        COST_PER_KM = 100 / 4  # ₹25/km

        trk_cost = df_disp.groupby("truck_no").agg(
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
                marker_color=WARN,
                text=srt["Diesel Cost (₹)"].apply(lambda v: f"₹{v:,.0f}"),
                textposition="outside",
            ))
            fig_trk.update_layout(**PLOT, height=300, yaxis_title="₹", showlegend=False)
            st.plotly_chart(fig_trk, use_container_width=True)


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

    i1, i2, i3, i4 = st.columns(4)
    i1.metric("Finished Goods Value",  f"₹{fg_value/LAKH:.2f}L")
    i2.metric("Cement + GGBS Value",   f"₹{rm_value/LAKH:.2f}L")
    i3.metric("Total Inventory Value", f"₹{(fg_value + rm_value)/LAKH:.2f}L")
    i4.metric("Products Low/Negative", f"{low_stock_ct}",
             delta=("check stock" if low_stock_ct else "all ok"),
             delta_color=("inverse" if low_stock_ct else "off"))

    if low_stock_ct:
        st.markdown(
            f'<div class="warn-box">⚠️ <b>{low_stock_ct} product(s)</b> with negative stock — '
            f'see the Overview tab below for details</div>',
            unsafe_allow_html=True,
        )

    if df_prod.empty and df_disp.empty:
        st.warning("No data for selected period. Enter some DPR or Dispatch records first.")
        return

    # ── Factory-Wide Financial Summary ────────────────────────────────────────
    # EMI/Power/Admin are whole-factory overheads, not attributable to any one
    # product or DPR line, so they're charged exactly once per calendar day
    # that had production in this period — computed here, at the combined
    # (Pipe + Other) level, so they're never double-counted across the two
    # category tabs.
    production_days = int(df_prod["date"].nunique()) if not df_prod.empty else 0
    fixed = daily_fixed_costs(production_days)
    gross_revenue = df_prod["revenue"].sum() if not df_prod.empty else 0
    gross_variable_cost = df_prod["total_cost"].sum() if not df_prod.empty else 0
    gross_margin = gross_revenue - gross_variable_cost
    net_profit = gross_margin - fixed["total"]
    net_profit_pct = (net_profit / gross_revenue * 100) if gross_revenue else 0

    st.markdown("---")
    st.markdown('<div class="section-header">🏭 Factory-Wide Financial Summary</div>', unsafe_allow_html=True)
    n1, n2, n3, n4 = st.columns(4)
    n1.metric("Production Value", f"₹{gross_revenue/LAKH:.2f}L")
    n2.metric("Gross Margin", f"₹{gross_margin/LAKH:.2f}L",
              help="Production Value minus variable per-product costs (RM, Production, Loading/Unloading, Welding, Jalli, Misc)")
    n3.metric(f"Fixed Costs ({production_days}d × EMI+Power+Admin)", f"₹{fixed['total']/LAKH:.2f}L",
              help=f"EMI ₹{fixed['emi_cost']/LAKH:.2f}L · Power ₹{fixed['power_cost']/LAKH:.2f}L · Admin ₹{fixed['admin_cost']/LAKH:.2f}L — charged once per production day, not per product")
    n4.metric("Net Profit", f"₹{net_profit/LAKH:.2f}L", delta=f"{net_profit_pct:.1f}%")

    # -- Production financials, split by category so Pipe economics never
    # blend with Slab/Pillar/Fencing Pillar/PSC Pole economics --------------
    is_pipe = df_prod["product"].isin(HUME_PIPE_PRODUCTS) if not df_prod.empty else pd.Series(dtype=bool)
    df_prod_pipe  = df_prod[is_pipe]  if not df_prod.empty else df_prod
    df_prod_other = df_prod[~is_pipe] if not df_prod.empty else df_prod

    is_disp_pipe = df_disp["product"].isin(HUME_PIPE_PRODUCTS) if not df_disp.empty else pd.Series(dtype=bool)
    df_disp_pipe  = df_disp[is_disp_pipe]  if not df_disp.empty else df_disp
    df_disp_other = df_disp[~is_disp_pipe] if not df_disp.empty else df_disp

    df_ord_demand = get_orders()
    if not df_ord_demand.empty:
        df_ord_demand["order_date"] = pd.to_datetime(df_ord_demand["order_date"], errors="coerce")
        df_ord_demand = df_ord_demand[
            (df_ord_demand["order_date"] >= pd.Timestamp(start)) &
            (df_ord_demand["order_date"] <= pd.Timestamp(end)) &
            (df_ord_demand["product"].isin(HUME_PIPE_PRODUCTS))
        ]

    st.markdown("---")

    tabs = st.tabs([
        "🏠 Overview", "🔵 Pipe Products", "📐 Pipe Demand", "⚙️ Other Products",
        "🚚 Dispatch & Sales", "📦 Sales Orders", "🔍 Advanced Analytics",
    ])

    with tabs[0]:
        st.markdown('<div class="section-header">📅 Monthly Volumes (m³)</div>', unsafe_allow_html=True)
        mv1, mv2 = st.columns(2)
        with mv1:
            st.markdown("**Monthly Production Volume (m³)**")
            if not df_prod.empty and "concrete_qty" in df_prod.columns:
                dp = df_prod.copy()
                dp["date"] = pd.to_datetime(dp["date"])
                mp = dp.groupby(dp["date"].dt.to_period("M").dt.to_timestamp())["concrete_qty"].sum().reset_index()
                mp.columns = ["month", "m3"]
                fig_mp = go.Figure(go.Bar(
                    x=mp["month"].dt.strftime("%b %Y"), y=mp["m3"].round(2),
                    marker_color=ACCENT, text=mp["m3"].round(2), textposition="outside",
                ))
                fig_mp.update_layout(**PLOT, height=300, yaxis_title="m³ Produced")
                st.plotly_chart(fig_mp, use_container_width=True, key="ov_monthly_prod_m3")
            else:
                st.caption("No production data for selected period.")

        with mv2:
            st.markdown("**Monthly Dispatch Volume (m³)**")
            if not df_disp.empty:
                dd = df_disp.copy()
                dd["date"] = pd.to_datetime(dd["date"])
                per_unit = dd["product"].map(
                    lambda p: PRODUCT_CONFIG.get(SKU_TO_PRICING_KEY.get(p, p), {}).get("concrete_volume_m3", 0)
                )
                dd["concrete_m3"] = dd["qty_dispatched"] * per_unit
                md = dd.groupby(dd["date"].dt.to_period("M").dt.to_timestamp())["concrete_m3"].sum().reset_index()
                md.columns = ["month", "m3"]
                fig_md = go.Figure(go.Bar(
                    x=md["month"].dt.strftime("%b %Y"), y=md["m3"].round(2),
                    marker_color=GOOD, text=md["m3"].round(2), textposition="outside",
                ))
                fig_md.update_layout(**PLOT, height=300, yaxis_title="m³ Dispatched")
                st.plotly_chart(fig_md, use_container_width=True, key="ov_monthly_disp_m3")
            else:
                st.caption("No dispatch data for selected period.")

        st.markdown("---")
        st.markdown('<div class="section-header">💰 Production Mix by Value</div>', unsafe_allow_html=True)
        if not df_prod.empty:
            mix_all = _top_n_others(df_prod, "product", "revenue", n=8)
            colors_all = (QUAL_COLORS + [OTHER_COLOR])[:len(mix_all)]
            fig_mix = go.Figure(go.Pie(
                labels=mix_all["product"], values=mix_all["revenue"], hole=0.42,
                textinfo="percent", marker_colors=colors_all,
            ))
            fig_mix.update_layout(
                **PLOT, height=340, showlegend=True,
                legend=dict(orientation="v", x=1.0, y=0.5, font=dict(size=10)),
            )
            st.plotly_chart(fig_mix, use_container_width=True, key="ov_production_mix_value")
            if df_prod["product"].unique().size > 8:
                st.caption(f"Top 8 of {df_prod['product'].unique().size} products shown — rest grouped as \"Other\".")
        else:
            st.caption("No production data for selected period.")

        st.markdown("---")
        st.markdown('<div class="section-header">🏗️ m³ Produced vs Dispatched — Diameter-wise</div>', unsafe_allow_html=True)
        prod_pipe_dia = _tag_pipe_skus(df_prod_pipe)
        disp_pipe_dia = _tag_pipe_skus(df_disp_pipe)
        if not disp_pipe_dia.empty:
            per_unit_d = disp_pipe_dia["product"].map(
                lambda p: PRODUCT_CONFIG.get(SKU_TO_PRICING_KEY.get(p, p), {}).get("concrete_volume_m3", 0)
            )
            disp_pipe_dia["concrete_m3"] = disp_pipe_dia["qty_dispatched"] * per_unit_d
        p_by_d = prod_pipe_dia.groupby("Diameter")["concrete_qty"].sum() if "concrete_qty" in prod_pipe_dia.columns else pd.Series(dtype=float)
        d_by_d = disp_pipe_dia.groupby("Diameter")["concrete_m3"].sum() if "concrete_m3" in disp_pipe_dia.columns else pd.Series(dtype=float)
        diam_idx = sorted(set(p_by_d.index) | set(d_by_d.index))
        if diam_idx:
            fig_pd = go.Figure()
            fig_pd.add_trace(go.Bar(name="Produced", x=[f"{d}mm" for d in diam_idx],
                                     y=[round(p_by_d.get(d, 0), 2) for d in diam_idx], marker_color=ACCENT))
            fig_pd.add_trace(go.Bar(name="Dispatched", x=[f"{d}mm" for d in diam_idx],
                                     y=[round(d_by_d.get(d, 0), 2) for d in diam_idx], marker_color=GOOD))
            fig_pd.update_layout(**PLOT, height=320, barmode="group", yaxis_title="m³",
                                  legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig_pd, use_container_width=True, key="ov_m3_produced_vs_dispatched_dia")
        else:
            st.caption("No pipe production or dispatch data for selected period.")

        st.markdown("---")
        # Demand signal falls back Orders -> Dispatch -> Production, in that
        # order of "truest customer intent" — same precedent as
        # _render_pipe_demand_section. Dispatch/Orders are legitimately empty
        # until those modules are used, so without this fallback these three
        # charts would just be blank forever even though production data
        # (which does exist) already answers "which size/class/joint are we
        # actually making".
        ord_pipe_t = _tag_pipe_skus(df_ord_demand) if df_ord_demand is not None else pd.DataFrame()
        disp_pipe_t = _tag_pipe_skus(df_disp_pipe)
        prod_pipe_t = _tag_pipe_skus(df_prod_pipe)
        if not ord_pipe_t.empty:
            cmp_df, cmp_col, cmp_label = ord_pipe_t, "qty_ordered", "Ordered"
        elif not disp_pipe_t.empty:
            cmp_df, cmp_col, cmp_label = disp_pipe_t, "qty_dispatched", "Dispatched"
        else:
            cmp_df, cmp_col, cmp_label = prod_pipe_t, "nos", "Produced"

        st.markdown(f'<div class="section-header">🏗️ Pipe {cmp_label} Comparison — Diameter / Class / Joint</div>', unsafe_allow_html=True)
        if cmp_df.empty:
            st.caption("No pipe production, dispatch, or order data for selected period.")
        else:
            if cmp_label == "Produced":
                st.caption("No dispatch or order data logged yet — showing production instead.")
            dc1, dc2, dc3 = st.columns(3)
            with dc1:
                st.markdown("**By Diameter**")
                by_dia = cmp_df.groupby("Diameter")[cmp_col].sum().sort_index()
                fig_d = go.Figure(go.Bar(
                    x=[f"{d}mm" for d in by_dia.index], y=by_dia.values,
                    marker_color=ACCENT, text=by_dia.values.astype(int), textposition="outside",
                ))
                fig_d.update_layout(**PLOT, height=300, yaxis_title=f"Nos. {cmp_label}")
                st.plotly_chart(fig_d, use_container_width=True, key="ov_cmp_diameter")

            with dc2:
                st.markdown("**By Class**")
                by_cls = cmp_df.groupby("Class")[cmp_col].sum()
                fig_c = go.Figure(go.Pie(
                    labels=by_cls.index, values=by_cls.values, hole=0.45,
                    textinfo="label+percent", marker_colors=QUAL_COLORS,
                ))
                fig_c.update_layout(**PLOT, height=300, showlegend=False)
                st.plotly_chart(fig_c, use_container_width=True, key="ov_cmp_class")

            with dc3:
                st.markdown("**By Joint Type**")
                by_joint = cmp_df.groupby("Joint")[cmp_col].sum()
                fig_j = go.Figure(go.Pie(
                    labels=by_joint.index, values=by_joint.values, hole=0.45,
                    textinfo="label+percent", marker_colors=QUAL_COLORS[2:],
                ))
                fig_j.update_layout(**PLOT, height=300, showlegend=False)
                st.plotly_chart(fig_j, use_container_width=True, key="ov_cmp_joint")

        st.markdown("---")
        oc1, oc2 = st.columns(2)
        with oc1:
            st.markdown('<div class="section-header">🔵 Pipe Products</div>', unsafe_allow_html=True)
            if not df_prod_pipe.empty:
                st.metric("Production Value", f"₹{df_prod_pipe['revenue'].sum()/LAKH:.2f}L")
                st.metric("Profit", f"₹{df_prod_pipe['profit'].sum()/LAKH:.2f}L")
            else:
                st.caption("No pipe production this period.")
        with oc2:
            st.markdown('<div class="section-header">⚙️ Other Precast Products</div>', unsafe_allow_html=True)
            if not df_prod_other.empty:
                st.metric("Production Value", f"₹{df_prod_other['revenue'].sum()/LAKH:.2f}L")
                st.metric("Profit", f"₹{df_prod_other['profit'].sum()/LAKH:.2f}L")
            else:
                st.caption("No other-product production this period.")

        st.markdown("---")
        st.markdown('<div class="section-header">📦 Inventory</div>', unsafe_allow_html=True)
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
                    marker_color=ACCENT,
                    text=(top_stock["Value (₹)"] / LAKH).round(2).astype(str) + "L",
                    textposition="outside",
                ))
                fig_inv.update_layout(**PLOT, height=280, xaxis_title="Value (L)",
                                      yaxis=dict(autorange="reversed"))
                st.plotly_chart(fig_inv, use_container_width=True)

    with tabs[1]:
        _render_production_section(df_prod_pipe, df_disp_pipe, "Pipe Products", ACCENT, PLOT)

    with tabs[2]:
        _render_pipe_demand_section(df_prod_pipe, df_disp_pipe, df_ord_demand, PLOT)

    with tabs[3]:
        _render_production_section(df_prod_other, df_disp_other, "Other Precast Products", ACCENT_OTHER, PLOT)

    with tabs[4]:
        _render_dispatch_sales_tab(df_disp, PLOT)

    with tabs[5]:
        _render_sales_orders_tab(df_disp, start, end, PLOT)

    with tabs[6]:
        _render_advanced_analytics_tab(df_prod, df_disp, start, end, PLOT)
