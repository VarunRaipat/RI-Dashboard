"""CRM plumbing behind Leads & Visits (ported from ECO360) — kept out of the views so the lead desk,
the sheet, imports and the daily report all agree on the same rules.

  * Ownership is by **login username** (`leads.owner`), never by display name,
    so renaming a staff login can't orphan their leads. `assigned_to` keeps the
    owner's display name purely for exports / the Business Assistant.
  * Salespeople = every login (Streamlit secrets [users]) allowed to view Leads,
    except Admin.
    New imported leads go round-robin among them (or to one fixed person —
    Admin ▸ Leads settings, stored in `crm_settings`).
  * IndiaMART: pulls enquiries from the IndiaMART Lead Manager CRM API when
    INDIAMART_CRM_KEY is in Streamlit secrets — at most once every 10 minutes,
    triggered by anyone opening the Leads page, deduped on UNIQUE_QUERY_ID.
"""
import re
from datetime import datetime, timedelta
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

from core.db import (get_leads, insert_sales_row, get_crm_setting, set_crm_setting)
from core.tz import IST
PRIORITIES = ["🔥 Hot", "🌤️ Warm", "❄️ Cold"]
DEFAULT_PRIORITY = "🌤️ Warm"


def now_ist():
    from core.tz import now_ist as _now
    return _now()


def can(role, module, action="view"):
    """RI-Ops permissions are per user (role default + Admin ▸ User Permissions
    override), so check the signed-in user."""
    from core.permissions import has_permission
    return bool(has_permission(st.session_state.get("username"), role, module, action))


def now_str():
    return now_ist().strftime("%Y-%m-%d %H:%M:%S")


def phone_key(phone):
    """Last 10 digits — '+91 98765-43210', '098765 43210' and '9876543210' match."""
    d = re.sub(r"\D", "", str(phone or ""))
    return d[-10:] if len(d) >= 10 else d


# ── People ────────────────────────────────────────────────────────────────────
def _logins():
    from core.permissions import get_all_users
    try:
        return get_all_users() or {}
    except Exception:
        return {}


def crm_users(include_admin=True):
    """username -> display name for every login that can work leads."""
    from core.permissions import has_permission
    users = {}
    for u, v in _logins().items():
        role = v.get("role")
        if role and (role != "admin" or include_admin) and has_permission(u, role, "leads", "view"):
            users[u] = v.get("name") or u
    return users


def salespeople():
    """Round-robin pool: everyone who can work leads except Admin."""
    return crm_users(include_admin=False)


def all_names():
    """username -> name for every login, so old owners still display."""
    return {u: v.get("name") or u for u, v in _logins().items()}


def owner_name(username):
    if not username or (isinstance(username, float) and pd.isna(username)):
        return "— Unassigned"
    return all_names().get(username, username)


# ── Auto-assignment ───────────────────────────────────────────────────────────
def next_owner():
    """Who gets the next auto-assigned lead: the fixed person from settings, or
    round-robin over salespeople (pointer kept in crm_settings)."""
    pool = sorted(salespeople())
    mode = get_crm_setting("assign_mode") or "round_robin"
    if mode != "round_robin":
        return mode if mode in crm_users() else (pool[0] if pool else None)
    if not pool:
        return None
    last = get_crm_setting("rr_last")
    nxt = pool[(pool.index(last) + 1) % len(pool)] if last in pool else pool[0]
    set_crm_setting("rr_last", nxt)
    return nxt


def new_lead_row(*, lead_date, source, owner, contact_name=None, phone=None, company=None,
                 product=None, city=None, email=None, discussion=None, remarks=None,
                 status="Fresh", priority=DEFAULT_PRIORITY, external_id=None, created_by=""):
    """A complete `leads` insert. First follow-up is due the day after the lead
    comes in (Varun's rule), unless it's already closed."""
    open_ = status in ("Fresh", "Negotiation")
    ld = pd.Timestamp(lead_date).date()
    return {
        "lead_date": str(ld), "source": source, "contact_name": contact_name or None,
        "phone": phone or None, "company": company or None, "product": product or None,
        "city": city or None, "email": email or None, "discussion": discussion or None,
        "remarks": remarks or None, "status": status, "priority": priority,
        "owner": owner, "assigned_to": owner_name(owner) if owner else None,
        "follow_up_date": str(ld + timedelta(days=1)) if open_ else None,
        "external_id": external_id, "created_by": created_by,
    }


# ── WhatsApp ──────────────────────────────────────────────────────────────────
WA_TEMPLATES = {
    "👋 Intro": "Namaste {name}, this is {me} from Rameshwaram Industries. Thank you for your enquiry{for_product}. "
               "When would be a good time to talk?",
    "🔁 Follow-up": "Hello {name}, {me} from Rameshwaram Industries here — following up on your requirement{for_product}. "
                   "Shall I send a quotation or arrange a site visit?",
    "🧾 Quote sent": "Hello {name}, we have shared our quotation{for_product}. "
                    "Happy to answer any questions — {me}, Rameshwaram Industries.",
    "🙏 Thank you": "Thank you {name} for choosing Rameshwaram Industries! We'll keep you posted on dispatch. — {me}",
}


