"""Lead desk — the phone-first side of Leads & Visits.

  * My Day — what a salesperson works from each morning: every open lead whose
             next follow-up is overdue, due today, or coming up, as cards with
             one-tap Call / WhatsApp (ready-made messages), "No answer — retry
             tomorrow", an Update form (what happened, status, next follow-up)
             and Edit details. Hot leads float to the top of each bucket.
  * Team   — manager view: per-salesperson workload, follow-up discipline and
             results, stale leads, and the latest updates.

Every update is written to `lead_activities` (one row per call / WhatsApp /
visit / note) so each lead carries its own history, and stamps the lead's
`follow_up_date` (next follow-up) and `last_contact_at`. Ownership is by login
username (`leads.owner`, see core/crm.py); role "sales" only sees its own.
"""
import re
from datetime import timedelta

import pandas as pd
import streamlit as st

from core.crm import (PRIORITIES, DEFAULT_PRIORITY, WA_TEMPLATES, wa_link, now_str, owner_name,
                      crm_users, phone_key, can)
from core.db import get_leads, get_lead_activities, insert_lead_activity, update_sales_row
from core.ui import flash

ACTIVITY_KINDS = ["Call", "WhatsApp", "Visit", "Meeting", "Note"]
LOST_REASONS = ["Price too high", "Bought from competitor", "Distance / delivery",
                "Not responding", "Requirement cancelled / postponed", "Product not available", "Other"]
CARDS_PER_BUCKET = 15
_PRIO_ORDER = {p: i for i, p in enumerate(PRIORITIES)}


def due_dates(df):
    """Next follow-up per lead; a lead without one is due the day after it came in."""
    fu = pd.to_datetime(df["follow_up_date"], errors="coerce")
    first = pd.to_datetime(df["lead_date"], errors="coerce") + pd.Timedelta(days=1)
    return fu.fillna(first).dt.normalize()


def _when(days):
    if days < 0:
        return f"🔴 {-days} day{'s' if days != -1 else ''} overdue"
    if days == 0:
        return "🟠 Due today"
    if days == 1:
        return "📅 Tomorrow"
    return f"📅 In {days} days"


def _ago(ts, today):
    t = pd.to_datetime(ts, errors="coerce")
    if pd.isna(t):
        return "never contacted"
    d = (pd.Timestamp(today) - t.normalize()).days
    return "contacted today" if d <= 0 else ("contacted yesterday" if d == 1 else f"last contact {d} days ago")


def log_update(lead_id, kind, notes, status, next_fu, lost_reason=None, order_value=None, me=""):
    """One activity row + the lead's status / next follow-up / last contact."""
    closed = status not in ("Fresh", "Negotiation")
    now = now_str()
    insert_lead_activity({
        "lead_id": int(lead_id), "activity_at": now, "kind": kind,
        "notes": (notes or "").strip() or None, "status_after": status,
        "next_follow_up": None if closed or not next_fu else str(next_fu), "created_by": me,
    })
    changes = {"status": status, "last_contact_at": now, "updated_at": now,
               "follow_up_date": None if closed or not next_fu else str(next_fu)}
    if status == "Did Not Convert":
        changes["lost_reason"] = lost_reason
    if status == "Converted" and order_value:
        changes["order_value"] = float(order_value)
    update_sales_row("leads", int(lead_id), changes)


