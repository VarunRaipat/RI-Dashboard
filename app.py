import streamlit as st
from core.db import (
    init_db, get_production, log_failed_login, recent_failed_login_count,
    LOGIN_LOCKOUT_THRESHOLD, LOGIN_LOCKOUT_WINDOW_MIN,
)
from core.config import USERS as _USERS_DEFAULT

def _load_users():
    try:
        sec = st.secrets.get("users", {})
        if sec:
            return {k: {"password": v["password"], "role": v["role"], "name": v["name"]} for k, v in sec.items()}
    except Exception:
        pass
    return _USERS_DEFAULT

USERS = _load_users()

@st.cache_data(ttl=300)
def _get_today_stats(d):
    return get_production(d, d)

st.set_page_config(
    page_title="RI",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()

PLOT = dict(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font_color="#EDF0F2",
    margin=dict(l=20, r=20, t=40, b=20),
)

if "role" not in st.session_state:
    st.session_state.role     = None
    st.session_state.username = None
    st.session_state.name     = None

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

html, body, [class*="css"], button, input, select, textarea,
.stMarkdown, label, p, div {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}

/* ── App base ── */
.stApp { background: #0B0C0D !important; }
.block-container { padding-top: 3.5rem !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0E1112 0%, #0B0E0F 60%, #0A0C0D 100%) !important;
    border-right: 1px solid rgba(36,106,139,0.18) !important;
}
[data-testid="stSidebar"] .stRadio > label { display: none; }
[data-testid="stSidebar"] .stRadio > div { gap: 2px !important; }
[data-testid="stSidebar"] .stRadio label {
    border-radius: 8px !important;
    padding: 10px 14px !important;
    font-size: 0.87rem !important;
    font-weight: 500 !important;
    color: #65737A !important;
    cursor: pointer !important;
    transition: all 0.15s !important;
    width: 100% !important;
    display: block !important;
}
[data-testid="stSidebar"] .stRadio label:hover {
    background: rgba(36,106,139,0.10) !important;
    color: #57A4C8 !important;
}
[data-testid="stSidebar"] .stRadio [aria-checked="true"] + div label,
[data-testid="stSidebar"] .stRadio input:checked + label {
    background: rgba(36,106,139,0.15) !important;
    color: #57A4C8 !important;
    font-weight: 600 !important;
    border-left: 3px solid #246A8B !important;
}

/* ── Metric cards ── */
[data-testid="stMetric"] {
    background: linear-gradient(145deg, #121618 0%, #151A1C 100%) !important;
    border: 1px solid rgba(36,106,139,0.14) !important;
    border-top: 3px solid #246A8B !important;
    border-radius: 14px !important;
    padding: 18px 22px !important;
    transition: border-color 0.2s, box-shadow 0.2s !important;
}
[data-testid="stMetric"]:hover {
    border-color: rgba(36,106,139,0.35) !important;
    box-shadow: 0 4px 20px rgba(36,106,139,0.10) !important;
}
[data-testid="stMetricLabel"] > div {
    font-size: 0.68rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
    color: #48545A !important;
}
[data-testid="stMetricValue"] > div {
    font-size: 1.52rem !important;
    font-weight: 700 !important;
    color: #EDF0F2 !important;
    letter-spacing: -0.025em !important;
}
[data-testid="stMetricDelta"] { font-size: 0.80rem !important; }

/* ── Section headers ── */
.section-header {
    font-size: 0.70rem;
    font-weight: 700;
    color: #57A4C8;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    border-left: 3px solid #246A8B;
    padding: 4px 0 4px 12px;
    margin: 24px 0 14px 0;
    background: rgba(36,106,139,0.06);
    border-radius: 0 6px 6px 0;
}

/* ── Page title ── */
.page-title {
    font-size: 1.65rem;
    font-weight: 800;
    letter-spacing: -0.03em;
    color: #EDF0F2;
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 4px;
}
.page-subtitle {
    font-size: 0.78rem;
    font-weight: 400;
    color: #48545A;
    letter-spacing: 0.04em;
    margin-bottom: 20px;
}

/* ── Login card ── */
.eco-login-outer {
    background: linear-gradient(145deg, #101314 0%, #131618 100%);
    border: 1px solid rgba(36,106,139,0.22);
    border-top: 3px solid #246A8B;
    border-radius: 20px;
    padding: 36px 40px 36px;
    box-shadow: 0 32px 80px rgba(0,0,0,0.70), 0 0 0 1px rgba(36,106,139,0.06);
    margin-top: 8px;
}
.eco-login-divider {
    height: 1px;
    background: linear-gradient(90deg, rgba(36,106,139,0.35) 0%, transparent 80%);
    margin: 20px 0 24px;
}
.eco-login-label {
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    color: #48545A;
    margin-bottom: 4px;
    text-align: center;
}

/* ── Sidebar brand ── */
.sb-header {
    padding: 16px 16px 14px;
    border-bottom: 1px solid rgba(36,106,139,0.15);
    margin-bottom: 10px;
}
.sb-sub {
    font-size: 0.60rem;
    font-weight: 500;
    color: #2A353A;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    margin-top: 4px;
    text-align: center;
}
.sb-user {
    background: rgba(36,106,139,0.07);
    border: 1px solid rgba(36,106,139,0.14);
    border-radius: 12px;
    padding: 12px 16px;
    margin: 4px 10px 14px;
}
.sb-user-name {
    font-size: 0.88rem;
    font-weight: 600;
    color: #AEBDC4;
    margin-bottom: 5px;
}
.sb-badge {
    display: inline-block;
    font-size: 0.60rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 3px 9px;
    border-radius: 20px;
}
.sb-badge-admin      { background: rgba(36,106,139,0.18); color: #52B3E0; border: 1px solid rgba(36,106,139,0.30); }
.sb-badge-production { background: rgba(39,174,96,0.14); color: #27AE60; border: 1px solid rgba(39,174,96,0.25); }
.sb-badge-dispatch   { background: rgba(212,160,17,0.14); color: #D4A011; border: 1px solid rgba(212,160,17,0.25); }
.sb-badge-viewer     { background: rgba(148,163,184,0.14); color: #94A3B8; border: 1px solid rgba(148,163,184,0.25); }
.sb-badge-factory    { background: rgba(167,139,250,0.14); color: #A78BFA; border: 1px solid rgba(167,139,250,0.25); }

/* ── Success / warn / info boxes ── */
.success-box {
    background: rgba(39,174,96,0.08);
    border: 1px solid rgba(39,174,96,0.22);
    border-left: 4px solid #27AE60;
    border-radius: 10px;
    padding: 14px 18px;
    margin: 10px 0;
    color: #6EE7A0;
    font-weight: 500;
    font-size: 0.92rem;
}
.warn-box {
    background: rgba(212,160,17,0.08);
    border: 1px solid rgba(212,160,17,0.22);
    border-left: 4px solid #D4A011;
    border-radius: 10px;
    padding: 14px 18px;
    margin: 10px 0;
    color: #F0D060;
    font-size: 0.92rem;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    background: #121618 !important;
    border-radius: 12px !important;
    padding: 5px !important;
    gap: 3px !important;
    border: 1px solid rgba(36,106,139,0.14) !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px !important;
    padding: 8px 24px !important;
    font-size: 0.84rem !important;
    font-weight: 500 !important;
    color: #50626A !important;
    background: transparent !important;
    border: none !important;
}
.stTabs [aria-selected="true"] {
    background: linear-gradient(135deg, #246A8B, #2D7FA5) !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
    box-shadow: 0 2px 12px rgba(36,106,139,0.35) !important;
}

/* ── Expander ── */
[data-testid="stExpander"] {
    background: #121618 !important;
    border: 1px solid rgba(36,106,139,0.14) !important;
    border-radius: 12px !important;
}
[data-testid="stExpander"] summary {
    font-weight: 600 !important;
    font-size: 0.88rem !important;
    color: #788F9A !important;
}

/* ── DataFrames ── */
[data-testid="stDataFrame"] {
    border: 1px solid rgba(36,106,139,0.12) !important;
    border-radius: 12px !important;
    overflow: hidden !important;
}

/* ── Form submit & primary buttons ── */
[data-testid="stFormSubmitButton"] > button,
button[kind="primary"] {
    background: linear-gradient(135deg, #246A8B 0%, #1A516B 100%) !important;
    border: none !important;
    border-radius: 9px !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    letter-spacing: 0.02em !important;
    padding: 0.6rem 1.5rem !important;
    color: #FFFFFF !important;
    transition: all 0.2s ease !important;
    box-shadow: 0 2px 14px rgba(36,106,139,0.30) !important;
}
[data-testid="stFormSubmitButton"] > button:hover,
button[kind="primary"]:hover {
    background: linear-gradient(135deg, #2D7FA5 0%, #246A8B 100%) !important;
    box-shadow: 0 6px 22px rgba(36,106,139,0.45) !important;
    transform: translateY(-1px) !important;
}

/* ── Secondary buttons ── */
button[kind="secondary"] {
    border-radius: 8px !important;
    font-weight: 500 !important;
    font-size: 0.85rem !important;
    color: #65737A !important;
    border-color: rgba(36,106,139,0.22) !important;
}
button[kind="secondary"]:hover {
    border-color: rgba(36,106,139,0.50) !important;
    color: #57A4C8 !important;
    background: rgba(36,106,139,0.07) !important;
}

/* ── Headings ── */
h1 {
    font-size: 1.65rem !important;
    font-weight: 800 !important;
    letter-spacing: -0.03em !important;
    color: #EDF0F2 !important;
}
h2 { font-weight: 700 !important; color: #C4CFD4 !important; letter-spacing: -0.02em !important; }
h3 { font-weight: 600 !important; color: #A8B6BC !important; }

/* ── Divider ── */
hr {
    border: none !important;
    border-top: 1px solid rgba(36,106,139,0.10) !important;
    margin: 20px 0 !important;
}

/* ── Alerts ── */
[data-testid="stAlert"] {
    border-radius: 10px !important;
    border-left-width: 4px !important;
}

/* ── Inputs & select ── */
[data-baseweb="select"] > div {
    border-radius: 8px !important;
    border-color: rgba(36,106,139,0.20) !important;
}
input[type="text"], input[type="password"] {
    border-radius: 8px !important;
}

/* ── All forms as cards ── */
[data-testid="stForm"] {
    background: linear-gradient(145deg, #121618 0%, #151A1C 100%) !important;
    border: 1px solid rgba(36,106,139,0.18) !important;
    border-top: 3px solid #246A8B !important;
    border-radius: 16px !important;
    padding: 22px 28px !important;
    box-shadow: 0 8px 32px rgba(0,0,0,0.40) !important;
}

/* ── Today's snapshot widget ── */
.sb-today {
    background: rgba(36,106,139,0.06);
    border: 1px solid rgba(36,106,139,0.14);
    border-radius: 10px;
    padding: 10px 16px;
    margin: 0 10px 12px;
    text-align: center;
}
.sb-today-label {
    font-size: 0.56rem;
    font-weight: 700;
    letter-spacing: 0.18em;
    color: #48545A;
    margin-bottom: 4px;
}
.sb-today-nos {
    font-size: 1.50rem;
    font-weight: 800;
    color: #EDF0F2;
    letter-spacing: -0.03em;
    line-height: 1.1;
}
.sb-today-unit {
    font-size: 0.68rem;
    font-weight: 400;
    color: #65737A;
}
.sb-today-profit { font-size: 0.80rem; font-weight: 600; color: #27AE60; margin-top: 3px; }
.sb-today-loss   { font-size: 0.80rem; font-weight: 600; color: #52B3E0; margin-top: 3px; }

/* ── Quick date filter buttons ── */
.stButton > button { font-size: 0.80rem !important; }

/* ── Sidebar active nav — more prominent ── */
[data-testid="stSidebar"] .stRadio [aria-checked="true"] + div label,
[data-testid="stSidebar"] .stRadio input:checked + label {
    background: rgba(36,106,139,0.22) !important;
    color: #70BCE0 !important;
    font-weight: 700 !important;
    border-left: 3px solid #57A4C8 !important;
    box-shadow: inset 0 0 12px rgba(36,106,139,0.08) !important;
}

/* ── Expander header styling ── */
[data-testid="stExpander"] summary {
    font-size: 0.92rem !important;
    font-weight: 700 !important;
    color: #AEBDC4 !important;
    letter-spacing: 0.01em !important;
}
[data-testid="stExpander"] summary:hover { color: #EDF0F2 !important; }
</style>
""", unsafe_allow_html=True)

# ── Login ─────────────────────────────────────────────────────────────────────
if st.session_state.role is None:
    _, col, _ = st.columns([1, 1.1, 1])
    with col:
        st.markdown("<div style='height:40px'></div>", unsafe_allow_html=True)
        st.image("assets/Logo.png", width=240)
        st.markdown("""
        <div style='text-align:center; margin:16px 0 20px;'>
            <div style='font-size:1.50rem; font-weight:800; letter-spacing:-0.02em; color:#EDF0F2;'>RI</div>
            <div style='font-size:0.68rem; font-weight:600; letter-spacing:0.16em;
                 text-transform:uppercase; color:#48545A; margin-top:4px;'>
                Manufacturing Portal · Sign In
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.form("login_form"):
            username = st.text_input("Username", placeholder="Username", label_visibility="collapsed")
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            password = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
            submitted = st.form_submit_button("Sign In →", use_container_width=True, type="primary")

        if submitted:
            attempts = recent_failed_login_count(username)
            if attempts >= LOGIN_LOCKOUT_THRESHOLD:
                st.error(
                    f"Too many failed attempts for this username. "
                    f"Try again in {LOGIN_LOCKOUT_WINDOW_MIN} minutes."
                )
            else:
                user = USERS.get(username)
                if user and user["password"] == password:
                    st.session_state.role     = user["role"]
                    st.session_state.username = username
                    st.session_state.name     = user["name"]
                    from core.db import log_activity
                    log_activity("login", "Auth", f"{username} ({user['role']}) logged in")
                    st.rerun()
                else:
                    log_failed_login(username)
                    st.error("Invalid username or password.")

        st.markdown("""
        <div style='text-align:center; margin-top:28px;
             font-size:0.62rem; color:#202A2E; letter-spacing:0.14em;'>
            RAMESHWARAM INDUSTRIES · RCC HUME PIPES &amp; PRECAST CONCRETE
        </div>
        """, unsafe_allow_html=True)
    st.stop()

# ── Sidebar ───────────────────────────────────────────────────────────────────
role = st.session_state.role
name = st.session_state.name

ROLE_BADGE = {
    "admin":      "sb-badge-admin",
    "production": "sb-badge-production",
    "dispatch":   "sb-badge-dispatch",
    "viewer":     "sb-badge-viewer",
    "factory":    "sb-badge-factory",
}

with st.sidebar:
    st.markdown('<div class="sb-header">', unsafe_allow_html=True)
    st.image("assets/Logo.png", use_container_width=True)
    st.markdown('<div class="sb-sub">RI · Manufacturing Portal</div></div>', unsafe_allow_html=True)

    st.markdown(f"""
    <div class="sb-user">
        <div class="sb-user-name">👤 &nbsp;{name}</div>
        <span class="sb-badge {ROLE_BADGE.get(role, '')}">
            {role.upper()}
        </span>
    </div>
    """, unsafe_allow_html=True)

    # ── Today's snapshot ──────────────────────────────────────────────────────
    from core.tz import today_ist
    _today_str = str(today_ist())
    try:
        _df_t = _get_today_stats(_today_str)
        if not _df_t.empty:
            _pft_t    = _df_t["profit"].sum()
            _pft_cls  = "sb-today-profit" if _pft_t >= 0 else "sb-today-loss"
            _pft_word = "profit" if _pft_t >= 0 else "loss"
            _by_prod  = _df_t.groupby("product")["nos"].sum().reset_index().sort_values("nos", ascending=False)
            _rows_html = "".join(
                f"<div style='display:flex;justify-content:space-between;font-size:0.72rem;"
                f"color:#AEBDC4;padding:1px 0'>"
                f"<span style='color:#65737A'>{r['product']}</span>"
                f"<span style='font-weight:600'>{int(r['nos']):,}</span></div>"
                for _, r in _by_prod.iterrows()
            )
            st.markdown(f"""
            <div class="sb-today">
                <div class="sb-today-label">TODAY</div>
                {_rows_html}
                <div style='border-top:1px solid rgba(36,106,139,0.15);margin:6px 0 4px'></div>
                <div class="{_pft_cls}">₹{abs(_pft_t):,.0f} {_pft_word}</div>
            </div>
            """, unsafe_allow_html=True)
    except Exception:
        pass

    pages = []
    if role in ("admin", "viewer"):
        pages.append("📊  Dashboard")
    if role in ("admin", "production", "factory", "viewer"):
        pages.append("📋  DPR Entry")
    if role in ("admin", "headoffice", "viewer"):
        pages.append("🧾  Quotation")
    if role in ("admin", "headoffice", "viewer"):
        pages.append("📦  Sales Orders")
    if role in ("admin", "dispatch", "factory", "headoffice", "viewer"):
        pages.append("🚚  Dispatch Entry")
    if role in ("admin", "viewer"):
        pages.append("🤖  Assistant")
    if role in ("admin", "dispatch", "factory", "viewer"):
        pages.append("🏭  Inventory")
    if role in ("admin", "dispatch", "factory", "viewer"):
        pages.append("🚧  Gate Entry")
    if role == "admin":
        pages.append("💰  Liability")
    if role in ("admin", "viewer"):
        pages.append("⚙️  Admin")

    page = st.radio("Navigate", pages, label_visibility="collapsed")

    st.markdown("<div style='flex:1'></div>", unsafe_allow_html=True)
    st.markdown("---")
    st.markdown(
        "<div style='font-size:0.62rem;color:#243A50;letter-spacing:0.08em;"
        "text-align:center;margin-bottom:8px'>RI · RAMESHWARAM INDUSTRIES</div>",
        unsafe_allow_html=True,
    )
    if st.button("Sign Out", use_container_width=True):
        for k in ["role", "username", "name"]:
            st.session_state[k] = None
        st.rerun()

# ── Activity log: page opened (only once per navigation, not every rerun) ────
if st.session_state.get("_logged_page") != page:
    st.session_state["_logged_page"] = page
    from core.db import log_activity
    log_activity("view", page.strip())

# ── Route ─────────────────────────────────────────────────────────────────────
if page == "📋  DPR Entry":
    from views.dpr import show; show(PLOT)
elif page == "🧾  Quotation":
    from views.quotation import show; show(PLOT)
elif page == "📦  Sales Orders":
    from views.orders import show; show(PLOT)
elif page == "🚚  Dispatch Entry":
    from views.dispatch import show; show(PLOT)
elif page == "📊  Dashboard":
    from views.dashboard import show; show(PLOT)
elif page == "🤖  Assistant":
    from views.chat import show; show(PLOT)
elif page == "🏭  Inventory":
    from views.inventory import show; show(PLOT)
elif page == "🚧  Gate Entry":
    from views.gate_entry import show; show(PLOT)
elif page == "💰  Liability":
    from views.liability import show; show(PLOT)
elif page == "⚙️  Admin":
    from views.admin import show; show(PLOT)
