"""
Per-user permission overrides on top of role-based defaults.

Access has always been role-based — a fixed set of "role in (...)" checks
scattered across app.py (page nav) and each views/*.py file (add/edit/delete
gates). ROLE_DEFAULTS below is that same rule set, just consolidated in one
place. A row in the user_permissions table, if present for
(username, module), fully overrides that module's role default for that one
user; with no row, the role default applies unchanged — so every existing
user behaves exactly as before until an admin explicitly grants an override
via Admin > User Permissions.
"""
from core.db import get_user_permissions as _get_user_permissions_rows


def get_all_users():
    """{username: {"password":..., "role":..., "name":..., "plant":...}} —
    same source app.py's login screen reads (Streamlit secrets' [users]
    section, falling back to the empty core.config.USERS if secrets aren't
    set). "plant" is optional — set it (Pipe Factory / Pole Factory) on a
    production/factory/dispatch user's secrets.toml entry to lock their
    login to that one plant only (no picker, no other plant's data visible);
    leave it unset — as admin/viewer/headoffice always are — to see both
    plants, same as before."""
    import streamlit as st
    from core.config import USERS as _users_default
    try:
        sec = st.secrets.get("users", {})
        if sec:
            return {
                k: {"password": v["password"], "role": v["role"], "name": v["name"], "plant": v.get("plant")}
                for k, v in sec.items()
            }
    except Exception:
        pass
    return _users_default

MODULES = ["dpr", "quotation", "orders", "dispatch", "inventory", "gate_entry", "liability", "admin"]

MODULE_LABELS = {
    "dpr":        "DPR Entry",
    "quotation":  "Quotation",
    "orders":     "Sales Orders",
    "dispatch":   "Dispatch Entry",
    "inventory":  "Inventory",
    "gate_entry": "Gate Entry",
    "liability":  "Liability",
    "admin":      "Admin",
}

ACTIONS = ["view", "add", "edit", "delete"]

_ALL_FALSE = {"view": False, "add": False, "edit": False, "delete": False}
_ALL_TRUE  = {"view": True,  "add": True,  "edit": True,  "delete": True}
_VIEW_ONLY = {"view": True,  "add": False, "edit": False, "delete": False}
_VIEW_ADD  = {"view": True,  "add": True,  "edit": False, "delete": False}

# One row per role, mirroring the access rules already hardcoded in app.py's
# sidebar nav (view) and each view file's inline role checks (add/edit/delete).
ROLE_DEFAULTS = {
    "admin": {m: dict(_ALL_TRUE) for m in MODULES},

    "viewer": {m: dict(_VIEW_ONLY) for m in MODULES if m != "liability"} | {"liability": dict(_ALL_FALSE)},

    "production": {
        "dpr":        dict(_VIEW_ADD),
        "quotation":  dict(_ALL_FALSE),
        "orders":     dict(_ALL_FALSE),
        "dispatch":   dict(_ALL_FALSE),
        "inventory":  dict(_ALL_FALSE),
        "gate_entry": dict(_ALL_FALSE),
        "liability":  dict(_ALL_FALSE),
        "admin":      dict(_ALL_FALSE),
    },

    "factory": {
        "dpr":        dict(_VIEW_ADD),
        "quotation":  dict(_ALL_FALSE),
        "orders":     dict(_ALL_FALSE),
        "dispatch":   dict(_VIEW_ADD),
        "inventory":  dict(_VIEW_ONLY),
        "gate_entry": dict(_VIEW_ADD),
        "liability":  dict(_ALL_FALSE),
        "admin":      dict(_ALL_FALSE),
    },

    "dispatch": {
        "dpr":        dict(_ALL_FALSE),
        "quotation":  dict(_ALL_FALSE),
        "orders":     dict(_ALL_FALSE),
        "dispatch":   dict(_VIEW_ADD),
        "inventory":  dict(_VIEW_ONLY),
        "gate_entry": dict(_VIEW_ADD),
        "liability":  dict(_ALL_FALSE),
        "admin":      dict(_ALL_FALSE),
    },

    "headoffice": {
        "dpr":        dict(_ALL_FALSE),
        "quotation":  dict(_VIEW_ADD),
        "orders":     dict(_VIEW_ADD),
        "dispatch":   dict(_VIEW_ONLY),
        "inventory":  dict(_ALL_FALSE),
        "gate_entry": dict(_ALL_FALSE),
        "liability":  dict(_ALL_FALSE),
        "admin":      dict(_ALL_FALSE),
    },
}


def _overrides():
    """{(username, module): {view,add,edit,delete}} sourced from the
    user_permissions table (cached at the db layer)."""
    rows = _get_user_permissions_rows()
    out = {}
    if rows is None or rows.empty:
        return out
    for _, r in rows.iterrows():
        out[(r["username"], r["module"])] = {
            "view":   bool(r["can_view"]),
            "add":    bool(r["can_add"]),
            "edit":   bool(r["can_edit"]),
            "delete": bool(r["can_delete"]),
        }
    return out


def get_effective_permissions(username, role, module):
    """Full {view,add,edit,delete} dict for this user+module — the override
    row if one exists, else the role default."""
    override = _overrides().get((username, module))
    if override is not None:
        return override
    return dict(ROLE_DEFAULTS.get(role, {}).get(module, _ALL_FALSE))


def has_permission(username, role, module, action):
    return get_effective_permissions(username, role, module).get(action, False)