# ── One lead as a card ────────────────────────────────────────────────────────
def _card(lead, due, acts, role, show_owner, key, products):
    from views.leads import STATUSES, _today, _me, _sees_all
    lead = {k: (None if not isinstance(v, (list, dict)) and pd.isna(v) else v) for k, v in lead.items()}
    lid = int(lead["id"])
    title = lead.get("company") or lead.get("contact_name") or lead.get("phone") or f"Lead #{lid}"
    who = lead.get("contact_name") if lead.get("company") else None
    today = _today()
    days = (due - pd.Timestamp(today)).days if pd.notna(due) else 0
    can_edit = can(role, "leads", "edit")
    status = lead.get("status") if lead.get("status") in STATUSES else "Fresh"
    is_open = status in ("Fresh", "Negotiation")

    with st.container(border=True):
        meta = [lead.get("priority") or DEFAULT_PRIORITY, status, lead.get("product") or "product —",
                lead.get("city"), lead.get("source")]
        if show_owner:
            meta.append(f"👤 {owner_name(lead.get('owner'))}")
        st.markdown(f"**{title}**" + (f" · {who}" if who else "")
                    + "  \n<span style='font-size:0.82rem;opacity:0.8'>"
                    + " · ".join(str(m) for m in meta if m)
                    + (f"<br>{_when(days)} · " if is_open else "<br>")
                    + _ago(lead.get("last_contact_at"), today) + "</span>",
                    unsafe_allow_html=True)
        last = acts.iloc[0] if not acts.empty else None
        if last is not None and pd.notna(last.get("notes")) and last.get("notes"):
            st.caption(f"Last: {last['kind']} — {last['notes']}")
        elif lead.get("discussion"):
            st.caption(f"Enquiry: {lead['discussion'][:220]}")

        phone = str(lead.get("phone") or "").strip()
        b1, b2, b3 = st.columns(3)
        if phone:
            b1.link_button("📞 Call", f"tel:{re.sub(r'[^0-9+]', '', phone)}", use_container_width=True)
            if wa_link(phone, "👋 Intro", lead, _me()):
                with b2.popover("💬 WhatsApp", use_container_width=True):
                    for t in WA_TEMPLATES:
                        st.link_button(t, wa_link(phone, t, lead, _me()), use_container_width=True)
        else:
            b1.caption("No phone number")
        if can_edit and is_open and b3.button("📵 No answer · tomorrow", key=f"{key}_na",
                                              use_container_width=True):
            log_update(lid, "Call", "No answer", status, today + timedelta(days=1), me=_me())
            flash(f"📵 {title} — retry tomorrow")
            st.rerun()

        if can_edit:
            with st.expander("✍️ Update — what happened?"):
                with st.form(f"{key}_form"):
                    c1, c2 = st.columns(2)
                    kind = c1.selectbox("How", ACTIVITY_KINDS)
                    new_status = c2.selectbox("Status", STATUSES, index=STATUSES.index(status))
                    notes = st.text_area("What happened", height=70,
                                         placeholder="e.g. Wants rate for 5000 pavers, call after site visit")
                    c3, c4 = st.columns(2)
                    next_fu = c3.date_input("Next follow-up", value=today + timedelta(days=1),
                                            help="Ignored if the lead is Converted or Did Not Convert")
                    lost = c4.selectbox("If Did Not Convert — why?", ["—"] + LOST_REASONS)
                    value = st.number_input("If Converted — order value (₹, optional)", min_value=0.0,
                                            step=1000.0, format="%.0f")
                    ok = st.form_submit_button("Save update", type="primary", use_container_width=True)
                if ok:
                    if new_status == "Did Not Convert" and lost == "—":
                        st.error("Pick why it did not convert.")
                    elif new_status in ("Fresh", "Negotiation") and next_fu < today:
                        st.error("Next follow-up can't be in the past.")
                    else:
                        log_update(lid, kind, notes, new_status, next_fu,
                                   None if lost == "—" else lost, value or None, me=_me())
                        flash(f"✅ {title} updated" + (f" — next follow-up {next_fu:%d %b}"
                                                     if new_status in ("Fresh", "Negotiation") else f" — {new_status}"))
                        st.rerun()

            with st.expander("✏️ Edit details"):
                with st.form(f"{key}_edit"):
                    e1, e2 = st.columns(2)
                    name = e1.text_input("Contact name", value=lead.get("contact_name") or "")
                    ph = e2.text_input("Phone", value=lead.get("phone") or "")
                    company = e1.text_input("Company", value=lead.get("company") or "")
                    city = e2.text_input("City / site", value=lead.get("city") or "")
                    prods = ["—"] + products + ["Multiple / Other"]
                    cur_p = lead.get("product") if lead.get("product") in prods else "—"
                    product = e1.selectbox("Product", prods, index=prods.index(cur_p))
                    prio = lead.get("priority") if lead.get("priority") in PRIORITIES else DEFAULT_PRIORITY
                    priority = e2.selectbox("Priority", PRIORITIES, index=PRIORITIES.index(prio))
                    owner = lead.get("owner")
                    if _sees_all(role):
                        users = crm_users()
                        opts = list(users) + ([owner] if owner and owner not in users else [])
                        owner = st.selectbox("Assigned to", [None] + opts,
                                             index=([None] + opts).index(owner) if owner in opts else 0,
                                             format_func=lambda u: "— Unassigned" if u is None else owner_name(u))
                    if st.form_submit_button("Save details", use_container_width=True):
                        changes = {"contact_name": name.strip() or None, "phone": ph.strip() or None,
                                   "company": company.strip() or None, "city": city.strip() or None,
                                   "product": None if product == "—" else product, "priority": priority,
                                   "owner": owner, "assigned_to": owner_name(owner) if owner else None,
                                   "updated_at": now_str()}
                        changes = {k: v for k, v in changes.items()
                                   if k == "updated_at" or v != lead.get(k)}
                        if len(changes) > 1:
                            update_sales_row("leads", lid, changes)
                            if "owner" in changes:
                                insert_lead_activity({"lead_id": lid, "activity_at": now_str(), "kind": "Note",
                                                      "notes": f"Reassigned to {owner_name(owner)}",
                                                      "status_after": status, "created_by": _me()})
                            flash(f"✏️ {title} — details saved")
                        st.rerun()

        if not acts.empty:
            with st.expander(f"🕘 History ({len(acts)})"):
                for _, a in acts.iterrows():
                    t = pd.to_datetime(a["activity_at"], errors="coerce")
                    st.markdown(f"**{t:%d %b, %I:%M %p}** · {a['kind']} · {a.get('created_by') or ''}"
                                f" → *{a.get('status_after') or ''}*  \n{a.get('notes') or ''}")


