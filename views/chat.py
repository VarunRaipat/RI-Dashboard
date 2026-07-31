import streamlit as st
import pandas as pd
from datetime import timedelta
from core.tz import today_ist
from core.db import (
    get_production, get_dispatch, get_orders,
    get_vendor_transactions, get_loan_payments, get_gate_entries,
    get_quotations, get_rm_usage, get_rm_prices,
)

LAKH = 100_000


def _build_context() -> str:
    today = today_ist()
    month_start = today.replace(day=1)

    df_prod = get_production()
    df_disp = get_dispatch()
    df_ord  = get_orders()

    lines = [
        f"Today: {today.strftime('%d %b %Y')}",
        f"Company: Rameshwaram Industries — manufactures RCC Hume Pipes (150mm-1200mm, NP2/NP3/NP4), Slabs, Pillars, Fencing Pillars, and PSC Poles.",
        "",
    ]

    # ── Production summary ────────────────────────────────────────────────────
    if not df_prod.empty:
        df_prod["date"] = pd.to_datetime(df_prod["date"], errors="coerce")
        df_m = df_prod[df_prod["date"] >= pd.Timestamp(month_start)]

        lines.append("=== PRODUCTION (This Month) ===")
        if not df_m.empty:
            lines.append(f"Total nos produced: {int(df_m['nos'].sum()):,}")
            lines.append(f"Production days: {df_m['date'].nunique()}")
            lines.append(f"Revenue: ₹{df_m['revenue'].sum()/LAKH:.2f}L")
            lines.append(f"Total Cost: ₹{df_m['total_cost'].sum()/LAKH:.2f}L")
            lines.append(f"Profit: ₹{df_m['profit'].sum()/LAKH:.2f}L")
            lines.append(f"Avg Profit %: {df_m['profit_pct'].mean():.1f}%")
            by_prod = df_m.groupby("product")["nos"].sum().sort_values(ascending=False)
            lines.append("By product: " + ", ".join(f"{p}: {int(n):,}" for p, n in by_prod.items()))

        df_prod["month"] = df_prod["date"].dt.to_period("M").astype(str)
        monthly = df_prod.groupby("month").agg(
            nos=("nos","sum"), profit=("profit","sum"), profit_pct=("profit_pct","mean")
        ).tail(6)
        lines.append("Last 6 months production:")
        for m, row in monthly.iterrows():
            lines.append(f"  {m}: {int(row['nos']):,} nos | Profit ₹{row['profit']/LAKH:.2f}L | {row['profit_pct']:.1f}%")

        if "plant" in df_prod.columns:
            plant_s = df_prod.groupby("plant")["nos"].sum()
            lines.append("Plant-wise all time: " + ", ".join(f"{p}: {int(n):,}" for p, n in plant_s.items()))

        if "operator_name" in df_prod.columns:
            top_op = df_prod.groupby("operator_name")["nos"].sum().sort_values(ascending=False).head(5)
            lines.append("Top operators (nos): " + ", ".join(f"{o}: {int(n):,}" for o, n in top_op.items()))

        low_profit = df_prod[df_prod["profit_pct"] < 10]
        lines.append(f"Entries with profit < 10%: {len(low_profit)}")
        lines.append("")

    # ── Dispatch summary ──────────────────────────────────────────────────────
    if not df_disp.empty:
        df_disp["date"] = pd.to_datetime(df_disp["date"], errors="coerce")
        df_dm = df_disp[df_disp["date"] >= pd.Timestamp(month_start)]

        lines.append("=== DISPATCH (This Month) ===")
        if not df_dm.empty:
            lines.append(f"Challans: {len(df_dm)}")
            lines.append(f"Dispatch Value: ₹{df_dm['dispatch_value'].sum()/LAKH:.2f}L")
            pending_mask = df_dm["bill_no"].isna() | df_dm["bill_no"].astype(str).str.strip().isin(["","None","nan"])
            lines.append(f"Pending bills: {pending_mask.sum()} challans")

        top_cl = df_disp.groupby("client_name")["dispatch_value"].sum().sort_values(ascending=False).head(5)
        lines.append("Top 5 clients all time: " + ", ".join(f"{c}: ₹{v/LAKH:.2f}L" for c, v in top_cl.items()))

        all_pending = df_disp[df_disp["bill_no"].isna() | df_disp["bill_no"].astype(str).str.strip().isin(["","None","nan"])]
        lines.append(f"Total unbilled challans (all time): {len(all_pending)} | Value: ₹{all_pending['dispatch_value'].sum()/LAKH:.2f}L")
        lines.append("")

    # ── Sales orders summary ──────────────────────────────────────────────────
    if not df_ord.empty:
        df_ord["order_date"] = pd.to_datetime(df_ord["order_date"], errors="coerce")
        lines.append("=== SALES ORDERS ===")
        lines.append(f"Total DIs: {df_ord['di_no'].nunique()}")
        lines.append(f"Total Order Value: ₹{df_ord['total_amount'].sum()/LAKH:.2f}L")

        if "mode_of_payment" in df_ord.columns:
            pay = df_ord.groupby("mode_of_payment")["total_amount"].sum().sort_values(ascending=False)
            lines.append("Payment mode mix: " + ", ".join(f"{m}: ₹{v/LAKH:.2f}L" for m, v in pay.items()))

        repeat = df_ord.groupby("client_name")["di_no"].nunique()
        repeat_clients = repeat[repeat > 1]
        lines.append(f"Repeat clients: {len(repeat_clients)} ({', '.join(repeat_clients.index.tolist()[:5])})")
        lines.append("")

    # ── Quotations summary ──────────────────────────────────────────────────
    df_quot = get_quotations()
    if not df_quot.empty:
        df_quot["quote_date"] = pd.to_datetime(df_quot["quote_date"], errors="coerce")
        df_qm = df_quot[df_quot["quote_date"] >= pd.Timestamp(month_start)]

        lines.append("=== QUOTATIONS ===")
        lines.append(f"Total quotes: {df_quot['quote_no'].nunique()} | This month: {len(df_qm)}")
        lines.append(f"Total quoted value (all time): ₹{df_quot['amount'].sum()/LAKH:.2f}L")
        if "status" in df_quot.columns:
            by_status = df_quot.groupby("status")["amount"].agg(["count", "sum"])
            lines.append("By status: " + ", ".join(
                f"{s}: {int(r['count'])} (₹{r['sum']/LAKH:.2f}L)" for s, r in by_status.iterrows()
            ))
        if "converted_di_no" in df_quot.columns:
            converted = df_quot["converted_di_no"].notna() & (df_quot["converted_di_no"].astype(str).str.strip() != "")
            lines.append(f"Converted to orders: {converted.sum()} of {len(df_quot)}")
        lines.append("")

    # ── Vendor transactions (payables/purchases) ──────────────────────────────
    df_vt = get_vendor_transactions()
    if not df_vt.empty:
        df_vt["date"] = pd.to_datetime(df_vt["date"], errors="coerce")
        df_vm = df_vt[df_vt["date"] >= pd.Timestamp(month_start)]

        lines.append("=== VENDOR TRANSACTIONS ===")
        if "txn_type" in df_vt.columns:
            by_type_m = df_vm.groupby("txn_type")["amount"].sum()
            lines.append("This month by type: " + ", ".join(f"{t}: ₹{v/LAKH:.2f}L" for t, v in by_type_m.items()))
            by_type_all = df_vt.groupby("txn_type")["amount"].sum()
            lines.append("All time by type: " + ", ".join(f"{t}: ₹{v/LAKH:.2f}L" for t, v in by_type_all.items()))
        top_vendors = df_vt.groupby("vendor_name")["amount"].sum().sort_values(ascending=False).head(5)
        lines.append("Top vendors (all time): " + ", ".join(f"{v}: ₹{a/LAKH:.2f}L" for v, a in top_vendors.items()))
        lines.append("")

    # ── Loan / EMI payments ────────────────────────────────────────────────────
    df_loan = get_loan_payments()
    if not df_loan.empty:
        lines.append("=== LOAN PAYMENTS ===")
        by_loan = df_loan.groupby("loan_name").agg(emi=("emi_amount", "sum"), paid=("paid_amount", "sum"))
        for name, row in by_loan.iterrows():
            pending = row["emi"] - row["paid"]
            lines.append(f"  {name}: EMI total ₹{row['emi']/LAKH:.2f}L, Paid ₹{row['paid']/LAKH:.2f}L, Pending ₹{pending/LAKH:.2f}L")
        lines.append("")

    # ── Gate entries (material/equipment movement) ─────────────────────────────
    df_gate = get_gate_entries()
    if not df_gate.empty:
        df_gate["date"] = pd.to_datetime(df_gate["date"], errors="coerce")
        df_gm = df_gate[df_gate["date"] >= pd.Timestamp(month_start)]
        lines.append("=== GATE ENTRIES (This Month) ===")
        if not df_gm.empty and "direction" in df_gm.columns:
            by_dir = df_gm.groupby("direction").size()
            lines.append("By direction: " + ", ".join(f"{d}: {n}" for d, n in by_dir.items()))
        if not df_gm.empty and "category" in df_gm.columns:
            by_cat = df_gm.groupby("category").size().sort_values(ascending=False).head(5)
            lines.append("Top categories: " + ", ".join(f"{c}: {n}" for c, n in by_cat.items()))
        lines.append("")

    # ── Raw material usage & pricing ────────────────────────────────────────────
    df_rm = get_rm_usage()
    if not df_rm.empty:
        df_rm["date"] = pd.to_datetime(df_rm["date"], errors="coerce")
        df_rmm = df_rm[df_rm["date"] >= pd.Timestamp(month_start)]
        lines.append("=== RAW MATERIAL USAGE (This Month) ===")
        if not df_rmm.empty:
            lines.append(f"Cement bags: {df_rmm['cement_bags'].sum():,.0f} | GGBS bags: {df_rmm['ggbs_bags'].sum():,.0f}")
        prices = get_rm_prices()
        lines.append("Current RM prices: " + ", ".join(f"{k}: ₹{v:,.0f}" for k, v in prices.items()))
        lines.append("")

    return "\n".join(lines)


