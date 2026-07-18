import streamlit as st
from core.db import get_production
from core.calculations import liability_totals
from core.config import LIABILITY_PCT, REPAIRING_PCT_OF_PRODUCTION
from core.ui import show_flashes, quick_date_range_filter


def show(PLOT):
    show_flashes()

    st.markdown("""
    <div class="page-title">💰 Liability</div>
    <div class="page-subtitle">Labour liability accrued from DPR entries in the selected period</div>
    """, unsafe_allow_html=True)
    st.caption(
        f"Accrued liability = {LIABILITY_PCT:.0f}% of (Production + Jalli + Welding + Repairing) "
        f"cost. Repairing isn't entered separately — it's always "
        f"{REPAIRING_PCT_OF_PRODUCTION:.0f}% of Production cost."
    )

    start, end = quick_date_range_filter("liability")

    df_prod = get_production(str(start), str(end))
    r = liability_totals(df_prod)

    c1, c2, c3 = st.columns(3)
    c1.metric("Production", f"₹{r['production_cost']:,.0f}")
    c2.metric("Welding", f"₹{r['welding_cost']:,.0f}")
    c3.metric("Jalli", f"₹{r['jalli_cost']:,.0f}")

    c4, c5 = st.columns(2)
    c4.metric(f"Repairing ({REPAIRING_PCT_OF_PRODUCTION:.0f}%)", f"₹{r['repairing_cost']:,.0f}")
    c5.metric("Base", f"₹{r['liability_base']:,.0f}")

    st.markdown("---")
    st.metric(f"Accrued Liability ({LIABILITY_PCT:.0f}%)", f"₹{r['accrued_liability']:,.0f}")
