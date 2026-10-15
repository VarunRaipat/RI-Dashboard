"""Leads & Visits.

  * My Day       — phone-first follow-up queue (views/lead_desk.py).
  * Leads        — one row per enquiry: source, contact, company, product,
                   the salesperson it's assigned to, what was discussed,
                   remarks and status (Fresh / Negotiation / Converted /
                   Did Not Convert). follow_up_date is the *next* follow-up:
                   the day after the lead comes in, then whatever each
                   update in My Day sets. (qty is no longer entered.)
  * Sales Visits — one row per field visit: where, whom, why, what came of
                   it, and when to go back. A visit can also open a new lead.
  * Analysis     — totals, conversion ratio, and performance by source,
                   month, product and person, for any date range.
  * Team         — scorecard per salesperson (views/lead_desk.py).
  * Import       — Excel/CSV import (IndiaMART / Meta exports), IndiaMART live
                   sync status, and who new leads are auto-assigned to.

Leads are owned by a login (`owner` = username; core/crm.py). Role "sales"
only sees leads it owns and visits it made; every other role with Leads
access (Admin, Head Office) sees everything.

Both lists are editable in place like a spreadsheet (for roles with Edit
access) and can be filtered, searched and sorted. Access comes from the
Access-Control matrix (module "leads"); no role has it by default.
"""
import re
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from core.config import plant_for_product
from core.db import (get_leads, get_sales_visits, get_lead_activities, insert_sales_row,
                     update_sales_row, delete_row)
from core.crm import (crm_users, owner_name, all_names, new_lead_row, next_owner, PRIORITIES,
                      DEFAULT_PRIORITY, phone_key, sync_indiamart, indiamart_key, guess_column,
                      IMPORT_FIELDS, salespeople, now_str, can)
from core.db import get_crm_setting, set_crm_setting
from core.ui import flash, show_flashes, quick_date_range_filter, _to_excel_bytes
from views.lead_desk import LOST_REASONS, log_update

from core.tz import IST

# Lead "Product" is the product family, not the exact SKU (a pipe enquiry is
# rarely sized on the first call); each family belongs to one plant.
LEAD_PRODUCTS = ["Hume Pipe", "Pipe Collar", "Boundary Wall", "Slab", "Slab Design", "Pillar",
                 "Fencing Pillar", "PSC Pole"]


def get_order_products():
    return list(LEAD_PRODUCTS)


def _plant(product):
    if not product or (not isinstance(product, str) and pd.isna(product)) or product not in LEAD_PRODUCTS:
        return "— Not set / multiple"
    return plant_for_product(product)
OPEN_STATUSES = ["Fresh", "Negotiation"]
STATUSES = OPEN_STATUSES + ["Converted", "Did Not Convert"]
# "Others" is stored as "Others: <what was typed>" so the detail isn't lost;
# analysis folds every such value back into one "Others" bucket.
SOURCES = ["IndiaMART", "Past Customer", "Meta", "Walk-In", "Reference", "Others"]
PURPOSES = ["New Enquiry", "Follow-up", "Payment Collection", "Complaint / Service",
            "Site Inspection", "Other"]
_STATUS_COLOR = {"Fresh": "#5B8DEF", "Negotiation": "#D4A011",
                 "Converted": "#27AE60", "Did Not Convert": "#C0504D"}

LEAD_COLS = {  # db column -> sheet header
    "id": "ID", "lead_date": "Date", "source": "Source", "contact_name": "Name",
    "phone": "Phone", "company": "Company", "product": "Product", "city": "City",
    "priority": "Priority", "assigned_to": "Assigned To", "discussion": "Discussed", "remarks": "Remarks",
    "status": "Status", "follow_up_date": "Next Follow-up", "lost_reason": "Lost Reason",
    "order_value": "Order Value", "created_by": "Added By",
}
VISIT_COLS = {
    "id": "ID", "visit_date": "Date", "visited_by": "Visited By", "place": "Place",
    "company": "Company", "contact_name": "Met", "phone": "Phone", "purpose": "Purpose",
    "discussion": "Discussed", "remarks": "Outcome / Remarks",
    "next_visit_date": "Next Visit", "created_by": "Added By",
}
_DATE_COLS = {"lead_date", "follow_up_date", "visit_date", "next_visit_date"}


def _source_group(s):
    """'Others: Trade fair' / legacy values -> 'Others'; named sources unchanged."""
    return s if s in SOURCES[:-1] else "Others"


def _source_options(df):
    """Sheet dropdown: the fixed list plus every 'Others: …' value already saved."""
    extra = sorted(v for v in df["source"].dropna().unique() if v not in SOURCES) if not df.empty else []
    return SOURCES + extra


def _sees_all(role):
    return role != "sales"


def _my_username():
    return st.session_state.get("username") or ""


def _is_me(series):
    me = _me().strip().lower()
    return series.fillna("").astype(str).str.strip().str.lower() == me