def _get_client():
    try:
        import anthropic
    except ImportError:
        return None, "anthropic package not installed — redeploy the app."

    api_key = ""
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass

    if not api_key:
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        # show what keys ARE present to help diagnose
        try:
            present = list(st.secrets.keys())
        except Exception:
            present = ["(could not read secrets)"]
        return None, f"ANTHROPIC_API_KEY not found in secrets. Keys present: {present}"

    try:
        return anthropic.Anthropic(api_key=api_key), None
    except Exception as e:
        return None, f"Failed to create Anthropic client: {e}"


SYSTEM_PROMPT = """You are a sharp business analyst for Rameshwaram Industries, a manufacturing company.
You have access to live business data provided below. Answer questions concisely and in plain language.
Use ₹ for currency, mention values in Lakhs (L) when large.
If asked something not in the data, say so clearly rather than guessing.
Keep answers short — 2-5 sentences unless a table or breakdown is genuinely useful.
Current business data:

{context}"""


def show(PLOT):
    st.markdown("""
    <div class="page-title">🤖 Business Assistant</div>
    <div class="page-subtitle">Ask anything about production, dispatch, sales, profit</div>
    """, unsafe_allow_html=True)

    client, err = _get_client()
    if err:
        st.error(f"⚠️ {err}")
        if "ANTHROPIC_API_KEY" in err:
            st.info("Add `ANTHROPIC_API_KEY = \"sk-ant-...\"` to your Streamlit Cloud secrets, then reboot the app.")
        return

    # Session state for chat history
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # Suggested questions
    user_input = None
    if not st.session_state.chat_history:
        st.markdown('<div class="section-header">Try asking</div>', unsafe_allow_html=True)
        suggestions = [
            "What is my profit this month?",
            "Which product has the highest margin?",
            "Who are my top 3 clients?",
            "How many challans are unbilled?",
            "Which operator produces the most?",
            "Compare old plant vs new plant performance",
            "What's my pending EMI on loans?",
            "Which vendors have I paid the most this month?",
        ]
        cols = st.columns(3)
        for i, s in enumerate(suggestions):
            if cols[i % 3].button(s, key=f"sugg_{i}", use_container_width=True):
                user_input = s

    # Display chat history
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    user_input = user_input or st.chat_input("Ask about your business data…")
    if user_input:
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                context = _build_context()
                messages = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state.chat_history
                ]
                try:
                    resp = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=600,
                        system=SYSTEM_PROMPT.format(context=context),
                        messages=messages,
                    )
                    answer = resp.content[0].text
                except Exception as e:
                    answer = f"Error calling AI: {e}"

            st.markdown(answer)
            st.session_state.chat_history.append({"role": "assistant", "content": answer})

    # Clear button
    if st.session_state.chat_history:
        st.markdown("---")
        if st.button("🗑️ Clear chat", key="clear_chat"):
            st.session_state.chat_history = []
            st.rerun()
