import streamlit as st
from datetime import timedelta
from core.db import get_production, get_dispatch, get_product_config, get_pipe_diameter_config
from core.calculations import liability_totals
from core.config import REPAIRING_PCT_OF_PRODUCTION
from core.ui import show_flashes
from core.tz import today_ist


def _pay_week(offset_weeks=0):
    """Friday-Thursday liability pay week (confirmed cycle — not the
    calendar Monday-Sunday week used elsewhere in the app). offset_weeks=0
    is the pay week containing today, -1 is the one before it."""
    today = today_ist()
    week_start = today - timedelta(days=(today.weekday() - 4) % 7) + timedelta(weeks=offset_weeks)
    return week_start, week_start + timedelta(days=6)


def show(PLOT):
    show_flashes()

    st.markdown("""
    <div class="page-title">💰 Liability</div>
    <div class="page-subtitle">Labour cost owed for the selected period (Friday–Thursday pay week)</div>
    """, unsafe_allow_html=True)
    st.caption(
        "Production + Jalli + Welding + Repairing (from DPR) + Loading/Unloading (from Dispatch qty). "
        f"Repairing is always {REPAIRING_PCT_OF_PRODUCTION:.0f}% of Production cost — not entered separately."
    )

    start_key, end_key = "liab_start", "liab_end"
    if start_key not in st.session_state:
        st.session_state[start_key], st.session_state[end_key] = _pay_week(0)

    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("This Pay Week", use_container_width=True):
        st.session_state[start_key], st.session_state[end_key] = _pay_week(0)
    if b2.button("Last Pay Week", use_container_width=True):
        st.session_state[start_key], st.session_state[end_key] = _pay_week(-1)

    c1, c2 = st.columns(2)
    start = c1.date_input("From", key=start_key)
    end   = c2.date_input("To", key=end_key)

    df_prod = get_production(str(start), str(end))
    df_disp = get_dispatch(str(start), str(end))
    r = liability_totals(df_prod, df_disp, get_product_config(), get_pipe_diameter_config())

    c1, c2, c3 = st.columns(3)
    c1.metric("Production", f"₹{r['production_cost']:,.0f}")
    c2.metric("Welding", f"₹{r['welding_cost']:,.0f}")
    c3.metric("Jalli", f"₹{r['jalli_cost']:,.0f}")

    c4, c5 = st.columns(2)
    c4.metric(f"Repairing ({REPAIRING_PCT_OF_PRODUCTION:.0f}%)", f"₹{r['repairing_cost']:,.0f}")
    c5.metric("Loading/Unloading", f"₹{r['loading_unloading_cost']:,.0f}")

    st.markdown("---")
    st.metric("Total", f"₹{r['total_cost']:,.0f}")