def _scope_leads(df, role):
    """Salespeople see only the leads they own."""
    if _sees_all(role) or df.empty:
        return df
    return df[df["owner"] == _my_username()]


def _scope_visits(df, role):
    if _sees_all(role) or df.empty:
        return df
    return df[_is_me(df["visited_by"]) | _is_me(df["created_by"])]


def _owner_names():
    """Display names offered in the sheet's Assigned To dropdown."""
    return sorted(set(crm_users().values()))


def _today():
    return datetime.now(IST).date()


def _me():
    return st.session_state.get("name") or st.session_state.get("username") or ""


def _clean(v):
    """Normalise a cell for comparison / saving: NaN/NaT/'' -> None, dates -> ISO."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return v.strip() or None
    return v


def _to_sheet(df, cols):
    """DB frame -> display frame with sheet headers and real date objects."""
    out = df.reindex(columns=list(cols)).copy()
    for c in _DATE_COLS & set(out.columns):
        out[c] = pd.to_datetime(out[c], errors="coerce").dt.normalize()
    if "order_value" in out:
        out["order_value"] = pd.to_numeric(out["order_value"], errors="coerce")
    return out.rename(columns=cols)


def _save_sheet_edits(table, original, edited, cols):
    """Write back only the cells that changed. Returns the number of rows updated."""
    inv = {v: k for k, v in cols.items()}
    orig = original.set_index("ID")
    n = 0
    for _, row in edited.iterrows():
        rid = int(row["ID"])
        if rid not in orig.index:
            continue
        changes = {}
        for header, val in row.items():
            col = inv.get(header)
            if col in (None, "id", "created_by"):
                continue
            new, old = _clean(val), _clean(orig.at[rid, header])
            if new != old:
                changes[col] = new
        if changes:
            if table == "leads":
                changes["updated_at"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
                if "assigned_to" in changes:
                    by_name = {v: k for k, v in {**all_names(), **crm_users()}.items()}
                    changes["owner"] = by_name.get(changes["assigned_to"])
            update_sales_row(table, rid, changes)
            n += 1
    return n


def _search(df, text, cols):
    if not text.strip():
        return df
    t = text.strip().lower()
    mask = pd.Series(False, index=df.index)
    for c in cols:
        if c in df:
            mask |= df[c].astype(str).str.lower().str.contains(t, regex=False, na=False)
    return df[mask]


def _in_range(df, col, start, end):
    d = pd.to_datetime(df[col], errors="coerce")
    return df[(d >= pd.Timestamp(start)) & (d <= pd.Timestamp(end))]


# ── Leads ─────────────────────────────────────────────────────────────────────
def _add_lead_form(products, existing, role, key="sheet"):
    mine_only = not _sees_all(role)
    users = crm_users()
    with st.expander("➕ Add a lead"):
        with st.form(f"lead_add_form_{key}", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            phone = c1.text_input("Phone", placeholder="Mobile number")
            name = c2.text_input("Contact name")
            company = c3.text_input("Company name")
            c4, c5, c6 = st.columns(3)
            source = c4.selectbox("Source", SOURCES)
            other_src = c5.text_input("If Others — type source", placeholder="e.g. Trade fair")
            product = c6.selectbox("Product", ["—"] + products + ["Multiple / Other"])
            c7, c8, c9 = st.columns(3)
            if mine_only:
                c7.text_input("Sales assigned to", value=_me(), disabled=True)
                owner_pick = _my_username()
            else:
                owner_pick = c7.selectbox("Sales assigned to", ["__auto__"] + list(users),
                                          format_func=lambda u: "⚙️ Auto-assign" if u == "__auto__" else users[u])
            city = c8.text_input("City / site (optional)")
            priority = c9.selectbox("Priority", PRIORITIES, index=PRIORITIES.index(DEFAULT_PRIORITY))
            c10, c11 = st.columns([1, 2])
            lead_date = c10.date_input("Date", value=_today())
            status = c11.selectbox("Status", STATUSES)
            discussion = st.text_area("What was discussed / requirement", height=80)
            remarks = st.text_input("Remarks")
            ok = st.form_submit_button("Save Lead", type="primary")
        if ok:
            if not (name.strip() or company.strip() or phone.strip()):
                st.error("Enter at least a name, phone or company.")
                return
            if source == "Others":
                if not other_src.strip():
                    st.error("Source is Others — type what the source was.")
                    return
                source = f"Others: {other_src.strip()}"
            dup = None
            pk = phone_key(phone)
            if pk and not existing.empty:
                hit = existing[existing["phone"].map(phone_key) == pk]
                if not hit.empty:
                    dup = hit.iloc[0]
            owner = next_owner() if owner_pick == "__auto__" else owner_pick
            insert_sales_row("leads", new_lead_row(
                lead_date=lead_date, source=source, owner=owner, contact_name=name.strip(),
                phone=phone.strip(), company=company.strip(),
                product=None if product == "—" else product, city=city.strip(),
                discussion=discussion.strip(), remarks=remarks.strip(), status=status,
                priority=priority, created_by=_me()))
            flash(f"✅ Lead saved — {company.strip() or name.strip() or phone.strip()}"
                  + (f" · assigned to {owner_name(owner)}" if owner and not mine_only else ""),
                  f"Note: this phone number is already on lead #{int(dup['id'])} "
                  f"({dup.get('company') or dup.get('contact_name') or ''}, "
                  f"assigned to {owner_name(dup.get('owner'))})" if dup is not None else None)
            st.rerun()


def _show_leads(role):
    all_leads = get_leads()
    df = _scope_leads(all_leads, role)
    products = get_order_products()
    today = _today()

    if can(role, "leads", "add"):
        _add_lead_form(products, all_leads, role)

    if df.empty:
        st.info("No leads yet.")
        return

    st.markdown("##### Lead sheet")
    start, end = quick_date_range_filter("leads", default_start=today.replace(day=1))
    f1, f2, f5, f3, f4 = st.columns([1.1, 1.1, 1.1, 1.5, 1.1])
    st_pick = f1.multiselect("Status", STATUSES, key="lead_f_status")
    src_pick = f2.multiselect("Source", SOURCES, key="lead_f_src")
    who_pick = f5.multiselect("Assigned to", _owner_names() + ["— Unassigned"], key="lead_f_who") \
        if _sees_all(role) else []
    q = f3.text_input("Search name / phone / company / product", key="lead_f_q")
    sort = f4.selectbox("Sort by", ["Newest first", "Oldest first", "Status", "Assigned to",
                                    "Company A–Z", "Source"], key="lead_f_sort")

    view = _in_range(df, "lead_date", start, end)
    if st_pick:
        view = view[view["status"].isin(st_pick)]
    if src_pick:
        view = view[view["source"].map(_source_group).isin(src_pick)]
    if who_pick:
        view = view[view["owner"].map(owner_name).isin(who_pick)]
    view = _search(view, q, ["contact_name", "phone", "company", "product", "city", "discussion", "remarks"])

    # Pipeline counts for the filtered range
    counts = view["status"].value_counts()
    m = st.columns(6)
    m[0].metric("Leads", len(view))
    for i, s in enumerate(STATUSES, start=1):
        m[i].metric(s, int(counts.get(s, 0)))
    m[5].metric("Conversion", _pct(counts.get("Converted", 0), len(view)),
                help="Converted ÷ all leads in this range. Full breakdown in the 📊 Analysis tab.")

    if sort == "Newest first":
        view = view.sort_values(["lead_date", "id"], ascending=False)
    elif sort == "Oldest first":
        view = view.sort_values(["lead_date", "id"])
    elif sort == "Assigned to":
        view = view.assign(_o=view["owner"].map(owner_name)).sort_values(
            ["_o", "lead_date"], ascending=[True, False]).drop(columns="_o")
    elif sort == "Status":
        view = view.assign(_o=view["status"].map({s: i for i, s in enumerate(STATUSES)})).sort_values(
            ["_o", "lead_date"], ascending=[True, False]).drop(columns="_o")
    elif sort == "Company A–Z":
        view = view.sort_values("company", key=lambda s: s.fillna("").str.lower())
    else:
        view = view.sort_values(["source", "lead_date"], ascending=[True, False])

    _sheet("leads", view, LEAD_COLS, role, {
        "ID": st.column_config.NumberColumn("ID", disabled=True, width="small"),
        "Date": st.column_config.DateColumn("Date", format="DD MMM YYYY", required=True),
        "Source": st.column_config.SelectboxColumn("Source", options=_source_options(df)),
        "Product": st.column_config.SelectboxColumn("Product", options=products + ["Multiple / Other"]),
        "Assigned To": st.column_config.SelectboxColumn("Assigned To", options=_owner_names(),
                                                        disabled=not _sees_all(role)),
        "Priority": st.column_config.SelectboxColumn("Priority", options=PRIORITIES),
        "Status": st.column_config.SelectboxColumn("Status", options=STATUSES, required=True),
        "Next Follow-up": st.column_config.DateColumn("Next Follow-up", format="DD MMM YYYY"),
        "Lost Reason": st.column_config.SelectboxColumn("Lost Reason", options=LOST_REASONS),
        "Order Value": st.column_config.NumberColumn("Order Value", min_value=0, format="₹%.0f"),
        "Discussed": st.column_config.TextColumn("Discussed", width="large"),
        "Remarks": st.column_config.TextColumn("Remarks", width="medium"),
        "Added By": st.column_config.TextColumn("Added By", disabled=True),
    }, label=lambda r: f"#{int(r['id'])} · {r.get('company') or r.get('contact_name') or r.get('phone') or ''}")


# ── Analysis ──────────────────────────────────────────────────────────────────
def _pct(num, den):
    return f"{num / den * 100:.1f}%" if den else "—"


def _breakdown(df, by, label):
    """One row per `by` value: lead counts per status plus both conversion ratios."""
    g = (df.assign(_k=df[by].fillna("—"))
         .pivot_table(index="_k", columns="status", values="id", aggfunc="count", fill_value=0)
         .reindex(columns=STATUSES, fill_value=0))
    g["Leads"] = g.sum(axis=1)
    decided = g["Converted"] + g["Did Not Convert"]
    g["Share"] = g["Leads"] / g["Leads"].sum() * 100
    g["Conversion %"] = (g["Converted"] / g["Leads"] * 100).round(1)
    g["Win rate %"] = (g["Converted"] / decided.where(decided > 0) * 100).round(1)
    g = g.sort_values("Leads", ascending=False).reset_index().rename(columns={"_k": label})
    return g[[label, "Leads", "Share", *STATUSES, "Conversion %", "Win rate %"]]


_PCT_COLS = {
    "Share": st.column_config.NumberColumn("Share", format="%.1f%%"),
    "Conversion %": st.column_config.ProgressColumn("Conversion %", format="%.1f%%", min_value=0, max_value=100),
    "Win rate %": st.column_config.NumberColumn("Win rate %", format="%.1f%%",
                                                help="Converted ÷ (Converted + Did Not Convert)"),
}


def _show_analysis(PLOT):
    df = get_leads()
    if df.empty:
        st.info("No leads yet — analysis appears once leads are added.")
        return
    today = _today()
    start, end = quick_date_range_filter("lead_an", default_start=today.replace(month=4, day=1)
                                         if today.month >= 4 else today.replace(year=today.year - 1, month=4, day=1))
    d = _in_range(df, "lead_date", start, end).copy()
    if d.empty:
        st.info("No leads in this date range.")
        return
    d["status"] = d["status"].where(d["status"].isin(STATUSES), "Fresh")
    d["source_group"] = d["source"].map(_source_group)
    d["city"] = d["city"].fillna("—") if "city" in d else "—"

    n = len(d)
    c = d["status"].value_counts()
    conv, lost = int(c.get("Converted", 0)), int(c.get("Did Not Convert", 0))
    open_ = int(c.get("Fresh", 0) + c.get("Negotiation", 0))

    k = st.columns(6)
    k[0].metric("Total leads", n)
    k[1].metric("Open", open_, help="Fresh + Negotiation — still in play")
    k[2].metric("Converted", conv)
    k[3].metric("Did not convert", lost)
    k[4].metric("Conversion ratio", _pct(conv, n), help="Converted ÷ all leads")
    k[5].metric("Win rate", _pct(conv, conv + lost),
                help="Converted ÷ leads that reached a decision (Converted + Did Not Convert). "
                     "Ignores leads still open, so it isn't dragged down by fresh enquiries.")
    won_value = pd.to_numeric(d.loc[d["status"] == "Converted", "order_value"], errors="coerce").sum()
    st.caption(f"{start:%d %b %Y} – {end:%d %b %Y} · by lead date"
               + (f" · converted order value ₹{won_value / 1e5:,.2f}L" if won_value else ""))

    # Status mix + leads by source
    a, b = st.columns([1, 1.6])
    with a:
        sc = c.reindex(STATUSES, fill_value=0)
        fig = go.Figure(go.Pie(labels=sc.index, values=sc.values, hole=0.55, sort=False,
                               marker_colors=[_STATUS_COLOR[s] for s in sc.index], textinfo="value+percent"))
        fig.update_layout(**PLOT, title="Status mix", height=320, showlegend=True,
                          legend=dict(orientation="h", y=-0.1))
        st.plotly_chart(fig, use_container_width=True)
    with b:
        sg = d.groupby(["source_group", "status"]).size().reset_index(name="Leads")
        fig = px.bar(sg, x="source_group", y="Leads", color="status", category_orders={
            "status": STATUSES, "source_group": SOURCES}, color_discrete_map=_STATUS_COLOR)
        fig.update_layout(**PLOT, title="Leads by source", height=320, xaxis_title=None,
                          legend_title=None, legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("##### Source performance")
    st.dataframe(_breakdown(d, "source_group", "Source"), hide_index=True,
                 use_container_width=True, column_config=_PCT_COLS)
    others = d[d["source_group"] == "Others"]
    if not others.empty:
        with st.expander(f"What 'Others' was ({len(others)} leads)"):
            st.dataframe(_breakdown(others.assign(
                source=others["source"].fillna("Others").str.replace(r"^Others:\s*", "", regex=True)),
                "source", "Typed source"), hide_index=True, use_container_width=True, column_config=_PCT_COLS)

    # Month-wise: leads received and how many of them converted
    st.markdown("##### Month-wise")
    d["month"] = pd.to_datetime(d["lead_date"]).dt.to_period("M").dt.to_timestamp()
    mt = (d.pivot_table(index="month", columns="status", values="id", aggfunc="count", fill_value=0)
          .reindex(columns=STATUSES, fill_value=0))
    mt["Leads"] = mt.sum(axis=1)
    mt["Conversion %"] = (mt["Converted"] / mt["Leads"] * 100).round(1)
    fig = go.Figure()
    fig.add_bar(x=mt.index, y=mt["Leads"], name="Leads", marker_color="#5B8DEF")
    fig.add_bar(x=mt.index, y=mt["Converted"], name="Converted", marker_color="#27AE60")
    fig.add_scatter(x=mt.index, y=mt["Conversion %"], name="Conversion %", yaxis="y2",
                    mode="lines+markers+text", text=[f"{v:.0f}%" for v in mt["Conversion %"]],
                    textposition="top center", line_color="#D4A011")
    fig.update_layout(**PLOT, height=340, barmode="group", xaxis=dict(tickformat="%b %Y", dtick="M1"),
                      yaxis=dict(title="Leads"), yaxis2=dict(title="Conversion %", overlaying="y",
                                                             side="right", range=[0, 105], showgrid=False),
                      legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Leads are counted in the month they came in; a lead received in June and converted "
               "in August counts toward June.")

    st.markdown("##### By plant")
    st.dataframe(_breakdown(d.assign(_plant=d["product"].map(_plant)), "_plant", "Plant"),
                 hide_index=True, use_container_width=True, column_config=_PCT_COLS)

    p, q = st.columns(2)
    with p:
        st.markdown("##### By product")
        st.dataframe(_breakdown(d, "product", "Product")
                     [["Product", "Leads", "Converted", "Did Not Convert", "Conversion %", "Win rate %"]],
                     hide_index=True, use_container_width=True, column_config=_PCT_COLS)
    with q:
        st.markdown("##### By salesperson (assigned to)")
        st.dataframe(_breakdown(d.assign(_owner=d["owner"].map(owner_name)), "_owner", "Salesperson")
                     [["Salesperson", "Leads", "Converted", "Did Not Convert", "Conversion %", "Win rate %"]],
                     hide_index=True, use_container_width=True, column_config=_PCT_COLS)

    if d["city"].nunique() > 1:
        with st.expander("📍 By city / area"):
            st.dataframe(_breakdown(d, "city", "City"), hide_index=True, use_container_width=True,
                         column_config=_PCT_COLS)

    lost_df = d[d["status"] == "Did Not Convert"]
    if not lost_df.empty:
        st.markdown("##### ❌ Why leads did not convert")
        lr = lost_df["lost_reason"].fillna("Not recorded").value_counts().reset_index()
        lr.columns = ["Reason", "Leads"]
        fig = px.bar(lr, x="Leads", y="Reason", orientation="h", text="Leads",
                     color_discrete_sequence=[_STATUS_COLOR["Did Not Convert"]])
        fig.update_layout(**PLOT, height=60 + 40 * len(lr), yaxis=dict(autorange="reversed", title=None))
        st.plotly_chart(fig, use_container_width=True)
        with st.expander(f"Leads that did not convert ({len(lost_df)})"):
            st.dataframe(_to_sheet(lost_df.sort_values("lead_date", ascending=False), LEAD_COLS)
                         [["Date", "Source", "Company", "Name", "Product", "Assigned To", "Lost Reason",
                           "Remarks"]],
                         hide_index=True, use_container_width=True)


# ── Sales visits ──────────────────────────────────────────────────────────────
def _add_visit_form(role):
    open_leads = _scope_leads(get_leads(), role)
    open_leads = open_leads[open_leads["status"].isin(OPEN_STATUSES)] if not open_leads.empty else open_leads
    lead_opts = {}
    for _, r in open_leads.iterrows():
        r = {k: (None if not isinstance(v, (list, dict)) and pd.isna(v) else v) for k, v in r.items()}
        lead_opts[int(r["id"])] = " · ".join(x for x in [r.get("company") or r.get("contact_name"),
                                                         r.get("phone")] if x) or f"Lead #{int(r['id'])}"
    with st.expander("➕ Log a visit"):
        with st.form("visit_add_form", clear_on_submit=True):
            lead_id = st.selectbox("Visit for an existing lead? (optional)", [None] + list(lead_opts),
                                   format_func=lambda i: "— Not linked to a lead" if i is None else lead_opts[i])
            c1, c2, c3 = st.columns(3)
            vdate = c1.date_input("Visit date", value=_today())
            by = c2.text_input("Visited by", value=_me(), disabled=not _sees_all(role))
            place = c3.text_input("Place / area / site", placeholder="e.g. Ranchi – Kanke Road site")
            c4, c5, c6 = st.columns(3)
            company = c4.text_input("Company / party")
            met = c5.text_input("Person met")
            phone = c6.text_input("Phone")
            c7, c8 = st.columns(2)
            purpose = c7.selectbox("Purpose", PURPOSES)
            nxt = c8.date_input("Next visit / follow-up (optional)", value=None)
            discussion = st.text_area("What was discussed", height=80)
            remarks = st.text_input("Outcome / remarks")
            make_lead = st.checkbox("Not linked above — also add this as a new lead (source Others: Site Visit)",
                                    disabled=not can(role, "leads", "add"))
            ok = st.form_submit_button("Save Visit", type="primary")
        if ok:
            if not place.strip():
                st.error("Enter where the visit was.")
                return
            insert_sales_row("sales_visits", {
                "visit_date": str(vdate), "visited_by": by.strip() or _me(), "place": place.strip(),
                "company": company.strip() or None, "contact_name": met.strip() or None,
                "phone": phone.strip() or None, "purpose": purpose,
                "discussion": discussion.strip() or None, "remarks": remarks.strip() or None,
                "next_visit_date": str(nxt) if nxt else None, "created_by": _me(), "lead_id": lead_id,
            })
            note = None
            if lead_id is not None:
                lead = open_leads.set_index("id").loc[lead_id]
                log_update(lead_id, "Visit", " — ".join(x for x in [f"Visit at {place.strip()}",
                                                                   discussion.strip(), remarks.strip()] if x),
                           lead["status"], nxt or (_today() + timedelta(days=1)), me=_me())
                note = "Logged on the lead's history."
            elif make_lead:
                insert_sales_row("leads", new_lead_row(
                    lead_date=vdate, source="Others: Site Visit",
                    owner=_my_username() if not _sees_all(role) else next_owner(),
                    contact_name=met.strip(), phone=phone.strip(), company=company.strip(),
                    city=place.strip(), discussion=discussion.strip(),
                    remarks=f"From visit at {place.strip()}", created_by=_me()))
                note = "Also added as a new lead."
            flash(f"✅ Visit to {place.strip()} saved", note)
            st.rerun()


def _show_visits(role):
    df = _scope_visits(get_sales_visits(), role)
    today = _today()
    if can(role, "leads", "add"):
        _add_visit_form(role)
    if df.empty:
        st.info("No visits logged yet.")
        return

    nv = pd.to_datetime(df["next_visit_date"], errors="coerce")
    upcoming = df[nv.notna() & (nv >= pd.Timestamp(today)) & (nv <= pd.Timestamp(today) + pd.Timedelta(days=7))]
    if not upcoming.empty:
        with st.expander(f"📅 {len(upcoming)} visit{'s' if len(upcoming) != 1 else ''} planned in the next 7 days"):
            st.dataframe(_to_sheet(upcoming.sort_values("next_visit_date"), VISIT_COLS)
                         [["Next Visit", "Place", "Company", "Met", "Phone", "Visited By"]],
                         hide_index=True, use_container_width=True)

    st.markdown("##### Visit sheet")
    start, end = quick_date_range_filter("visits", default_start=today.replace(day=1))
    f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.6, 1.2])
    by_pick = f1.multiselect("Visited by", sorted(df["visited_by"].dropna().unique()), key="vis_f_by")
    pur_pick = f2.multiselect("Purpose", PURPOSES, key="vis_f_pur")
    q = f3.text_input("Search place / company / person", key="vis_f_q")
    sort = f4.selectbox("Sort by", ["Newest first", "Oldest first", "Place A–Z", "Company A–Z",
                                    "Visited by", "Next visit"], key="vis_f_sort")

    view = _in_range(df, "visit_date", start, end)
    if by_pick:
        view = view[view["visited_by"].isin(by_pick)]
    if pur_pick:
        view = view[view["purpose"].isin(pur_pick)]
    view = _search(view, q, ["place", "company", "contact_name", "phone", "discussion", "remarks"])

    m = st.columns(4)
    m[0].metric("Visits", len(view))
    m[1].metric("Places", view["place"].nunique())
    m[2].metric("Companies", view["company"].nunique())
    m[3].metric("People visiting", view["visited_by"].nunique())

    if sort == "Newest first":
        view = view.sort_values(["visit_date", "id"], ascending=False)
    elif sort == "Oldest first":
        view = view.sort_values(["visit_date", "id"])
    elif sort == "Place A–Z":
        view = view.sort_values("place", key=lambda s: s.fillna("").str.lower())
    elif sort == "Company A–Z":
        view = view.sort_values("company", key=lambda s: s.fillna("").str.lower())
    elif sort == "Visited by":
        view = view.sort_values(["visited_by", "visit_date"], ascending=[True, False])
    else:
        view = view.sort_values("next_visit_date", na_position="last")

    _sheet("sales_visits", view, VISIT_COLS, role, {
        "ID": st.column_config.NumberColumn("ID", disabled=True, width="small"),
        "Date": st.column_config.DateColumn("Date", format="DD MMM YYYY", required=True),
        "Purpose": st.column_config.SelectboxColumn("Purpose", options=PURPOSES),
        "Next Visit": st.column_config.DateColumn("Next Visit", format="DD MMM YYYY"),
        "Discussed": st.column_config.TextColumn("Discussed", width="large"),
        "Added By": st.column_config.TextColumn("Added By", disabled=True),
    }, label=lambda r: f"#{int(r['id'])} · {r['visit_date']} · {r.get('place') or ''}")

    if not view.empty:
        with st.expander("📍 Summary by place and by person"):
            a, b = st.columns(2)
            a.dataframe(view.groupby("place").agg(Visits=("id", "count"), Last=("visit_date", "max"))
                        .sort_values("Visits", ascending=False).reset_index()
                        .rename(columns={"place": "Place", "Last": "Last visit"}),
                        hide_index=True, use_container_width=True)
            b.dataframe(view.groupby("visited_by").agg(Visits=("id", "count"), Places=("place", "nunique"))
                        .sort_values("Visits", ascending=False).reset_index()
                        .rename(columns={"visited_by": "Visited By"}),
                        hide_index=True, use_container_width=True)


# ── Import & settings ─────────────────────────────────────────────────────────
def _show_import(role):
    users = crm_users()
    pool = salespeople()

    st.markdown("##### ⚙️ Who gets new leads")
    st.caption("Used for IndiaMART leads, imports and any lead added with “Auto-assign”.")
    mode = get_crm_setting("assign_mode") or "round_robin"
    opts = ["round_robin"] + list(users)
    pick = st.selectbox("New leads go to", opts, index=opts.index(mode) if mode in opts else 0,
                        format_func=lambda u: f"🔄 Round robin — {', '.join(pool.values()) or 'nobody yet'}"
                        if u == "round_robin" else users[u], key="crm_assign_mode",
                        disabled=not can(role, "leads", "edit"))
    if pick != mode and st.button("Save", key="crm_assign_save"):
        set_crm_setting("assign_mode", pick)
        flash("✅ Auto-assign updated")
        st.rerun()

    st.markdown("##### 🔌 IndiaMART live sync")
    if indiamart_key():
        last = get_crm_setting("im_last_end")
        st.success(f"Connected — new IndiaMART enquiries come in automatically (checked every 10 min while "
                   f"anyone has Leads open). Last synced up to: {last or 'never'}.")
        if st.button("🔄 Sync now", key="crm_im_sync"):
            added, msg = sync_indiamart(force=True, created_by=_me())
            flash(("✅ " if added else "ℹ️ ") + msg)
            st.rerun()
    else:
        st.info("Not connected. In IndiaMART Seller panel → Lead Manager → Import/Export → **CRM API key**, "
                "generate the key, then add it to the app's Streamlit secrets as `INDIAMART_CRM_KEY = \"…\"`.")

    st.markdown("##### 📥 Import leads from Excel / CSV")
    st.caption("Works with IndiaMART Lead Manager exports, Meta lead-form downloads, or any sheet with a "
               "name/phone column. Numbers already in Leads are skipped.")
    up = st.file_uploader("Choose a file", type=["xlsx", "xls", "csv"], key="crm_import_file")
    if up is None:
        return
    try:
        raw = pd.read_csv(up) if up.name.lower().endswith(".csv") else pd.read_excel(up)
    except Exception as e:
        st.error(f"Couldn't read that file: {e}")
        return
    raw = raw.dropna(how="all")
    st.caption(f"{len(raw)} rows found.")
    cols = ["—"] + [str(c) for c in raw.columns]
    labels = {"contact_name": "Name", "phone": "Phone", "company": "Company", "city": "City",
              "email": "Email", "product": "Product / requirement", "discussion": "Message / notes",
              "lead_date": "Date"}
    mapping = {}
    grid = st.columns(4)
    for i, f in enumerate(IMPORT_FIELDS):
        g = guess_column(raw.columns, f)
        mapping[f] = grid[i % 4].selectbox(labels[f], cols, index=cols.index(g) if g in cols else 0,
                                           key=f"crm_map_{f}")
    c1, c2, c3 = st.columns(3)
    source = c1.selectbox("Source for these leads", SOURCES, key="crm_imp_src")
    other = c2.text_input("If Others — type source", key="crm_imp_other")
    assign = c3.selectbox("Assign to", ["__auto__"] + list(users), key="crm_imp_owner",
                          format_func=lambda u: "⚙️ Auto-assign (setting above)" if u == "__auto__" else users[u])

    def val(row, f):
        c = mapping[f]
        if c == "—":
            return None
        v = row.get(c)
        return None if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v).strip() or None

    existing = get_leads()
    seen = set(existing["phone"].map(phone_key)) if not existing.empty else set()
    rows, skipped = [], 0
    for _, r in raw.iterrows():
        r = r.to_dict()
        ph = val(r, "phone")
        if ph and ph.endswith(".0"):
            ph = ph[:-2]          # Excel numbers come through as floats
        if not (val(r, "contact_name") or ph or val(r, "company")):
            continue
        if ph and phone_key(ph) in seen:
            skipped += 1
            continue
        seen.add(phone_key(ph))
        d = pd.to_datetime(val(r, "lead_date"), errors="coerce", dayfirst=True)
        rows.append(dict(contact_name=val(r, "contact_name"), phone=ph, company=val(r, "company"),
                         city=val(r, "city"), email=val(r, "email"), product=val(r, "product"),
                         discussion=val(r, "discussion"),
                         lead_date=(d.date() if pd.notna(d) else _today())))
    st.dataframe(pd.DataFrame(rows).head(20), hide_index=True, use_container_width=True)
    st.caption(f"Ready to import **{len(rows)}** lead(s)" + (f" · {skipped} skipped (phone already in Leads)"
                                                           if skipped else "") + ". Showing the first 20.")
    if rows and st.button(f"📥 Import {len(rows)} leads", type="primary", key="crm_imp_go"):
        if source == "Others" and not other.strip():
            st.error("Source is Others — type what the source was.")
            return
        src = f"Others: {other.strip()}" if source == "Others" else source
        for x in rows:
            insert_sales_row("leads", new_lead_row(
                **x, source=src, owner=next_owner() if assign == "__auto__" else assign, created_by=_me()))
        flash(f"📥 {len(rows)} leads imported", f"{skipped} duplicates skipped." if skipped else None)
        st.rerun()


# ── Shared sheet: editable grid + save, export, delete ────────────────────────
def _sheet(table, view, cols, role, col_config, label):
    if view.empty:
        st.info("Nothing matches these filters.")
        return
    sheet = _to_sheet(view, cols)
    editable = can(role, "leads", "edit")
    ver_key = f"{table}_sheet_ver"
    ver = st.session_state.setdefault(ver_key, 0)
    if editable:
        st.caption("✏️ Click any cell to edit, then press **Save changes**.")
        edited = st.data_editor(sheet, hide_index=True, use_container_width=True, num_rows="fixed",
                                column_config=col_config, key=f"{table}_editor_{ver}")
        b1, b2, _ = st.columns([1, 1, 3])
        if b1.button("💾 Save changes", type="primary", key=f"{table}_save"):
            n = _save_sheet_edits(table, sheet, edited, cols)
            st.session_state[ver_key] = ver + 1
            flash(f"💾 {n} row{'s' if n != 1 else ''} updated" if n else "No changes to save")
            st.rerun()
        if b2.button("↩️ Discard edits", key=f"{table}_discard"):
            st.session_state[ver_key] = ver + 1
            st.rerun()
    else:
        st.dataframe(sheet, hide_index=True, use_container_width=True, column_config=col_config)

    st.download_button("⬇️ Export to Excel", _to_excel_bytes(sheet, "Leads" if table == "leads" else "Visits"),
                       file_name=f"{table}_{_today()}.xlsx", key=f"{table}_xlsx")

    if can(role, "leads", "delete"):
        with st.expander("🗑️ Delete a row"):
            by_id = {int(r["id"]): r for _, r in view.iterrows()}
            rid = st.selectbox("Row", list(by_id), format_func=lambda i: label(by_id[i]), key=f"{table}_del_pick")
            ok = st.checkbox("Yes, delete it permanently", key=f"{table}_del_ok")
            if st.button("Delete", disabled=not ok, key=f"{table}_del_btn"):
                delete_row(table, rid)
                flash(f"🗑️ {label(by_id[rid])} deleted")
                st.rerun()


# ── Page ──────────────────────────────────────────────────────────────────────
def show(PLOT):
    show_flashes()
    role = st.session_state.get("role")
    st.markdown("""
    <div class="page-title">🎯 Leads & Sales Visits</div>
    <div class="page-subtitle">Follow up every enquiry on time · track every visit · see what converts and why.</div>
    """, unsafe_allow_html=True)

    try:
        get_leads(); get_sales_visits(); get_lead_activities()
    except Exception as e:
        st.error(f"Leads tables aren't set up yet ({e}). Run the Leads migration at the bottom of "
                 "supabase_schema.sql in the Supabase SQL Editor.")
        return

    # IndiaMART: pull new enquiries (rate-limited inside; silent unless something arrived)
    if indiamart_key():
        try:
            added, _msg = sync_indiamart()
            if added:
                st.toast(f"📥 {added} new IndiaMART lead{'s' if added != 1 else ''}")
        except Exception:
            pass

    from views.lead_desk import show_my_day, show_team
    t_imp = None
    if _sees_all(role):
        tabs = ["📱 My Day", "🎯 Leads", "📊 Analysis", "🏆 Team", "🚗 Sales Visits"]
        if can(role, "leads", "add"):
            tabs.append("📥 Import & Settings")
        got = st.tabs(tabs)
        t_day, t_leads, t_an, t_team, t_visits = got[:5]
        t_imp = got[5] if len(got) > 5 else None
    else:
        t_day, t_leads, t_visits = st.tabs(["📱 My Day", "🎯 My Leads", "🚗 My Visits"])
        t_an = t_team = None
    with t_day:
        show_my_day(role, get_order_products())
    with t_leads:
        _show_leads(role)
    if t_an is not None:
        with t_an:
            _show_analysis(PLOT)
        with t_team:
            show_team()
    with t_visits:
        _show_visits(role)
    if t_imp is not None:
        with t_imp:
            _show_import(role)