def render_cards(frame, acts, role, show_owner, key, products):
    acts_by = {k: g.sort_values("activity_at", ascending=False) for k, g in acts.groupby("lead_id")} \
        if not acts.empty else {}
    empty = pd.DataFrame(columns=["activity_at", "kind", "notes", "status_after", "created_by"])
    for _, r in frame.iterrows():
        _card(r, r["_due"], acts_by.get(r["id"], empty), role, show_owner, f"{key}_{int(r['id'])}", products)


# ── My Day ────────────────────────────────────────────────────────────────────
def show_my_day(role, products):
    from views.leads import OPEN_STATUSES, _today, _scope_leads, _sees_all, _add_lead_form, _search
    today = pd.Timestamp(_today())
    all_leads = get_leads()
    df = _scope_leads(all_leads, role)
    acts = get_lead_activities()
    show_owner = _sees_all(role)

    if can(role, "leads", "add"):
        _add_lead_form(products, all_leads, role, key="myday")

    if show_owner and not df.empty:
        users = crm_users()
        owners = sorted(set(df["owner"].dropna()) | set(users), key=owner_name)
        pick = st.selectbox("Salesperson", ["__all__", "__none__"] + owners, key="myday_who",
                            format_func=lambda u: {"__all__": "Everyone", "__none__": "— Unassigned"}.get(
                                u, owner_name(u)))
        if pick == "__none__":
            df = df[df["owner"].isna()]
        elif pick != "__all__":
            df = df[df["owner"] == pick]

    df = df.assign(_due=due_dates(df)) if not df.empty else df.assign(_due=pd.Series(dtype="datetime64[ns]"))
    open_ = df[df["status"].isin(OPEN_STATUSES)].copy()
    open_["_p"] = open_["priority"].map(_PRIO_ORDER).fillna(1)
    overdue = open_[open_["_due"] < today]
    due_today = open_[open_["_due"] == today]
    upcoming = open_[(open_["_due"] > today) & (open_["_due"] <= today + pd.Timedelta(days=7))]
    later = open_[open_["_due"] > today + pd.Timedelta(days=7)]

    updates_today = 0
    if not acts.empty and not df.empty:
        mine = acts[acts["lead_id"].isin(df["id"])]
        updates_today = int((pd.to_datetime(mine["activity_at"], errors="coerce").dt.normalize() == today).sum())
    won_month = df[(df["status"] == "Converted")
                   & (pd.to_datetime(df["updated_at"], errors="coerce") >= today.replace(day=1))]

    m = st.columns(5)
    m[0].metric("🔴 Overdue", len(overdue))
    m[1].metric("🟠 Due today", len(due_today))
    m[2].metric("📅 Next 7 days", len(upcoming))
    m[3].metric("✅ Updates today", updates_today)
    m[4].metric("🏆 Won this month", len(won_month))

    q = st.text_input("🔍 Find any lead — name, phone or company", key="myday_q")
    if q.strip():
        hits = _search(df, q, ["contact_name", "phone", "company", "product", "city", "discussion", "remarks"])
        if phone_key(q) and len(phone_key(q)) >= 6:
            hits = pd.concat([hits, df[df["phone"].map(phone_key).str.contains(phone_key(q), na=False)]]
                             ).drop_duplicates("id")
        st.markdown(f"##### {len(hits)} match{'es' if len(hits) != 1 else ''}")
        render_cards(hits.head(30), acts, role, show_owner, "mdq", products)
        return

    if open_.empty:
        st.success("🎉 Nothing to follow up — no open leads here." if not df.empty else "No leads assigned yet.")
        return

    def bucket(frame, label, key, expanded):
        if frame.empty:
            return
        frame = frame.sort_values(["_p", "_due", "lead_date"])
        with st.expander(f"{label} ({len(frame)})", expanded=expanded):
            show_all = len(frame) > CARDS_PER_BUCKET and st.toggle(f"Show all {len(frame)}", key=f"md_all_{key}")
            render_cards(frame if show_all else frame.head(CARDS_PER_BUCKET), acts, role, show_owner,
                         f"md_{key}", products)

    bucket(overdue, "🔴 Overdue", "over", True)
    bucket(due_today, "🟠 Due today", "today", True)
    bucket(upcoming, "📅 Coming up — next 7 days", "up", False)
    bucket(later, "🗓️ Later", "later", False)


