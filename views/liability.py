import streamlit as st
from core.db import get_production
from core.calculations import weekly_liability
from core.config import LIABILITY_PCT, REPAIRING_PCT_OF_PRODUCTION
from core.ui import show_flashes, interactive_table


def show(PLOT):
    show_flashes()

    st.markdown("""
    <div class="page-title">💰 Liability</div>
    <div class="page-subtitle">Weekly labour liability, accrued automatically from DPR entries</div>
    """, unsafe_allow_html=True)
    st.caption(
        f"Accrued liability = {LIABILITY_PCT:.0f}% of (Production + Jalli + Welding + Repairing) "
        f"cost each week. Repairing isn't entered separately — it's always "
        f"{REPAIRING_PCT_OF_PRODUCTION:.0f}% of that week's Production cost."
    )

    df_prod = get_production()
    weekly  = weekly_liability(df_prod)

    if weekly.empty:
        st.info("No production data yet — liability accrues automatically from DPR entries.")
        return

    weekly["week_start"] = weekly["week_start"].astype(str)
    weekly["week_end"]   = weekly["week_end"].astype(str)

    st.metric("Total Accrued Liability", f"₹{weekly['accrued_liability'].sum():,.0f}")

    st.markdown('<div class="section-header">Weekly Liability</div>', unsafe_allow_html=True)
    show_cols = ["week_start", "week_end", "production_cost", "welding_cost", "jalli_cost",
                 "repairing_cost", "liability_base", "accrued_liability"]
    rename = {
        "week_start": "Week Start", "week_end": "Week End",
        "production_cost": "Production", "welding_cost": "Welding", "jalli_cost": "Jalli",
        "repairing_cost": f"Repairing ({REPAIRING_PCT_OF_PRODUCTION:.0f}%)",
        "liability_base": "Base", "accrued_liability": f"Accrued ({LIABILITY_PCT:.0f}%)",
    }
    interactive_table(
        weekly.sort_values("week_start", ascending=False), key="liability_weekly",
        show_cols=show_cols, rename=rename,
        sum_cols=["accrued_liability"],
    )