def wa_number(phone):
    d = re.sub(r"\D", "", str(phone or ""))
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10:
        d = "91" + d
    return d if len(d) >= 11 else ""


def wa_link(phone, template, lead, me):
    num = wa_number(phone)
    if not num:
        return None
    prod = lead.get("product")
    msg = WA_TEMPLATES[template].format(
        name=(lead.get("contact_name") or "").strip() or "Sir/Madam", me=me,
        for_product=f" for {prod}" if prod else "")
    return f"https://wa.me/{num}?text={quote(msg)}"


# ── IndiaMART Lead Manager pull ───────────────────────────────────────────────
INDIAMART_URL = "https://mapi.indiamart.com/wservce/crm/crmListing/v2/"
_IM_EVERY = timedelta(minutes=10)      # IndiaMART rejects calls closer than 5 min apart
_IM_MAX_WINDOW = timedelta(days=7)     # API returns at most 7 days per call


def indiamart_key():
    try:
        return st.secrets.get("INDIAMART_CRM_KEY") or ""
    except Exception:
        return ""


def _im_time(dt):
    return dt.strftime("%d-%b-%Y %H:%M:%S")


def sync_indiamart(force=False, created_by="IndiaMART sync"):
    """Pull new IndiaMART enquiries into leads. Returns (added, message)."""
    key = indiamart_key()
    if not key:
        return 0, "IndiaMART not connected (no INDIAMART_CRM_KEY in secrets)."
    now = now_ist().replace(tzinfo=None)
    last_call = get_crm_setting("im_last_call")
    if not force and last_call and now - datetime.fromisoformat(last_call) < _IM_EVERY:
        return 0, "Synced recently."
    last_end = get_crm_setting("im_last_end")
    start = datetime.fromisoformat(last_end) if last_end else now - timedelta(days=1)
    start = max(start, now - _IM_MAX_WINDOW)
    set_crm_setting("im_last_call", now.isoformat(timespec="seconds"))
    try:
        r = requests.get(INDIAMART_URL, params={"glusr_crm_key": key, "start_time": _im_time(start),
                                                "end_time": _im_time(now)}, timeout=20)
        data = r.json()
    except Exception as e:
        return 0, f"IndiaMART sync failed: {e}"
    code = int(data.get("CODE", 0) or 0)
    if code == 204:          # no new leads in the window
        set_crm_setting("im_last_end", now.isoformat(timespec="seconds"))
        return 0, "No new IndiaMART leads."
    if code != 200:
        return 0, f"IndiaMART: {data.get('MESSAGE') or data.get('STATUS') or 'error'} (code {code})"

    existing = get_leads()
    seen_ids = set(existing["external_id"].dropna()) if "external_id" in existing else set()
    seen_phones = set(existing["phone"].map(phone_key)) if not existing.empty else set()
    added = 0
    for q in data.get("RESPONSE") or []:
        ext = f"IM-{q.get('UNIQUE_QUERY_ID')}"
        if ext in seen_ids:
            continue
        msg = " · ".join(x for x in [q.get("QUERY_PRODUCT_NAME"), q.get("QUERY_MESSAGE")] if x)
        phone = q.get("SENDER_MOBILE") or q.get("SENDER_PHONE") or ""
        note = "Repeat enquiry — this number is already in Leads." if phone_key(phone) in seen_phones else None
        when = pd.to_datetime(q.get("QUERY_TIME"), errors="coerce")
        insert_sales_row("leads", new_lead_row(
            lead_date=(when if pd.notna(when) else now).date(), source="IndiaMART", owner=next_owner(),
            contact_name=q.get("SENDER_NAME"), phone=phone, company=q.get("SENDER_COMPANY"),
            city=", ".join(x for x in [q.get("SENDER_CITY"), q.get("SENDER_STATE")] if x) or None,
            email=q.get("SENDER_EMAIL"), discussion=re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", msg)).strip()[:2000] or None,
            remarks=note, external_id=ext, created_by=created_by))
        seen_ids.add(ext)
        seen_phones.add(phone_key(phone))
        added += 1
    set_crm_setting("im_last_end", now.isoformat(timespec="seconds"))
    return added, f"{added} new IndiaMART lead{'s' if added != 1 else ''} added."


# ── Spreadsheet import (IndiaMART / Meta / any export) ────────────────────────
IMPORT_FIELDS = {  # lead field -> header words that usually mean it
    "contact_name": ["sender name", "full_name", "full name", "name", "contact", "customer"],
    "phone": ["mobile", "phone_number", "phone", "contact number", "whatsapp"],
    "company": ["company", "firm", "organisation", "organization", "business"],
    "city": ["city", "location", "address", "area"],
    "email": ["email", "e-mail"],
    "product": ["product", "item", "requirement"],
    "discussion": ["message", "query", "enquiry", "comments", "remarks", "details"],
    "lead_date": ["date", "created_time", "query time", "time"],
}


def guess_column(columns, field):
    cols = [str(c) for c in columns]
    for word in IMPORT_FIELDS[field]:
        for c in cols:
            if c.strip().lower() == word:
                return c
    for word in IMPORT_FIELDS[field]:
        for c in cols:
            if word in c.strip().lower():
                return c
    return None