# ── Team (manager) ────────────────────────────────────────────────────────────
def show_team():
    from views.leads import OPEN_STATUSES, _today, _pct
    df = get_leads()
    if df.empty:
        st.info("No leads yet.")
        return
    acts = get_lead_activities()
    today = pd.Timestamp(_today())
    df = df.copy()
    df["_due"] = due_dates(df)
    df["who"] = df["owner"].map(owner_name)
    is_open = df["status"].isin(OPEN_STATUSES)

    if not acts.empty:
        acts = acts.copy()
        acts["_t"] = pd.to_datetime(acts["activity_at"], errors="coerce")
        acts = acts.merge(df[["id", "who"]].rename(columns={"id": "lead_id"}), on="lead_id", how="inner")
    week_ago = today - pd.Timedelta(days=6)
    month_start = today.replace(day=1)

    rows = []
    for who, g in df.groupby("who"):
        o = g[g["status"].isin(OPEN_STATUSES)]
        a = acts[acts["who"] == who] if not acts.empty else acts
        conv = int((g["status"] == "Converted").sum())
        lost = int((g["status"] == "Did Not Convert").sum())
        resp = pd.Series(dtype=float)
        if not a.empty:   # first response: lead entered -> first logged activity
            first = a.groupby("lead_id")["_t"].min()
            created = pd.to_datetime(g.set_index("id")["created_at"], errors="coerce", utc=True) \
                .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            resp = ((first - created.reindex(first.index)).dt.total_seconds() / 3600).dropna()
            resp = resp[resp >= 0]
        won_val = pd.to_numeric(g.loc[g["status"] == "Converted", "order_value"], errors="coerce").sum()
        rows.append({
            "Salesperson": who, "Open": len(o),
            "Overdue": int((o["_due"] < today).sum()), "Due today": int((o["_due"] == today).sum()),
            "Updates today": int((a["_t"].dt.normalize() == today).sum()) if not a.empty else 0,
            "Updates (7 days)": int((a["_t"] >= week_ago).sum()) if not a.empty else 0,
            "Never contacted": int(o["last_contact_at"].isna().sum()),
            "Won (month)": int(((g["status"] == "Converted") &
                                (pd.to_datetime(g["updated_at"], errors="coerce") >= month_start)).sum()),
            "Won": conv, "Lost": lost, "Win rate": _pct(conv, conv + lost),
            "Won value": won_val,
            "First response (hrs)": round(float(resp.median()), 1) if len(resp) else None,
        })
    team = pd.DataFrame(rows).sort_values(["Overdue", "Open"], ascending=False)
    st.markdown("##### Salesperson scorecard")
    st.caption("Overdue = open leads past their next follow-up. First response = median hours from a lead "
               "being entered to its first logged call / WhatsApp / visit.")
    st.dataframe(team, hide_index=True, use_container_width=True, column_config={
        "Overdue": st.column_config.NumberColumn("🔴 Overdue"),
        "Won value": st.column_config.NumberColumn("Won value", format="₹%.0f"),
        "First response (hrs)": st.column_config.NumberColumn("First response (hrs)", format="%.1f"),
    })

    stale_cut = today - pd.Timedelta(days=7)
    last = pd.to_datetime(df["last_contact_at"], errors="coerce").fillna(
        pd.to_datetime(df["lead_date"], errors="coerce"))
    stale = df[is_open & (last < stale_cut)].assign(_last=last)
    st.markdown(f"##### 🕸️ Stale leads — open, no contact in 7+ days ({len(stale)})")
    if stale.empty:
        st.success("None — every open lead has been touched in the last week.")
    else:
        st.dataframe(stale.sort_values("_last").assign(
            **{"Last contact": lambda d: d["_last"].dt.strftime("%d %b %Y"),
               "Next follow-up": lambda d: d["_due"].dt.strftime("%d %b %Y")})
            [["who", "company", "contact_name", "phone", "status", "Last contact", "Next follow-up"]]
            .rename(columns={"who": "Salesperson", "company": "Company", "contact_name": "Name",
                             "phone": "Phone", "status": "Status"}),
            hide_index=True, use_container_width=True)

    if not acts.empty:
        st.markdown("##### Latest updates")
        recent = acts.sort_values("_t", ascending=False).head(30).merge(
            df[["id", "company", "contact_name"]].rename(columns={"id": "lead_id"}), on="lead_id", how="left")
        st.dataframe(pd.DataFrame({
            "When": recent["_t"].dt.strftime("%d %b %I:%M %p"), "By": recent["created_by"],
            "Lead": recent["company"].fillna(recent["contact_name"]), "How": recent["kind"],
            "Status": recent["status_after"], "Notes": recent["notes"],
        }), hide_index=True, use_container_width=True)
